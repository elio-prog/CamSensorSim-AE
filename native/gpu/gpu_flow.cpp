// Mesure du mouvement (DIS) et obturateur deroulant sur la carte graphique : memes etapes que
// native/core/shutter.cpp (dis_flow, rolling_shutter), noyaux de native/gpu/kernels.cu.
#include "gpu_flow.h"

#include <algorithm>
#include <cmath>
#include <cstring>

#include "../core/shutter.h"
#include "../core/util.h"
#include "gpu.h"

namespace camsim {
namespace gpu {

namespace {

struct Plane {
    DevPtr p = 0;
    int w = 0, h = 0;
};

// Rang k (0 = plus petit) parmi n cles de 64 bits, par histogrammes de 16 bits successifs.
bool select_rank(Session& S, DevPtr keys, int n, long long k, unsigned long long* out) {
    DevPtr hist = S.alloc(65536 * sizeof(unsigned int));
    std::vector<unsigned int> hh(65536);
    unsigned long long prefix = 0, mask = 0;
    for (int shift = 48; shift >= 0; shift -= 16) {
        S.zero(hist, 65536 * sizeof(unsigned int));
        void* a[] = {&keys, &n, &shift, &mask, &prefix, &hist};
        S.launch("k_hist16", n, a);
        if (!S.download(hh.data(), hist, hh.size() * sizeof(unsigned int))) return false;
        long long cum = 0;
        int bin = 0;
        for (; bin < 65536; ++bin) {
            if (cum + hh[bin] > k) break;
            cum += hh[bin];
        }
        if (bin == 65536) return false;
        k -= cum;
        prefix |= (unsigned long long)bin << shift;
        mask |= 0xFFFFULL << shift;
    }
    *out = prefix;
    return true;
}

// Luminance sur 8 bits (shutter.to_gray8) d'une image RVB deja sur la carte.
bool gray8(Session& S, DevPtr rgb, int w, int h, Plane& g) {
    const int n = w * h;
    DevPtr lum = S.alloc((size_t)n * sizeof(double)), keys = S.alloc((size_t)n * sizeof(unsigned long long));
    { void* a[] = {&rgb, &lum, &keys, (void*)&n}; S.launch("k_lum", n, a); }
    const double pos = 0.995 * (double)(n - 1);
    const long long lo = (long long)std::floor(pos), hi = std::min(lo + 1, (long long)n - 1);
    unsigned long long ka = 0, kb = 0;
    if (!select_rank(S, keys, n, lo, &ka)) return false;
    if (!select_rank(S, keys, n, hi, &kb)) return false;
    double a, b;
    std::memcpy(&a, &ka, 8);
    std::memcpy(&b, &kb, 8);
    double scale = a + (b - a) * (pos - lo);
    if (scale == 0) scale = 1.0;
    g = {S.alloc((size_t)n * sizeof(float)), w, h};
    void* args[] = {&lum, &g.p, (void*)&n, &scale};
    return S.launch("k_gray", n, args);
}

// Redimensionnement separable avec les tables de cv::resize.
bool resize(Session& S, const Plane& src, Plane& dst, int nw, int nh, bool area, bool round8) {
    const Taps1D tx = area ? taps_area(src.w, nw) : taps_linear(src.w, nw);
    const Taps1D ty = area ? taps_area(src.h, nh) : taps_linear(src.h, nh);
    auto up = [&](const void* d, size_t bytes) {
        DevPtr p = S.alloc(bytes);
        S.upload(p, d, bytes);
        return p;
    };
    DevPtr xs = up(tx.start.data(), tx.start.size() * sizeof(int)), xi = up(tx.src.data(), tx.src.size() * sizeof(int)),
           xw = up(tx.w.data(), tx.w.size() * sizeof(double));
    DevPtr ys = up(ty.start.data(), ty.start.size() * sizeof(int)), yi = up(ty.src.data(), ty.src.size() * sizeof(int)),
           yw = up(ty.w.data(), ty.w.size() * sizeof(double));
    DevPtr tmp = S.alloc((size_t)src.h * nw * sizeof(double));
    dst = {S.alloc((size_t)nw * nh * sizeof(float)), nw, nh};
    int w = src.w, h = src.h, r8 = round8 ? 1 : 0;
    { void* a[] = {(void*)&src.p, &tmp, &w, &h, &nw, &xs, &xi, &xw}; S.launch("k_resize_h", (size_t)h * nw, a); }
    { void* a[] = {&tmp, &dst.p, &nw, &nh, &ys, &yi, &yw, &r8}; S.launch("k_resize_v", (size_t)nh * nw, a); }
    return S.ok();
}

void refine(Session& S, const Plane& A, const Plane& B, Plane& U, Plane& V, const FlowTuning& T) {
    const int w = A.w, h = A.h, n = w * h;
    const size_t fb = (size_t)n * sizeof(float);
    auto plane = [&]() { return S.alloc(fb); };
    DevPtr I0 = plane(), I1 = plane();
    double norm = T.refine_norm;
    { void* a[] = {(void*)&A.p, &I0, (void*)&n, &norm}; S.launch("k_scale_plane", n, a); }
    { void* a[] = {(void*)&B.p, &I1, (void*)&n, &norm}; S.launch("k_scale_plane", n, a); }
    int ww = w, hh = h;
    auto deriv = [&](DevPtr src, DevPtr& gx, DevPtr& gy) {
        gx = plane();
        gy = plane();
        void* a[] = {&src, &gx, &gy, &ww, &hh};
        S.launch("k_deriv5", n, a);
    };
    DevPtr I0x, I0y, I1x, I1y;
    deriv(I0, I0x, I0y);
    deriv(I1, I1x, I1y);
    DevPtr W = plane(), Wx = plane(), Wy = plane();
    { void* a[] = {&I1, &I1x, &I1y, &U.p, &V.p, &W, &Wx, &Wy, &ww, &hh}; S.launch("k_warp3", n, a); }
    DevPtr Wxx, Wxy, Wyx, Wyy, I0xx, I0xy, I0yx, I0yy;
    deriv(Wx, Wxx, Wxy);
    deriv(Wy, Wyx, Wyy);
    deriv(I0x, I0xx, I0xy);
    deriv(I0y, I0yx, I0yy);
    DevPtr Tm = S.alloc(fb * 8), Sm = S.alloc(fb * 6), du = plane(), dv = plane();
    int nn = n;
    { void* a[] = {&W, &Wx, &Wy, &Wxx, &Wxy, &Wyx, &Wyy, &I0, &I0x, &I0y, &I0xx, &I0xy, &I0yx, &I0yy, &Tm, &nn}; S.launch("k_terms", n, a); }
    S.zero(du, fb);
    S.zero(dv, fb);
    double alpha = T.alpha, delta = T.delta, gamma = T.gamma, e2 = T.eps * T.eps, omega = 1.6;
    const size_t sor_n = (size_t)h * ((w + 1) / 2);
    for (int fp = 0; fp < T.refine_iters; ++fp) {
        { void* a[] = {&Tm, &U.p, &V.p, &du, &dv, &Sm, &ww, &hh, &alpha, &delta, &gamma, &e2}; S.launch("k_weights", n, a); }
        for (int it = 0; it < 5; ++it)
            for (int color = 0; color < 2; ++color) {
                void* a[] = {&Sm, &U.p, &V.p, &du, &dv, &ww, &hh, &color, &omega};
                S.launch("k_sor", sor_n, a);
            }
    }
    { void* a[] = {&U.p, &V.p, &du, &dv, &nn}; S.launch("k_add2", n, a); }
}

// DIS sur deux images grises deja sur la carte ; flux (w x h x 2) ecrit dans flow.
bool dis(Session& S, const Plane& g0, const Plane& g1, int finest, DevPtr flow) {
    const FlowTuning& T = g_flow;
    const int w = g0.w, h = g0.h, psz = 8, pstr = 3;
    finest = std::max(0, finest);
    int coarsest = std::min((int)(std::log(std::max(w, h) / (4.0 * psz)) / std::log(2.0) + 0.5),
                            (int)(std::log(std::min(w, h) / (double)psz) / std::log(2.0)));
    coarsest = std::max(coarsest, finest);
    std::vector<Plane> P0(coarsest + 1), P1(coarsest + 1);
    P0[0] = g0;
    P1[0] = g1;
    for (int sc = 1; sc <= coarsest; ++sc) {
        const int sw = std::max(1, w >> sc), sh = std::max(1, h >> sc);
        if (!resize(S, P0[sc - 1], P0[sc], sw, sh, true, true) || !resize(S, P1[sc - 1], P1[sc], sw, sh, true, true)) return false;
    }
    Plane U, V;
    for (int sc = coarsest; sc >= finest; --sc) {
        const int sw = w >> sc, sh = h >> sc;
        if (sw < psz || sh < psz) continue;
        const Plane& A = P0[sc];
        const Plane& B = P1[sc];
        int n = sw * sh;
        if (!U.p) {
            U = {S.alloc((size_t)n * sizeof(float)), sw, sh};
            V = {S.alloc((size_t)n * sizeof(float)), sw, sh};
            S.zero(U.p, (size_t)n * sizeof(float));
            S.zero(V.p, (size_t)n * sizeof(float));
        } else {
            double kx = (double)sw / U.w, ky = (double)sh / U.h;
            Plane U2, V2;
            if (!resize(S, U, U2, sw, sh, false, false) || !resize(S, V, V2, sw, sh, false, false)) return false;
            U = U2;
            V = V2;
            void* a[] = {&U.p, &V.p, &n, &kx, &ky};
            S.launch("k_scale2", n, a);
        }
        DevPtr Ax = S.alloc((size_t)n * sizeof(float)), Ay = S.alloc((size_t)n * sizeof(float));
        int ww = sw, hh = sh;
        { void* a[] = {(void*)&A.p, &Ax, &Ay, &ww, &hh}; S.launch("k_sobel", n, a); }
        int ws = 1 + (sw - psz) / pstr, hs = 1 + (sh - psz) / pstr;
        int nb = ws * hs, p_sz = psz, p_st = pstr;
        DevPtr H = S.alloc((size_t)nb * 5 * sizeof(float)), Sx = S.alloc((size_t)nb * sizeof(float)), Sy = S.alloc((size_t)nb * sizeof(float));
        { void* a[] = {&Ax, &Ay, &ww, &ws, &hs, &p_sz, &p_st, &H}; S.launch("k_patch_hess", nb, a); }
        { void* a[] = {&U.p, &V.p, &U.w, &Sx, &Sy, &ws, &hs, &p_sz, &p_st}; S.launch("k_patch_init", nb, a); }
        int n_str = std::max(1, std::min(hs, 16));
        const int rows = (hs + n_str - 1) / n_str;
        // Au plus 8 groupes de 32 fils par bande (au-dela, la carte refuse le lancement : trop de registres) ;
        // chaque groupe traite alors plusieurs lignes de blocs, meme resultat.
        const unsigned threads = 32u * (unsigned)std::min(rows, 8);
        int iters = T.grad_iters;
        for (int pass = 0; pass < 2; ++pass) {
            void* a[] = {(void*)&A.p, (void*)&B.p, &Ax, &Ay, &H, &Sx, &Sy, &ww, &hh, &ws, &hs, &n_str, &pass, &iters};
            S.launch_grid("k_patch_pass", (unsigned)n_str, threads, a);
        }
        { void* a[] = {(void*)&A.p, (void*)&B.p, &Sx, &Sy, &U.p, &V.p, &ww, &hh, &ws, &hs, &p_sz, &p_st}; S.launch("k_densify", n, a); }
        if (T.refine_iters > 0) refine(S, A, B, U, V, T);
        if (!S.ok()) return false;
    }
    if (!U.p) return false;
    int ww = w, hh = h;
    double kx = (double)w / U.w, ky = (double)h / U.h;
    void* a[] = {&U.p, &V.p, &U.w, &U.h, &flow, &ww, &hh, &kx, &ky};
    S.launch("k_flow_up", (size_t)w * h, a);
    return S.ok();
}

}  // namespace

bool estimate_flow(const float* a, const float* b, int w, int h, int finest, std::vector<float>& flow) {
    if (!available()) return false;
    Session S;
    if (!S.ok()) return false;
    const size_t img = (size_t)w * h * 3 * sizeof(float);
    DevPtr da = S.alloc(img), db = S.alloc(img), df = S.alloc((size_t)w * h * 2 * sizeof(float));
    S.upload(da, a, img);
    S.upload(db, b, img);
    Plane g0, g1;
    if (!gray8(S, da, w, h, g0) || !gray8(S, db, w, h, g1)) return false;
    if (!dis(S, g0, g1, finest, df)) return false;
    flow.resize((size_t)w * h * 2);
    return S.download(flow.data(), df, flow.size() * sizeof(float));
}

bool rolling_shutter(const float* cur, const float* prev, const float* next, int w, int h, const std::vector<double>& tau,
                     int finest, std::vector<float>& out, std::vector<float>* flow_next) {
    if (!available() || (!prev && !next)) return false;
    Session S;
    if (!S.ok()) return false;
    const size_t img = (size_t)w * h * 3 * sizeof(float), fl = (size_t)w * h * 2 * sizeof(float);
    DevPtr dc = S.alloc(img), dout = S.alloc(img), dfn = S.alloc(fl), dfp = S.alloc(fl);
    S.upload(dc, cur, img);
    Plane gc;
    if (!gray8(S, dc, w, h, gc)) return false;               // l'image courante, une seule fois
    auto flow_to = [&](const float* other, DevPtr dst) {
        DevPtr d = S.alloc(img);
        S.upload(d, other, img);
        Plane g;
        return gray8(S, d, w, h, g) && dis(S, gc, g, finest, dst);
    };
    if (next && !flow_to(next, dfn)) return false;
    if (prev && !flow_to(prev, dfp)) return false;
    int n = w * h * 2;
    double m1 = -1.0, one = 1.0;
    // S'il manque un voisin (debut ou fin de plan), on suppose un mouvement regulier.
    if (!next) { void* a[] = {&dfp, &dfn, &n, &m1}; S.launch("k_scale_plane", n, a); }
    if (!prev) { void* a[] = {&dfn, &dfp, &n, &m1}; S.launch("k_scale_plane", n, a); }
    (void)one;
    DevPtr dtau = S.alloc((size_t)h * sizeof(double));
    S.upload(dtau, tau.data(), (size_t)h * sizeof(double));
    int ww = w, hh = h;
    { void* a[] = {&dc, &dfn, &dfp, &dtau, &dout, &ww, &hh}; S.launch("k_rolling", (size_t)w * h, a); }
    out.resize((size_t)w * h * 3);
    if (!S.download(out.data(), dout, img)) return false;
    if (flow_next && next) {
        flow_next->resize((size_t)w * h * 2);
        if (!S.download(flow_next->data(), dfn, fl)) return false;
    }
    return true;
}

bool resize_rgb(const float* src, int w, int h, int nw, int nh, int method, std::vector<float>& out) {
    if (!available()) return false;
    Taps1D tx, ty;
    resize_taps(method, w, h, nw, nh, tx, ty);
    Session S;
    if (!S.ok()) return false;
    auto up = [&](const void* d, size_t bytes) {
        DevPtr p = S.alloc(bytes);
        S.upload(p, d, bytes);
        return p;
    };
    int c = 3;
    DevPtr dsrc = up(src, (size_t)w * h * 3 * sizeof(float));
    DevPtr xs = up(tx.start.data(), tx.start.size() * sizeof(int)), xi = up(tx.src.data(), tx.src.size() * sizeof(int)),
           xw = up(tx.w.data(), tx.w.size() * sizeof(double));
    DevPtr ys = up(ty.start.data(), ty.start.size() * sizeof(int)), yi = up(ty.src.data(), ty.src.size() * sizeof(int)),
           yw = up(ty.w.data(), ty.w.size() * sizeof(double));
    DevPtr tmp = S.alloc((size_t)h * nw * 3 * sizeof(double)), dst = S.alloc((size_t)nw * nh * 3 * sizeof(float));
    { void* a[] = {&dsrc, &tmp, &w, &h, &nw, &c, &xs, &xi, &xw}; S.launch("k_resize_hc", (size_t)h * nw * 3, a); }
    { void* a[] = {&tmp, &dst, &nw, &nh, &c, &ys, &yi, &yw}; S.launch("k_resize_vc", (size_t)nh * nw * 3, a); }
    out.resize((size_t)nw * nh * 3);
    return S.download(out.data(), dst, out.size() * sizeof(float));
}

}  // namespace gpu
}  // namespace camsim
