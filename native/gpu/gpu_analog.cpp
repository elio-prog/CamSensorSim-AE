// Signal analogique sur la carte graphique : memes etapes que analog_apply (core/post.cpp), memes tirages
// (plan_tape, bruit a compteur). Les filtres en frequence deviennent des convolutions circulaires par ligne.
#include "gpu_analog.h"

#include <algorithm>
#include <cmath>
#include <functional>

#include "../core/analog_plan.h"
#include "../core/grid.h"
#include "../core/util.h"
#include "gpu.h"

namespace camsim {
namespace gpu {

using namespace analog_detail;

namespace {

constexpr double PI = 3.14159265358979323846;

inline double butter(double f, double fc, int order) { return 1.0 / std::sqrt(1.0 + std::pow(f / fc, 2 * order)); }

// Reponse impulsionnelle (longueur N) du filtre H(f) applique comme filter_rows : H[k] = H(|f_k|).
std::vector<double> impulse(int N, double fs, const std::function<double(double)>& H) {
    std::vector<cplx> a(N);
    for (int k = 0; k < N; ++k) {
        const int kk = (k <= N / 2) ? k : N - k;
        a[k] = cplx(H(kk * fs / N), 0.0);
    }
    FFT(N).inverse(a);
    std::vector<double> h(N);
    for (int k = 0; k < N; ++k) h[k] = a[k].real();
    return h;
}

struct Ctx {
    Session& S;
    int L, N;
    double fs;
    size_t M;
    DevPtr tmp;                                       // tampon de la taille de la trame
    DevPtr partial;                                   // sommes partielles
    std::vector<double> part_host;
};

DevPtr plane(Ctx& c) { return c.S.alloc(c.M * sizeof(double)); }

// x <- filtre H (sur place, via le tampon).
void filt(Ctx& c, DevPtr& x, const std::function<double(double)>& H) {
    const std::vector<double> h = impulse(c.N, c.fs, H);
    DevPtr dh = c.S.alloc(c.N * sizeof(double));
    c.S.upload(dh, h.data(), c.N * sizeof(double));
    int N = c.N;
    void* a[] = {&x, &c.tmp, &N, &dh};
    c.S.launch_grid("k_an_filt", (unsigned)c.L, 256, a, (unsigned)(4 * c.N * sizeof(float)));
    std::swap(x, c.tmp);
}

void lowpass(Ctx& c, DevPtr& x, double fc, int order = 4) {
    if (fc <= 0 || fc >= c.fs / 2) return;
    filt(c, x, [=](double f) { return butter(f, fc, order); });
}

double sum(Ctx& c, DevPtr x, double m, int mode) {
    int n = (int)c.M;
    void* a[] = {&x, &n, &m, &mode, &c.partial};
    c.S.launch("k_sum_partial", c.M, a);
    const size_t nb = (c.M + 255) / 256;
    c.part_host.resize(nb);
    c.S.download(c.part_host.data(), c.partial, nb * sizeof(double));
    double s = 0;
    for (double v : c.part_host) s += v;
    return s;
}

double stdev(Ctx& c, DevPtr x) {
    const double m = sum(c, x, 0.0, 0) / c.M;
    return std::sqrt(sum(c, x, m, 1) / c.M);
}

void noise(Ctx& c, DevPtr n, uint64_t base) {
    int M = (int)c.M;
    void* a[] = {&n, &M, &base};
    c.S.launch("k_an_noise", c.M, a);
}

void shift_lines(Ctx& c, DevPtr& x, DevPtr shift) {
    int L = c.L, N = c.N;
    void* a[] = {&x, &c.tmp, &L, &N, &shift};
    c.S.launch("k_an_shift", c.M, a);
    std::swap(x, c.tmp);
}

void avg_prev(Ctx& c, DevPtr& x) {
    int L = c.L, N = c.N;
    void* a[] = {&x, &c.tmp, &L, &N};
    c.S.launch("k_an_avgprev", c.M, a);
    std::swap(x, c.tmp);
}

void tape(Ctx& c, DevPtr& Y, DevPtr& U, DevPtr& V, const AnalogParams& p, const TapeSpec& t, const TapePlan& P) {
    const TapeSettings T = tape_settings(p, t);
    const double us = c.fs * 1e-6;
    int n = (int)c.M, L = c.L, N = c.N;
    if (p.sharpness > 0) {
        DevPtr lo = plane(c);                          // lo = copie de Y, puis passe-bas
        double one = 1.0;
        c.S.zero(lo, c.M * sizeof(double));
        { void* a[] = {&lo, &Y, &n, &one}; c.S.launch("k_axpy", c.M, a); }
        lowpass(c, lo, 1.5e6, 2);
        double s = p.sharpness;
        void* a[] = {&Y, &lo, &n, &s};
        c.S.launch("k_an_sharp", c.M, a);
    }
    lowpass(c, Y, T.luma);
    if (T.snr < 200) {                                // bruit FM, spectre triangulaire puis passe-bas
        DevPtr nz = plane(c);
        noise(c, nz, P.noise_y);
        const double luma = T.luma, fsh = c.fs / 2;
        filt(c, nz, [=](double f) { return f / std::max(luma, 1.0) * ((luma > 0 && luma < fsh) ? butter(f, luma, 4) : 1.0); });
        double k = std::pow(10.0, -T.snr / 20.0) / std::max(stdev(c, nz), 1e-12);
        void* a[] = {&Y, &nz, &n, &k};
        c.S.launch("k_axpy", c.M, a);
    }
    lowpass(c, U, T.chroma, 2);
    lowpass(c, V, T.chroma, 2);
    if (T.csnr < 200)
        for (int ch = 0; ch < 2; ++ch) {
            DevPtr nz = plane(c);
            noise(c, nz, ch == 0 ? P.noise_u : P.noise_v);
            lowpass(c, nz, T.chroma, 2);
            double k = std::pow(10.0, -T.csnr / 20.0) / std::max(stdev(c, nz), 1e-12);
            DevPtr target = ch == 0 ? U : V;
            void* a[] = {&target, &nz, &n, &k};
            c.S.launch("k_axpy", c.M, a);
        }
    if (T.cdelay) {
        std::vector<double> d(L, T.cdelay * us);
        DevPtr dd = c.S.alloc(L * sizeof(double));
        c.S.upload(dd, d.data(), L * sizeof(double));
        shift_lines(c, U, dd);
        shift_lines(c, V, dd);
    }
    avg_prev(c, U);
    avg_prev(c, V);
    DevPtr ramp = c.S.alloc(L * sizeof(double)), seed = c.S.alloc(L * sizeof(uint64_t)), sh = c.S.alloc(L * sizeof(double));
    c.S.upload(ramp, P.head_ramp.data(), L * sizeof(double));
    c.S.upload(seed, P.head_seed.data(), L * sizeof(uint64_t));
    c.S.upload(sh, P.shift.data(), L * sizeof(double));
    { void* a[] = {&Y, &ramp, &seed, &L, &N}; c.S.launch("k_an_head", c.M, a); }
    shift_lines(c, Y, sh);
    shift_lines(c, U, sh);
    shift_lines(c, V, sh);
    if (!P.drops.empty()) {
        DevPtr vals = c.S.alloc(std::max<size_t>(1, P.drop_vals.size()) * sizeof(double));
        if (!P.drop_vals.empty()) c.S.upload(vals, P.drop_vals.data(), P.drop_vals.size() * sizeof(double));
        for (const TapePlan::Drop& D : P.drops) {      // dans l'ordre : une perte peut recopier une ligne deja touchee
            int line = D.line, x0 = D.x0, x1 = D.x1, val = D.val;
            void* a[] = {&Y, &U, &V, &line, &x0, &x1, &val, &vals, &N};
            c.S.launch("k_an_drop", (size_t)std::max(0, x1 - x0), a);
        }
    }
    if (!P.tracks.empty()) {
        std::vector<int> info;
        std::vector<double> base;
        for (const TapePlan::Track& tr : P.tracks) {
            info.insert(info.end(), {tr.line, tr.hit, tr.x0, tr.x1, tr.val, tr.s});
            base.push_back(tr.base);
        }
        DevPtr di = c.S.alloc(info.size() * sizeof(int)), db = c.S.alloc(base.size() * sizeof(double));
        DevPtr tv = c.S.alloc(std::max<size_t>(1, P.track_vals.size()) * sizeof(double));
        c.S.upload(di, info.data(), info.size() * sizeof(int));
        c.S.upload(db, base.data(), base.size() * sizeof(double));
        if (!P.track_vals.empty()) c.S.upload(tv, P.track_vals.data(), P.track_vals.size() * sizeof(double));
        void* a[] = {&Y, &di, &db, &tv, &N};
        c.S.launch_grid("k_an_track", (unsigned)P.tracks.size(), 256, a, (unsigned)(N * sizeof(double)));
    }
}

void composite(Ctx& c, DevPtr& Y, DevPtr& U, DevPtr& V, const Std& Sd, bool pal, const AnalogParams& p, Rng& rng, int64_t frame) {
    int L = c.L, N = c.N, n = (int)c.M, ipal = pal ? 1 : 0, total = Sd.total_lines;
    double fsc = Sd.fsc, fs = c.fs, cpl = Sd.cycles_per_line;
    long long fr = frame;
    double link_k = p.link_snr_db < 200 ? std::pow(10.0, -p.link_snr_db / 20.0) : 0.0;
    uint64_t base = rng.stream(1000);
    DevPtr comp = plane(c);
    { void* a[] = {&Y, &U, &V, &comp, &L, &N, &ipal, &fsc, &fs, &total, &cpl, &fr, &link_k, &base}; c.S.launch("k_an_mod", c.M, a); }
    lowpass(c, comp, Sd.luma_bw, 10);
    const double bw = std::max(Sd.c_bw0, Sd.c_bw1) * 0.9, sfsc = Sd.fsc;
    auto band = [=](double f) { return std::exp(-0.5 * std::pow((f - sfsc) / std::max(bw, 1.0), 2)); };
    DevPtr Cs = plane(c), Ys = plane(c);
    if (p.comb) {
        int k = Sd.comb_step;
        { void* a[] = {&comp, &Cs, &L, &N, &k}; c.S.launch("k_an_comb", c.M, a); }
        filt(c, Cs, band);
        void* a[] = {&comp, &Cs, &Ys, &n};
        c.S.launch("k_sub", c.M, a);
    } else {
        // Cs = filtre bande (comp), Ys = filtre complementaire (comp)
        double one = 1.0;
        c.S.zero(Cs, c.M * sizeof(double));
        { void* a[] = {&Cs, &comp, &n, &one}; c.S.launch("k_axpy", c.M, a); }
        c.S.zero(Ys, c.M * sizeof(double));
        { void* a[] = {&Ys, &comp, &n, &one}; c.S.launch("k_axpy", c.M, a); }
        filt(c, Cs, band);
        filt(c, Ys, [=](double f) { return 1.0 - band(f); });
    }
    double hue = pal ? 0.0 : p.hue_deg * PI / 180;
    DevPtr a2 = plane(c), b2 = plane(c);
    { void* a[] = {&Cs, &a2, &b2, &L, &N, &fsc, &fs, &total, &cpl, &fr, &hue}; c.S.launch("k_an_demod", c.M, a); }
    lowpass(c, a2, Sd.c_bw0, 2);
    lowpass(c, b2, Sd.c_bw0, 2);
    Y = Ys;
    if (pal) {
        { void* a[] = {&b2, &L, &N}; c.S.launch("k_an_palsw", c.M, a); }
        avg_prev(c, a2);
        avg_prev(c, b2);
        U = a2;
        V = b2;
    } else {
        lowpass(c, b2, Sd.c_bw0, 2);                   // I
        lowpass(c, a2, Sd.c_bw1, 2);                   // Q
        void* a[] = {&b2, &a2, &U, &V, &n};
        c.S.launch("k_an_ntsc", c.M, a);
    }
}

void inv3(const double* m, double* r) {
    double a = m[0], b = m[1], c = m[2], d = m[3], e = m[4], f = m[5], g = m[6], hh = m[7], i = m[8];
    double det = a * (e * i - f * hh) - b * (d * i - f * g) + c * (d * hh - e * g);
    r[0] = (e * i - f * hh) / det; r[1] = (c * hh - b * i) / det; r[2] = (b * f - c * e) / det;
    r[3] = (f * g - d * i) / det; r[4] = (a * i - c * g) / det; r[5] = (c * d - a * f) / det;
    r[6] = (d * hh - e * g) / det; r[7] = (b * g - a * hh) / det; r[8] = (a * e - b * d) / det;
}

bool resize_d(Session& S, DevPtr src, int w, int h, DevPtr dst, int nw, int nh, int method) {
    Taps1D tx, ty;
    resize_taps(method, w, h, nw, nh, tx, ty);
    auto up = [&](const void* d, size_t bytes) {
        DevPtr p = S.alloc(bytes);
        S.upload(p, d, bytes);
        return p;
    };
    int c = 3;
    DevPtr xs = up(tx.start.data(), tx.start.size() * sizeof(int)), xi = up(tx.src.data(), tx.src.size() * sizeof(int)),
           xw = up(tx.w.data(), tx.w.size() * sizeof(double));
    DevPtr ys = up(ty.start.data(), ty.start.size() * sizeof(int)), yi = up(ty.src.data(), ty.src.size() * sizeof(int)),
           yw = up(ty.w.data(), ty.w.size() * sizeof(double));
    DevPtr tmp = S.alloc((size_t)h * nw * 3 * sizeof(double));
    { void* a[] = {&src, &tmp, &w, &h, &nw, &c, &xs, &xi, &xw}; S.launch("k_resize_hd", (size_t)h * nw * 3, a); }
    { void* a[] = {&tmp, &dst, &nw, &nh, &c, &ys, &yi, &yw}; S.launch("k_resize_vd", (size_t)nh * nw * 3, a); }
    return S.ok();
}

}  // namespace

bool analog_apply(std::vector<float>& img, int w, int h, int64_t frame, const AnalogParams& p) {
    if (p.standard == Standard::Off) return true;
    if (!available()) return false;
    const bool pal = p.standard == Standard::PAL;
    const Std& Sd = pal ? PAL : NTSC;
    const double fs = 4.0 * Sd.fsc;
    const int L = Sd.lines, N = (int)std::nearbyint(Sd.active_us * 1e-6 * fs);
    const double par = (4.0 / 3.0) / ((double)N / L);
    int hr, wr;
    sensor_grid(N, L, par, 1.0, h, w, &hr, &wr);
    if (p.anamorphic) { hr = L; wr = N; }
    int y0 = (L - hr) / 2, x0 = (N - wr) / 2;

    Session S;
    if (!S.ok()) return false;
    Ctx c{S, L, N, fs, (size_t)L * N, 0, 0, {}};
    c.tmp = plane(c);
    c.partial = S.alloc(((c.M + 255) / 256) * sizeof(double));
    const int n = w * h;
    DevPtr dimg = S.alloc((size_t)n * 3 * sizeof(float)), rgb = S.alloc((size_t)n * 3 * sizeof(double));
    S.upload(dimg, img.data(), (size_t)n * 3 * sizeof(float));
    { int nn = n; void* a[] = {&dimg, &rgb, &nn}; S.launch("k_an_in", n, a); }
    DevPtr small = S.alloc((size_t)wr * hr * 3 * sizeof(double));
    if (!resize_d(S, rgb, w, h, small, wr, hr, 3)) return false;
    DevPtr Y = plane(c), U = plane(c), V = plane(c);
    S.zero(Y, c.M * sizeof(double));
    S.zero(U, c.M * sizeof(double));
    S.zero(V, c.M * sizeof(double));
    { void* a[] = {&small, &Y, &U, &V, &wr, &hr, &x0, &y0, (void*)&N}; S.launch("k_an_raster", (size_t)wr * hr, a); }
    Rng rng(frame_seed(p, frame));
    if (p.tape != Tape::None)
        for (int g = 0; g < std::max(1, p.generations); ++g) {
            const TapePlan plan = plan_tape(L, N, fs, p, TAPES[(int)p.tape], rng, g);
            tape(c, Y, U, V, p, TAPES[(int)p.tape], plan);
        }
    if (p.composite) composite(c, Y, U, V, Sd, pal, p, rng, frame);
    double inv[9];
    inv3(YUV, inv);
    DevPtr crop = S.alloc((size_t)wr * hr * 3 * sizeof(double));
    {
        void* a[] = {&Y, &U, &V, &crop, &wr, &hr, &x0, &y0, (void*)&N, &inv[0], &inv[1], &inv[2], &inv[3], &inv[4], &inv[5], &inv[6], &inv[7], &inv[8]};
        S.launch("k_an_out", (size_t)wr * hr, a);
    }
    DevPtr big = rgb;                                  // meme taille que l'entree
    if (!resize_d(S, crop, wr, hr, big, w, h, 1)) return false;
    { int nn = n; void* a[] = {&big, &dimg, &nn}; S.launch("k_an_final", n, a); }
    return S.download(img.data(), dimg, (size_t)n * 3 * sizeof(float));
}

}  // namespace gpu
}  // namespace camsim
