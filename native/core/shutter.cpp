#include "shutter.h"

#include <algorithm>
#include <cmath>
#include <chrono>
#include <cstdio>
#include <cstdlib>

#include "util.h"
#include "log.h"
#include "../gpu/gpu_flow.h"

namespace camsim {

FlowTuning g_flow;

namespace {

constexpr double PI = 3.14159265358979323846;

inline int clampi(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }
inline int reflect101(int i, int n) {
    if (n == 1) return 0;
    while (i < 0 || i >= n) i = i < 0 ? -i : 2 * n - 2 - i;
    return i;
}

// Luminance sur 8 bits, comme shutter._to_gray8 (racine, echelle au centile 99,5).
std::vector<float> to_gray8(const float* rgb, int w, int h) {
    const long long n = (long long)w * h;
    std::vector<double> lum(n);
#pragma omp parallel for
    for (long long i = 0; i < n; ++i)
        lum[i] = std::max(((double)rgb[i * 3] + rgb[i * 3 + 1] + rgb[i * 3 + 2]) / 3.0, 0.0);
    std::vector<double> s(lum);
    const double pos = 0.995 * (double)(n - 1);
    const size_t lo = (size_t)std::floor(pos), hi = std::min(lo + 1, (size_t)n - 1);
    std::nth_element(s.begin(), s.begin() + lo, s.end());
    const double a = s[lo];
    const double b = (hi == lo) ? a : *std::min_element(s.begin() + lo + 1, s.end());
    double scale = a + (b - a) * (pos - lo);
    if (scale == 0) scale = 1.0;
    std::vector<float> g(n);
#pragma omp parallel for
    for (long long i = 0; i < n; ++i)
        g[i] = (float)std::floor(std::sqrt(std::clamp(lum[i] / std::max(scale, 1e-6), 0.0, 1.0)) * 255.0 + 0.5);
    return g;
}

// ------------------------------------------------------------------ DIS (Kroeger et al., ECCV 2016)
// Meme methode et memes reglages que cv2.DISOpticalFlow (preset MEDIUM) du moteur Python : recherche
// inverse de petits blocs sur une pyramide, densification, puis affinage variationnel.

struct Plane {
    int w = 0, h = 0;
    std::vector<float> p;
    float at(int x, int y) const { return p[(size_t)clampi(y, 0, h - 1) * w + clampi(x, 0, w - 1)]; }
    float bil(float x, float y) const {           // lecture bilineaire, bords prolonges
        x = std::clamp(x, 0.0f, (float)(w - 1));
        y = std::clamp(y, 0.0f, (float)(h - 1));
        const int x0 = std::min((int)x, std::max(w - 2, 0)), y0 = std::min((int)y, std::max(h - 2, 0));
        const int x1 = std::min(x0 + 1, w - 1), y1 = std::min(y0 + 1, h - 1);
        const float ax = x - x0, ay = y - y0;
        const float* r0 = &p[(size_t)y0 * w];
        const float* r1 = &p[(size_t)y1 * w];
        return (1 - ay) * ((1 - ax) * r0[x0] + ax * r0[x1]) + ay * ((1 - ax) * r1[x0] + ax * r1[x1]);
    }
};

Plane resize_plane(const Plane& a, int nw, int nh, bool area, bool round8) {
    std::vector<double> d(a.p.begin(), a.p.end());
    d = area ? resize_cv_area(d, a.w, a.h, 1, nw, nh) : resize_linear(d, a.w, a.h, 1, nw, nh);
    Plane o;
    o.w = nw;
    o.h = nh;
    o.p.resize(d.size());
    for (size_t i = 0; i < d.size(); ++i) o.p[i] = round8 ? (float)std::clamp(std::nearbyint(d[i]), 0.0, 255.0) : (float)d[i];
    return o;
}

// Gradient de Sobel 3 x 3 (cv2.spatialGradient, bords en miroir sans repetition).
void sobel(const Plane& a, Plane& gx, Plane& gy) {
    gx.w = gy.w = a.w;
    gx.h = gy.h = a.h;
    gx.p.assign(a.p.size(), 0.0f);
    gy.p.assign(a.p.size(), 0.0f);
#pragma omp parallel for
    for (int y = 0; y < a.h; ++y) {
        const int ym = reflect101(y - 1, a.h), yp = reflect101(y + 1, a.h);
        for (int x = 0; x < a.w; ++x) {
            const int xm = reflect101(x - 1, a.w), xp = reflect101(x + 1, a.w);
            auto I = [&](int xx, int yy) { return a.p[(size_t)yy * a.w + xx]; };
            gx.p[(size_t)y * a.w + x] = (I(xp, ym) - I(xm, ym)) + 2 * (I(xp, y) - I(xm, y)) + (I(xp, yp) - I(xm, yp));
            gy.p[(size_t)y * a.w + x] = (I(xm, yp) - I(xm, ym)) + 2 * (I(x, yp) - I(x, ym)) + (I(xp, yp) - I(xp, ym));
        }
    }
}

struct PatchSearch {
    const Plane &I0, &I1, &Ix, &Iy;
    int psz, pstr, ws, hs, iters;
    bool mean_norm;
    std::vector<float> Sx, Sy;
    std::vector<float> Hxx, Hyy, Hxy, Gx, Gy;     // par bloc : hessien (normalise) et sommes des gradients

    PatchSearch(const Plane& i0, const Plane& i1, const Plane& ix, const Plane& iy, int ps, int st, int it, bool mn)
        : I0(i0), I1(i1), Ix(ix), Iy(iy), psz(ps), pstr(st), iters(it), mean_norm(mn) {
        ws = 1 + (I0.w - psz) / pstr;
        hs = 1 + (I0.h - psz) / pstr;
        const size_t n = (size_t)ws * hs;
        Sx.assign(n, 0.0f);
        Sy.assign(n, 0.0f);
        Hxx.resize(n); Hyy.resize(n); Hxy.resize(n); Gx.resize(n); Gy.resize(n);
        const float np = (float)(psz * psz);
#pragma omp parallel for
        for (int is = 0; is < hs; ++is)
            for (int js = 0; js < ws; ++js) {
                double xx = 0, yy = 0, xy = 0, sx = 0, sy = 0;
                for (int r = 0; r < psz; ++r)
                    for (int c = 0; c < psz; ++c) {
                        const size_t k = (size_t)(is * pstr + r) * I0.w + js * pstr + c;
                        const double gx = Ix.p[k], gy = Iy.p[k];
                        xx += gx * gx; yy += gy * gy; xy += gx * gy; sx += gx; sy += gy;
                    }
                const size_t b = (size_t)is * ws + js;
                if (mean_norm) { xx -= sx * sx / np; yy -= sy * sy / np; xy -= sx * sy / np; }
                Hxx[b] = (float)xx; Hyy[b] = (float)yy; Hxy[b] = (float)xy; Gx[b] = (float)sx; Gy[b] = (float)sy;
            }
    }

    // Ecart entre le bloc de I0 et le bloc de I1 deplace de (ux, uy) (moyenne retiree).
    float cost(int is, int js, float ux, float uy) const {
        const int i = is * pstr, j = js * pstr;
        double s = 0, s2 = 0;
        for (int r = 0; r < psz; ++r)
            for (int c = 0; c < psz; ++c) {
                const double d = I1.bil(j + c + ux, i + r + uy) - I0.p[(size_t)(i + r) * I0.w + j + c];
                s += d; s2 += d * d;
            }
        return (float)(mean_norm ? s2 - s * s / (psz * psz) : s2);
    }

    // Descente de gradient compositionnelle inverse sur un bloc.
    void descend(int is, int js, int n_iter) {
        const size_t b = (size_t)is * ws + js;
        const int i = is * pstr, j = js * pstr;
        float ux = Sx[b], uy = Sy[b];
        const float ux0 = ux, uy0 = uy;
        double det = (double)Hxx[b] * Hyy[b] - (double)Hxy[b] * Hxy[b];
        if (std::fabs(det) < 1e-6) det += 1e-6;
        float best = cost(is, js, ux, uy), bx = ux, by = uy;
        const double np = psz * psz;
        for (int t = 0; t < n_iter; ++t) {
            double bxs = 0, bys = 0, sd = 0;
            for (int r = 0; r < psz; ++r)
                for (int c = 0; c < psz; ++c) {
                    const size_t k = (size_t)(i + r) * I0.w + j + c;
                    const double d = I1.bil(j + c + ux, i + r + uy) - I0.p[k];
                    bxs += d * Ix.p[k]; bys += d * Iy.p[k]; sd += d;
                }
            if (mean_norm) { bxs -= sd * Gx[b] / np; bys -= sd * Gy[b] / np; }
            const double dx = (Hyy[b] * bxs - Hxy[b] * bys) / det, dy = (Hxx[b] * bys - Hxy[b] * bxs) / det;
            ux -= (float)dx;
            uy -= (float)dy;
            const float cst = cost(is, js, ux, uy);
            if (cst < best) { best = cst; bx = ux; by = uy; }
            if (std::fabs(dx) < 0.01 && std::fabs(dy) < 0.01) break;
        }
        ux = bx; uy = by;
        if ((ux - ux0) * (ux - ux0) + (uy - uy0) * (uy - uy0) > (float)(psz * psz)) { ux = ux0; uy = uy0; }
        Sx[b] = ux;
        Sy[b] = uy;
    }

    void run(const Plane& U, const Plane& V) {
        for (int is = 0; is < hs; ++is)
            for (int js = 0; js < ws; ++js) {
                const size_t b = (size_t)is * ws + js;
                Sx[b] = U.p[(size_t)(is * pstr + psz / 2) * U.w + js * pstr + psz / 2];
                Sy[b] = V.p[(size_t)(is * pstr + psz / 2) * V.w + js * pstr + psz / 2];
            }
        // Bandes de lignes de blocs independantes (calcul parallele), propagation dans chaque bande.
        const int n_str = std::max(1, std::min(hs, 16));
        const int it1 = iters / 2, it2 = iters - it1;
        for (int pass = 0; pass < 2; ++pass) {
#pragma omp parallel for schedule(dynamic)
            for (int st = 0; st < n_str; ++st) {
                const int r0 = st * hs / n_str, r1 = (st + 1) * hs / n_str;
                for (int k = 0; k < r1 - r0; ++k) {
                    const int is = pass == 0 ? r0 + k : r1 - 1 - k;
                    for (int m = 0; m < ws; ++m) {
                        const int js = pass == 0 ? m : ws - 1 - m;
                        const size_t b = (size_t)is * ws + js;
                        float best = cost(is, js, Sx[b], Sy[b]);
                        const int nj = pass == 0 ? js - 1 : js + 1, ni = pass == 0 ? is - 1 : is + 1;
                        if (nj >= 0 && nj < ws) {
                            const size_t q = (size_t)is * ws + nj;
                            const float c = cost(is, js, Sx[q], Sy[q]);
                            if (c < best) { best = c; Sx[b] = Sx[q]; Sy[b] = Sy[q]; }
                        }
                        if (ni >= r0 && ni < r1) {
                            const size_t q = (size_t)ni * ws + js;
                            const float c = cost(is, js, Sx[q], Sy[q]);
                            if (c < best) { best = c; Sx[b] = Sx[q]; Sy[b] = Sy[q]; }
                        }
                        descend(is, js, pass == 0 ? it1 : it2);
                    }
                }
            }
        }
    }

    // Densification : chaque pixel recoit la moyenne des blocs qui le couvrent, ponderee par leur accord.
    void densify(Plane& U, Plane& V) const {
        const int w = I0.w, h = I0.h;
#pragma omp parallel for
        for (int y = 0; y < h; ++y)
            for (int x = 0; x < w; ++x) {
                int i_lo = (y - psz + 1 + pstr - 1) / pstr, i_hi = y / pstr;
                int j_lo = (x - psz + 1 + pstr - 1) / pstr, j_hi = x / pstr;
                if (y - psz + 1 < 0) i_lo = 0;
                if (x - psz + 1 < 0) j_lo = 0;
                i_hi = std::min(i_hi, hs - 1); j_hi = std::min(j_hi, ws - 1);
                i_lo = std::min(i_lo, i_hi); j_lo = std::min(j_lo, j_hi);
                const float v0 = I0.p[(size_t)y * w + x];
                double su = 0, sv = 0, sw = 0;
                for (int is = i_lo; is <= i_hi; ++is)
                    for (int js = j_lo; js <= j_hi; ++js) {
                        const size_t b = (size_t)is * ws + js;
                        const double d = I1.bil(x + Sx[b], y + Sy[b]) - v0;
                        const double wt = 1.0 / std::max(1.0, std::fabs(d));
                        su += wt * Sx[b]; sv += wt * Sy[b]; sw += wt;
                    }
                U.p[(size_t)y * w + x] = (float)(su / sw);
                V.p[(size_t)y * w + x] = (float)(sv / sw);
            }
    }
};

// Derivee a 5 points (1, -8, 0, 8, -1) / 12, bords prolonges.
void deriv5(const Plane& a, Plane& gx, Plane& gy) {
    gx.w = gy.w = a.w; gx.h = gy.h = a.h;
    gx.p.resize(a.p.size()); gy.p.resize(a.p.size());
#pragma omp parallel for
    for (int y = 0; y < a.h; ++y)
        for (int x = 0; x < a.w; ++x) {
            gx.p[(size_t)y * a.w + x] = (a.at(x - 2, y) - 8 * a.at(x - 1, y) + 8 * a.at(x + 1, y) - a.at(x + 2, y)) / 12.0f;
            gy.p[(size_t)y * a.w + x] = (a.at(x, y - 2) - 8 * a.at(x, y - 1) + 8 * a.at(x, y + 1) - a.at(x, y + 2)) / 12.0f;
        }
}

// Affinage variationnel (Brox et al. 2004, comme cv2.VariationalRefinement) : constance de la couleur et du
// gradient, lissage, penalites robustes ; points fixes et SOR rouge-noir.
void refine(const Plane& I0in, const Plane& I1in, Plane& U, Plane& V, int fixed_iters, int sor_iters, double alpha,
            double delta, double gamma, double eps, double omega, double norm) {
    const int w = I0in.w, h = I0in.h;
    const size_t N = (size_t)w * h;
    Plane I0 = I0in, I1 = I1in;
    for (float& v : I0.p) v = (float)(v * norm);
    for (float& v : I1.p) v = (float)(v * norm);
    Plane I0x, I0y, I1x, I1y;
    deriv5(I0, I0x, I0y);
    deriv5(I1, I1x, I1y);
    // Images de I1 (et derivees) deplacees par le flux courant.
    Plane W, Wx, Wy;
    W.w = Wx.w = Wy.w = w; W.h = Wx.h = Wy.h = h;
    W.p.resize(N); Wx.p.resize(N); Wy.p.resize(N);
#pragma omp parallel for
    for (int y = 0; y < h; ++y)
        for (int x = 0; x < w; ++x) {
            const size_t i = (size_t)y * w + x;
            const float sx = x + U.p[i], sy = y + V.p[i];
            W.p[i] = I1.bil(sx, sy); Wx.p[i] = I1x.bil(sx, sy); Wy.p[i] = I1y.bil(sx, sy);
        }
    Plane Wxx, Wxy, Wyx, Wyy, I0xx, I0xy, I0yx, I0yy;
    deriv5(Wx, Wxx, Wxy);
    deriv5(Wy, Wyx, Wyy);
    deriv5(I0x, I0xx, I0xy);
    deriv5(I0y, I0yx, I0yy);
    std::vector<float> Ix(N), Iy(N), Iz(N), Ixx(N), Ixy(N), Iyy(N), Ixz(N), Iyz(N);
    for (size_t i = 0; i < N; ++i) {
        Ix[i] = 0.5f * (Wx.p[i] + I0x.p[i]);
        Iy[i] = 0.5f * (Wy.p[i] + I0y.p[i]);
        Iz[i] = W.p[i] - I0.p[i];
        Ixx[i] = 0.5f * (Wxx.p[i] + I0xx.p[i]);
        Ixy[i] = 0.25f * (Wxy.p[i] + I0xy.p[i] + Wyx.p[i] + I0yx.p[i]);
        Iyy[i] = 0.5f * (Wyy.p[i] + I0yy.p[i]);
        Ixz[i] = Wx.p[i] - I0x.p[i];
        Iyz[i] = Wy.p[i] - I0y.p[i];
    }
    std::vector<float> du(N, 0.0f), dv(N, 0.0f), wd(N), wg(N), ws(N);
    std::vector<float> A11(N), A12(N), A22(N), b1(N), b2(N);
    const double e2 = eps * eps;
    for (int fp = 0; fp < fixed_iters; ++fp) {
#pragma omp parallel for
        for (int y = 0; y < h; ++y)
            for (int x = 0; x < w; ++x) {
                const size_t i = (size_t)y * w + x;
                const double rd = Iz[i] + Ix[i] * du[i] + Iy[i] * dv[i];
                const double rgx = Ixz[i] + Ixx[i] * du[i] + Ixy[i] * dv[i];
                const double rgy = Iyz[i] + Ixy[i] * du[i] + Iyy[i] * dv[i];
                wd[i] = (float)(delta / (2 * std::sqrt(rd * rd + e2)));
                wg[i] = (float)(gamma / (2 * std::sqrt(rgx * rgx + rgy * rgy + e2)));
                // Lissage sur le flux complet (differences avant).
                auto uu = [&](int xx, int yy) { const size_t k = (size_t)clampi(yy, 0, h - 1) * w + clampi(xx, 0, w - 1); return (double)U.p[k] + du[k]; };
                auto vv = [&](int xx, int yy) { const size_t k = (size_t)clampi(yy, 0, h - 1) * w + clampi(xx, 0, w - 1); return (double)V.p[k] + dv[k]; };
                const double ux = uu(x + 1, y) - uu(x, y), uy = uu(x, y + 1) - uu(x, y);
                const double vx = vv(x + 1, y) - vv(x, y), vy = vv(x, y + 1) - vv(x, y);
                ws[i] = (float)(alpha / (2 * std::sqrt(ux * ux + uy * uy + vx * vx + vy * vy + e2)));
                A11[i] = (float)(wd[i] * Ix[i] * Ix[i] + wg[i] * (Ixx[i] * Ixx[i] + Ixy[i] * Ixy[i]));
                A12[i] = (float)(wd[i] * Ix[i] * Iy[i] + wg[i] * (Ixx[i] * Ixy[i] + Ixy[i] * Iyy[i]));
                A22[i] = (float)(wd[i] * Iy[i] * Iy[i] + wg[i] * (Ixy[i] * Ixy[i] + Iyy[i] * Iyy[i]));
                b1[i] = (float)(-wd[i] * Ix[i] * Iz[i] - wg[i] * (Ixx[i] * Ixz[i] + Ixy[i] * Iyz[i]));
                b2[i] = (float)(-wd[i] * Iy[i] * Iz[i] - wg[i] * (Ixy[i] * Ixz[i] + Iyy[i] * Iyz[i]));
            }
        for (int it = 0; it < sor_iters; ++it)
            for (int color = 0; color < 2; ++color) {
#pragma omp parallel for
                for (int y = 0; y < h; ++y)
                    for (int x = (y + color) & 1; x < w; x += 2) {
                        const size_t i = (size_t)y * w + x;
                        double su = 0, sv = 0, sw = 0;
                        const int nb[4][2] = {{x - 1, y}, {x + 1, y}, {x, y - 1}, {x, y + 1}};
                        for (const auto& q : nb) {
                            if (q[0] < 0 || q[0] >= w || q[1] < 0 || q[1] >= h) continue;
                            const size_t k = (size_t)q[1] * w + q[0];
                            // poids de l'arete : celui du pixel d'ou part la difference avant
                            const double wt = (q[0] < x || q[1] < y) ? ws[k] : ws[i];
                            su += wt * ((double)U.p[k] + du[k] - U.p[i]);
                            sv += wt * ((double)V.p[k] + dv[k] - V.p[i]);
                            sw += wt;
                        }
                        const double a11 = A11[i] + sw, a22 = A22[i] + sw;
                        const double nu = (b1[i] + su - A12[i] * dv[i]) / a11;
                        du[i] = (float)((1 - omega) * du[i] + omega * nu);
                        const double nv = (b2[i] + sv - A12[i] * du[i]) / a22;
                        dv[i] = (float)((1 - omega) * dv[i] + omega * nv);
                    }
            }
    }
    for (size_t i = 0; i < N; ++i) { U.p[i] += du[i]; V.p[i] += dv[i]; }
}

std::vector<float> dis_flow(const std::vector<float>& g0, const std::vector<float>& g1, int w, int h, int finest) {
    const FlowTuning& TT = g_flow;
    const int psz = 8, pstr = 3;
    finest = std::max(0, finest);
    int coarsest = std::min((int)(std::log(std::max(w, h) / (4.0 * psz)) / std::log(2.0) + 0.5),
                            (int)(std::log(std::min(w, h) / (double)psz) / std::log(2.0)));
    coarsest = std::max(coarsest, finest);
    Plane I0{w, h, g0}, I1{w, h, g1};
    // Pyramide : echelle s = image divisee par 2^s (moyenne par surface, arrondie a l'entier comme l'image
    // 8 bits d'OpenCV), chaque echelle tiree de la precedente.
    std::vector<Plane> P0(coarsest + 1), P1(coarsest + 1);
    P0[0] = I0;
    P1[0] = I1;
    for (int sc = 1; sc <= coarsest; ++sc) {
        const int sw = std::max(1, w >> sc), sh = std::max(1, h >> sc);
        P0[sc] = resize_plane(P0[sc - 1], sw, sh, true, true);
        P1[sc] = resize_plane(P1[sc - 1], sw, sh, true, true);
    }
    Plane U, V;
    const bool tm = std::getenv("CAMSIM_TIMING") != nullptr;
    auto T = [&]() { return std::chrono::steady_clock::now(); };
    double t_pyr = 0, t_search = 0, t_dens = 0, t_ref = 0;
    for (int sc = coarsest; sc >= finest; --sc) {
        const int sw = w >> sc, sh = h >> sc;
        if (sw < psz || sh < psz) continue;
        auto q0 = T();
        const Plane& A = P0[sc];
        const Plane& B = P1[sc];
        t_pyr += std::chrono::duration<double, std::milli>(T() - q0).count();
        if (U.p.empty()) {
            U = Plane{sw, sh, std::vector<float>((size_t)sw * sh, 0.0f)};
            V = U;
        } else {
            const double kx = (double)sw / U.w, ky = (double)sh / U.h;
            U = resize_plane(U, sw, sh, false, false);
            V = resize_plane(V, sw, sh, false, false);
            for (float& v : U.p) v = (float)(v * kx);
            for (float& v : V.p) v = (float)(v * ky);
        }
        Plane Ax, Ay;
        sobel(A, Ax, Ay);
        auto q1 = T();
        PatchSearch ps(A, B, Ax, Ay, psz, pstr, TT.grad_iters, true);
        ps.run(U, V);
        auto q2 = T();
        ps.densify(U, V);
        auto q3 = T();
        if (TT.refine_iters > 0)
            refine(A, B, U, V, TT.refine_iters, 5, TT.alpha, TT.delta, TT.gamma, TT.eps, 1.6, TT.refine_norm);
        auto q4 = T();
        t_search += std::chrono::duration<double, std::milli>(q2 - q1).count();
        t_dens += std::chrono::duration<double, std::milli>(q3 - q2).count();
        t_ref += std::chrono::duration<double, std::milli>(q4 - q3).count();
    }
    if (tm) log("pyramide %.1f, blocs %.1f, densification %.1f, affinage %.1f ms", t_pyr, t_search, t_dens, t_ref);
    // Retour a la pleine resolution.
    const double kx = (double)w / U.w, ky = (double)h / U.h;
    std::vector<float> flow((size_t)w * h * 2);
    const double sx = (double)U.w / w, sy = (double)U.h / h;
#pragma omp parallel for
    for (int y = 0; y < h; ++y) {
        const float fy = (float)((y + 0.5) * sy - 0.5);
        for (int x = 0; x < w; ++x) {
            const float fx = (float)((x + 0.5) * sx - 0.5);
            const size_t i = (size_t)y * w + x;
            flow[i * 2] = (float)(U.bil(fx, fy) * kx);
            flow[i * 2 + 1] = (float)(V.bil(fx, fy) * ky);
        }
    }
    return flow;
}

}  // namespace

std::vector<float> estimate_flow(const float* a, const float* b, int w, int h, int finest, bool use_gpu) {
    if (use_gpu) {
        std::vector<float> f;
        if (gpu::estimate_flow(a, b, w, h, finest, f)) return f;
    }
    auto t0 = std::chrono::steady_clock::now();
    auto ga = to_gray8(a, w, h), gb = to_gray8(b, w, h);
    auto t1 = std::chrono::steady_clock::now();
    auto f = dis_flow(ga, gb, w, h, finest);
    auto t2 = std::chrono::steady_clock::now();
    if (std::getenv("CAMSIM_TIMING")) log("gris %.1f ms, dis %.1f ms",
        std::chrono::duration<double, std::milli>(t1 - t0).count(), std::chrono::duration<double, std::milli>(t2 - t1).count());
    return f;
}

std::vector<float> warp_rows(const std::vector<float>& img, int w, int h, int c, const std::vector<float>& flow,
                             const std::vector<double>& tau) {
    std::vector<float> out(img.size());
#pragma omp parallel for
    for (int y = 0; y < h; ++y) {
        const double t = tau[y];
        for (int x = 0; x < w; ++x) {
            const size_t i = (size_t)y * w + x;
            const double sx = std::clamp(x - t * flow[i * 2], 0.0, (double)(w - 1));
            const double sy = std::clamp(y - t * flow[i * 2 + 1], 0.0, (double)(h - 1));
            const int x1 = std::min((int)sx, std::max(w - 2, 0)), y1 = std::min((int)sy, std::max(h - 2, 0));
            const int x2 = std::min(x1 + 1, w - 1), y2 = std::min(y1 + 1, h - 1);
            const double ax = sx - x1, ay = sy - y1;
            for (int k = 0; k < c; ++k)
                out[i * c + k] = (float)((1 - ay) * ((1 - ax) * img[((size_t)y1 * w + x1) * c + k] + ax * img[((size_t)y1 * w + x2) * c + k]) +
                                         ay * ((1 - ax) * img[((size_t)y2 * w + x1) * c + k] + ax * img[((size_t)y2 * w + x2) * c + k]));
        }
    }
    return out;
}

std::vector<double> row_offsets_in_frames(int h, double fps, double readout_time_s, bool bottom_to_top) {
    std::vector<double> tau(h);
    for (int y = 0; y < h; ++y) {
        double pos = (y + 0.5) / h - 0.5;
        if (bottom_to_top) pos = -pos;
        tau[y] = readout_time_s * pos * fps;
    }
    return tau;
}

std::vector<float> rolling_shutter(const float* cur, const float* prev, const float* next, int w, int h, double fps,
                                   double readout_time_s, bool bottom_to_top, std::vector<float>* flow_next_out,
                                   int finest, bool use_gpu) {
    const size_t N = (size_t)w * h;
    std::vector<float> img(cur, cur + N * 3);
    if (readout_time_s <= 0 || (!prev && !next)) return img;
    const std::vector<double> tau = row_offsets_in_frames(h, fps, readout_time_s, bottom_to_top);
    if (use_gpu) {
        std::vector<float> out;
        const bool ok = gpu::rolling_shutter(cur, prev, next, w, h, tau, finest, out, flow_next_out);
        if (log_enabled()) log("  obturateur deroulant sur la carte graphique : %s", ok ? "oui" : "non (processeur)");
        if (ok) return out;
    }
    std::vector<float> fn, fp;
    const std::vector<float> gc = to_gray8(cur, w, h);        // l'image courante, une seule fois
    if (next) fn = dis_flow(gc, to_gray8(next, w, h), w, h, finest);
    if (prev) fp = dis_flow(gc, to_gray8(prev, w, h), w, h, finest);
    // S'il manque un voisin (debut ou fin de plan), on suppose un mouvement regulier.
    if (fn.empty()) { fn = fp; for (float& v : fn) v = -v; }
    if (fp.empty()) { fp = fn; for (float& v : fp) v = -v; }
    std::vector<double> tl(h), te(h);
    for (int y = 0; y < h; ++y) { tl[y] = std::max(tau[y], 0.0); te[y] = std::max(-tau[y], 0.0); }
    const std::vector<float> later = warp_rows(img, w, h, 3, fn, tl);
    const std::vector<float> earlier = warp_rows(img, w, h, 3, fp, te);
    std::vector<float> out(N * 3);
    for (int y = 0; y < h; ++y) {
        const std::vector<float>& src = tau[y] >= 0 ? later : earlier;
        std::copy(src.begin() + (size_t)y * w * 3, src.begin() + (size_t)(y + 1) * w * 3, out.begin() + (size_t)y * w * 3);
    }
    if (flow_next_out && next) *flow_next_out = fn;
    return out;
}

std::vector<double> flicker_gain(int h, int64_t frame, double fps, double readout_time_s, double exposure_time_s,
                                 double flicker_hz, double depth, double phase, bool bottom_to_top) {
    if (flicker_hz <= 0 || depth <= 0) return {};
    std::vector<double> g(h);
    const double x = flicker_hz * exposure_time_s;
    const double att = x == 0 ? 1.0 : std::sin(PI * x) / (PI * x);
    for (int y = 0; y < h; ++y) {
        double pos = (y + 0.5) / h - 0.5;
        if (bottom_to_top) pos = -pos;
        const double t = frame / fps + readout_time_s * pos;
        g[y] = 1.0 + depth * att * std::cos(2.0 * PI * flicker_hz * (t + 0.5 * exposure_time_s) + phase);
    }
    return g;
}

}  // namespace camsim
