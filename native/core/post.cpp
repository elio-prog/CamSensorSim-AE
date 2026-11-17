#define _USE_MATH_DEFINES
#include "post.h"
#include "analog_plan.h"
#include "../gpu/gpu_analog.h"
#include "log.h"

#include <algorithm>
#include <cmath>
#include <random>

#include "grid.h"
#include "util.h"

namespace camsim {

namespace {

const double REC709_TO_AP1[9] = {0.6130974024, 0.3395231462, 0.0473794514, 0.0701937225, 0.9163538791,
                                 0.0134523985, 0.0206155929, 0.1095697729, 0.8698146342};
const double AP1_TO_REC709[9] = {1.7050509927, -0.6217921207, -0.0832588720, -0.1302564175, 1.1408047366,
                                 -0.0105483191, -0.0240033568, -0.1289689761, 1.1529723329};
// Courbe BT.709 avec les constantes exactes de BT.2020 (sources.py).
const double A709 = 1.09929682680944, B709 = 0.018053968510807;

inline double enc709(double x) { x = std::max(x, 0.0); return x < B709 ? 4.5 * x : A709 * std::pow(x, 0.45) - (A709 - 1.0); }
inline double dec709(double v) { return v < 4.5 * B709 ? v / 4.5 : std::pow((std::max(v, 0.0) + A709 - 1.0) / A709, 1 / 0.45); }

inline void mat3(const double* m, const double* a, double* b) {
    for (int k = 0; k < 3; ++k) b[k] = m[k * 3] * a[0] + m[k * 3 + 1] * a[1] + m[k * 3 + 2] * a[2];
}

}  // namespace

// ------------------------------------------------------------------ etage video
void dsp_apply(std::vector<float>& img, int w, int h, const DspParams& p, bool interlaced, double gain) {
    if (!p.enabled) return;
    const size_t N = (size_t)w * h;
    std::vector<double> v(N * 3), y(N), det(N, 0.0);
    const bool do_knee = !(p.knee_point >= 1.5 || p.knee_slope >= 1.0);
#pragma omp parallel for
    for (int r = 0; r < h; ++r)
        for (int c = 0; c < w; ++c) {
            size_t i = (size_t)r * w + c;
            double a[3] = {img[i * 3] * gain, img[i * 3 + 1] * gain, img[i * 3 + 2] * gain}, rgb[3];
            mat3(AP1_TO_REC709, a, rgb);
            for (int k = 0; k < 3; ++k) {
                double e = enc709(std::max(rgb[k], 0.0));
                if (do_knee && e > p.knee_point) e = p.knee_point + (e - p.knee_point) * p.knee_slope;
                v[i * 3 + k] = e;
            }
            y[i] = 0.2126 * v[i * 3] + 0.7152 * v[i * 3 + 1] + 0.0722 * v[i * 3 + 2];
        }
    const int ls = interlaced ? 2 : 1;
    auto Y = [&](int r, int c) { return y[(size_t)std::clamp(r, 0, h - 1) * w + std::clamp(c, 0, w - 1)]; };
#pragma omp parallel for
    for (int r = 0; r < h; ++r)
        for (int c = 0; c < w; ++c) {
            double d = 0.0, yc = Y(r, c);
            if (p.detail_h) d += p.detail_h * (yc - 0.5 * (Y(r, c - 2) + Y(r, c + 2)));
            if (p.detail_v) d += p.detail_v * (yc - 0.5 * (Y(r - ls, c) + Y(r + ls, c)));
            if (p.coring > 0) d = (d > 0 ? 1 : (d < 0 ? -1 : 0)) * std::max(std::fabs(d) - p.coring, 0.0);
            if (p.limit > 0) d = std::clamp(d, -p.limit, p.limit);
            det[(size_t)r * w + c] = d;
        }
#pragma omp parallel for
    for (int r = 0; r < h; ++r)
        for (int c = 0; c < w; ++c) {
            size_t i = (size_t)r * w + c;
            double vv[3], yy = y[i] + det[i];
            for (int k = 0; k < 3; ++k) vv[k] = v[i * 3 + k] + det[i];
            if (p.highlight_chroma > 0 && p.knee_point < p.white_clip) {
                double top = std::max({vv[0], vv[1], vv[2]});
                double t = std::clamp((top - p.knee_point) / (p.white_clip - p.knee_point), 0.0, 1.0) * p.highlight_chroma;
                for (int k = 0; k < 3; ++k) vv[k] += (yy - vv[k]) * t;
            }
            double lin[3], out[3];
            for (int k = 0; k < 3; ++k) lin[k] = dec709(std::min(vv[k], p.white_clip));
            mat3(REC709_TO_AP1, lin, out);
            for (int k = 0; k < 3; ++k) img[i * 3 + k] = (float)out[k];
        }
}

// ------------------------------------------------------------------ signal analogique
namespace analog_detail {

const Std PAL = {4.43361875e6, 52.0, 5.0e6, 1.3e6, 1.3e6, 283.7516, 576, 4, 625};
const Std NTSC = {3.579545e6, 52.66, 4.2e6, 1.3e6, 0.4e6, 227.5, 486, 2, 525};

const TapeSpec TAPES[6] = {
    {0, 0, 0, 0, 0, 0, 0, 0, 0},
    {3.0e6, 0.4e6, 43.0, 38.0, 0.15, 0.04, 0.6, 5, 3.0},   // VHS (SP)
    {2.6e6, 0.35e6, 40.0, 35.0, 0.2, 0.07, 0.8, 6, 4.0},   // VHS (LP)
    {5.0e6, 0.4e6, 45.0, 39.0, 0.12, 0.03, 0.5, 5, 3.0},   // S-VHS
    {3.0e6, 0.4e6, 43.0, 38.0, 0.15, 0.03, 0.4, 4, 2.5},   // Video8
    {5.0e6, 0.4e6, 45.0, 39.0, 0.12, 0.03, 0.4, 4, 2.5},   // Hi8
};

const double YUV[9] = {0.299, 0.587, 0.114, -0.14713, -0.28886, 0.436, 0.615, -0.51499, -0.10001};

uint64_t mix64(uint64_t z) {
    z += 0x9E3779B97F4A7C15ULL;
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}

double ctr_normal(uint64_t base, uint64_t i) {
    const uint64_t h = mix64(base ^ mix64(i));
    double a = (double)(mix64(h) >> 11) * (1.0 / 9007199254740992.0), b = (double)(mix64(h ^ 0xD1B54A32D192ED03ULL) >> 11) * (1.0 / 9007199254740992.0);
    a = std::min(std::max(a, 1e-12), 1.0 - 1e-12);
    return std::sqrt(-2.0 * std::log(a)) * std::cos(6.283185307179586 * b);
}

uint64_t frame_seed(const AnalogParams& p, int64_t frame) {
    return ((uint64_t)p.seed * 0x9E3779B97F4A7C15ULL) ^ (uint64_t)(31 + 1000003 * frame);
}

TapeSettings tape_settings(const AnalogParams& p, const TapeSpec& t) {
    TapeSettings T;
    T.luma = p.luma_bw_mhz >= 0 ? p.luma_bw_mhz * 1e6 : t.luma;
    T.chroma = p.chroma_bw_mhz >= 0 ? p.chroma_bw_mhz * 1e6 : t.chroma;
    T.snr = p.snr_db >= 0 ? p.snr_db : t.snr;
    T.csnr = p.chroma_snr_db >= 0 ? p.chroma_snr_db : t.csnr;
    T.cdelay = p.chroma_delay_us >= 0 ? p.chroma_delay_us : t.cdelay;
    T.jitter = p.jitter_us >= 0 ? p.jitter_us : t.jitter;
    T.skew = p.skew_us >= 0 ? p.skew_us : t.skew;
    T.head = (int)(p.head_switch_lines >= 0 ? p.head_switch_lines : t.head);
    T.head_shift = p.head_switch_us >= 0 ? p.head_switch_us : t.head_shift;
    return T;
}

namespace {
double stdev_vec(const std::vector<double>& v) {
    double m = 0, s = 0;
    for (double x : v) m += x;
    m /= v.size();
    for (double x : v) s += (x - m) * (x - m);
    return std::sqrt(s / v.size());
}
}  // namespace

TapePlan plan_tape(int L, int N, double fs, const AnalogParams& p, const TapeSpec& t, Rng& rng, int gen) {
    const TapeSettings T = tape_settings(p, t);
    const double us = fs * 1e-6;
    TapePlan P;
    P.noise_y = rng.stream(10 * gen + 1);
    P.noise_u = rng.stream(10 * gen + 2);
    P.noise_v = rng.stream(10 * gen + 3);
    // Base de temps et haut qui penche (une marche aleatoire lissee par trame).
    std::vector<double> off(L, 0.0);
    for (int field = 0; field < 2; ++field) {
        std::vector<int> k;
        for (int r = field; r < L; r += 2) k.push_back(r);
        const int n = (int)k.size();
        std::vector<double> walk(n);
        double acc = 0;
        for (int i = 0; i < n; ++i) { acc += rng.normal(); walk[i] = acc * 0.25 + rng.normal(); }
        if (n > 4) {                                   // lissage gaussien (sigma 2), bords en miroir
            std::vector<double> sm(n, 0.0);
            double ws = 0;
            for (int d = -8; d <= 8; ++d) ws += std::exp(-0.5 * d * d / 4.0);
            for (int i = 0; i < n; ++i)
                for (int d = -8; d <= 8; ++d) {
                    int j = i + d;
                    while (j < 0 || j >= n) j = j < 0 ? -j : 2 * (n - 1) - j;
                    sm[i] += std::exp(-0.5 * d * d / 4.0) / ws * walk[j];
                }
            walk.swap(sm);
        }
        double sd = std::max(stdev_vec(walk), 1e-9);
        double sk = 0.6 + 0.4 * rng.uniform();
        for (int i = 0; i < n; ++i) off[k[i]] = T.jitter * walk[i] / sd + T.skew * std::exp(-i / 12.0) * sk;
    }
    // Commutation des tetes : bas de chaque trame.
    P.head_ramp.assign(L, 0.0);
    P.head_seed.assign(L, 0);
    if (T.head > 0)
        for (int field = 0; field < 2; ++field) {
            std::vector<int> k;
            for (int r = field; r < L; r += 2) k.push_back(r);
            const int n = std::min(T.head, (int)k.size());
            const double amp = 0.8 + 0.4 * rng.uniform();
            for (int i = 0; i < n; ++i) {
                int r = k[k.size() - n + i];
                double ramp = n > 1 ? 0.3 + 0.7 * i / (n - 1.0) : 0.3;
                off[r] += T.head_shift * ramp * amp;
                P.head_ramp[r] = ramp;
                P.head_seed[r] = rng.stream(10 * gen + 4) ^ (uint64_t)r;
            }
        }
    P.shift.resize(L);
    for (int r = 0; r < L; ++r) P.shift[r] = off[r] * us;
    // Pertes de signal.
    if (p.dropouts > 0) {
        int n_do = (int)std::poisson_distribution<int>(p.dropouts)(rng.g);
        for (int d = 0; d < n_do; ++d) {
            TapePlan::Drop D;
            D.line = std::min(L - 1, (int)(rng.uniform() * L));
            int length = (int)std::min((double)N, std::max(2.0, std::exponential_distribution<double>(1.0 / 12.0)(rng.g) * us));
            D.x0 = std::min(std::max(1, N - length) - 1, (int)(rng.uniform() * std::max(1, N - length)));
            D.x1 = std::min(N, D.x0 + length);
            if (p.dropout_comp && D.line >= 2) {
                D.val = -1;
            } else {
                D.val = (int)P.drop_vals.size();
                for (int j = D.x0; j < D.x1; ++j) P.drop_vals.push_back(0.6 + 0.4 * rng.uniform());
            }
            P.drops.push_back(D);
        }
    }
    // Bande mal calee.
    if (p.tracking > 0) {
        double c = std::fmod(rng.uniform() * L, (double)L);
        double width = 8 + 40 * p.tracking;
        for (int line = 0; line < L; ++line) {
            double dist = std::min(std::fabs(line - c), L - std::fabs(line - c));
            if (dist >= width) continue;
            double wgt = 1.0 - dist / width;
            TapePlan::Track tr{line, 0, 0, 0, -1, 0, 0.0};
            if (rng.uniform() < 0.6 * wgt * p.tracking) {
                tr.hit = 1;
                tr.x0 = std::min(N - 1, (int)(rng.uniform() * N));
                int ln = N / 20 + (int)(rng.uniform() * std::max(1, N / 2 - N / 20));
                tr.base = rng.uniform() * 0.8;
                tr.x1 = std::min(N, tr.x0 + ln);
                tr.val = (int)P.track_vals.size();
                for (int j = tr.x0; j < tr.x1; ++j) P.track_vals.push_back(rng.normal());
            }
            tr.s = (int)(wgt * p.tracking * 6 * us * rng.normal());
            P.tracks.push_back(tr);
        }
    }
    return P;
}

}  // namespace analog_detail

using namespace analog_detail;

namespace {

void inv3(const double* m, double* r) {
    double a = m[0], b = m[1], c = m[2], d = m[3], e = m[4], f = m[5], g = m[6], hh = m[7], i = m[8];
    double det = a * (e * i - f * hh) - b * (d * i - f * g) + c * (d * hh - e * g);
    r[0] = (e * i - f * hh) / det; r[1] = (c * hh - b * i) / det; r[2] = (b * f - c * e) / det;
    r[3] = (f * g - d * i) / det; r[4] = (a * i - c * g) / det; r[5] = (c * d - a * f) / det;
    r[6] = (d * hh - e * g) / det; r[7] = (b * g - a * hh) / det; r[8] = (a * e - b * d) / det;
}

inline double butter(double f, double fc, int order) { return 1.0 / std::sqrt(1.0 + std::pow(f / fc, 2 * order)); }

void lowpass(std::vector<double>& x, int L, int N, double fc, double fs, int order = 4) {
    if (fc <= 0 || fc >= fs / 2) return;
    filter_rows(x, L, N, fs, [=](double f) { return butter(f, fc, order); });
}

void shift_lines(std::vector<double>& x, int L, int N, const std::vector<double>& shift) {
    std::vector<double> out(x.size());
#pragma omp parallel for
    for (int r = 0; r < L; ++r)
        for (int j = 0; j < N; ++j) {
            double idx = j - shift[r];
            int i0 = (int)std::floor(idx);
            double f = idx - i0;
            double a = x[(size_t)r * N + std::clamp(i0, 0, N - 1)], b = x[(size_t)r * N + std::clamp(i0 + 1, 0, N - 1)];
            out[(size_t)r * N + j] = a * (1 - f) + b * f;
        }
    x.swap(out);
}

void average_prev_field_line(std::vector<double>& x, int L, int N) {      // 0.5 (x + ligne r-2), bord : r
    std::vector<double> out(x.size());
    for (int r = 0; r < L; ++r) {
        int q = r >= 2 ? r - 2 : r;
        for (int j = 0; j < N; ++j) out[(size_t)r * N + j] = 0.5 * (x[(size_t)r * N + j] + x[(size_t)q * N + j]);
    }
    x.swap(out);
}

void fill_normal(std::vector<double>& v, uint64_t base) {
    const long long n = (long long)v.size();
#pragma omp parallel for
    for (long long i = 0; i < n; ++i) v[i] = ctr_normal(base, (uint64_t)i);
}

double stdev(const std::vector<double>& v) { return stdev_vec(v); }

// Un passage sur bande, avec son plan de tirages (plan_tape).
void tape_pass(std::vector<double>& Y, std::vector<double>& U, std::vector<double>& V, int L, int N, double fs,
               const AnalogParams& p, const TapeSpec& t, const TapePlan& P) {
    const TapeSettings T = tape_settings(p, t);
    const double us = fs * 1e-6;
    const double luma = T.luma, chroma = T.chroma, snr = T.snr, csnr = T.csnr, cdelay = T.cdelay;
    const size_t M = (size_t)L * N;
    if (p.sharpness > 0) {
        std::vector<double> lo = Y;
        lowpass(lo, L, N, 1.5e6, fs, 2);
        for (size_t i = 0; i < M; ++i) Y[i] += p.sharpness * (Y[i] - lo[i]);
    }
    lowpass(Y, L, N, luma, fs);
    if (snr < 200) {                                  // bruit FM, spectre triangulaire
        std::vector<double> n(M);
        fill_normal(n, P.noise_y);
        filter_rows(n, L, N, fs, [=](double f) { return f / std::max(luma, 1.0); });
        lowpass(n, L, N, luma, fs);
        double k = std::pow(10.0, -snr / 20.0) / std::max(stdev(n), 1e-12);
        for (size_t i = 0; i < M; ++i) Y[i] += n[i] * k;
    }
    lowpass(U, L, N, chroma, fs, 2);
    lowpass(V, L, N, chroma, fs, 2);
    if (csnr < 200)
        for (auto* C : {&U, &V}) {
            std::vector<double> n(M);
            fill_normal(n, C == &U ? P.noise_u : P.noise_v);
            lowpass(n, L, N, chroma, fs, 2);
            double k = std::pow(10.0, -csnr / 20.0) / std::max(stdev(n), 1e-12);
            for (size_t i = 0; i < M; ++i) (*C)[i] += n[i] * k;
        }
    if (cdelay) {
        std::vector<double> d(L, cdelay * us);
        shift_lines(U, L, N, d);
        shift_lines(V, L, N, d);
    }
    average_prev_field_line(U, L, N);
    average_prev_field_line(V, L, N);
    for (int r = 0; r < L; ++r)                         // bruit de la commutation des tetes
        if (P.head_ramp[r] > 0)
            for (int j = 0; j < N; ++j) Y[(size_t)r * N + j] += ctr_normal(P.head_seed[r], (uint64_t)j) * 0.05 * P.head_ramp[r];
    shift_lines(Y, L, N, P.shift);
    shift_lines(U, L, N, P.shift);
    shift_lines(V, L, N, P.shift);
    for (const TapePlan::Drop& D : P.drops)
        for (int j = D.x0; j < D.x1; ++j) {
            size_t i = (size_t)D.line * N + j;
            if (D.val < 0) {
                Y[i] = Y[i - 2 * N]; U[i] = U[i - 2 * N]; V[i] = V[i - 2 * N];
            } else {
                Y[i] = P.drop_vals[D.val + (j - D.x0)]; U[i] = 0.0; V[i] = 0.0;
            }
        }
    std::vector<double> row(N);
    for (const TapePlan::Track& tr : P.tracks) {
        const size_t o = (size_t)tr.line * N;
        if (tr.hit)
            for (int j = tr.x0; j < tr.x1; ++j) Y[o + j] = tr.base + 0.4 * P.track_vals[tr.val + (j - tr.x0)];
        for (int j = 0; j < N; ++j) row[j] = Y[o + ((j - tr.s) % N + N) % N];
        for (int j = 0; j < N; ++j) Y[o + j] = row[j];
    }
}

void composite_pass(std::vector<double>& Y, std::vector<double>& U, std::vector<double>& V, int L, int N, double fs,
                    const Std& S, bool pal, const AnalogParams& p, Rng& rng, int64_t frame) {
    const size_t M = (size_t)L * N;
    std::vector<double> w(M), sw(L, 1.0), comp(M);
    const double c33 = std::cos(33.0 * M_PI / 180), s33 = std::sin(33.0 * M_PI / 180);
#pragma omp parallel for
    for (int r = 0; r < L; ++r) {
        double scan = (r % 2) * (S.total_lines / 2.0) + r / 2 + (double)frame * S.total_lines;
        double ph0 = 2 * M_PI * std::fmod(scan * S.cycles_per_line, 1.0);
        if (pal) sw[r] = (r % 4 < 2) ? 1.0 : -1.0;
        for (int j = 0; j < N; ++j) {
            size_t i = (size_t)r * N + j;
            w[i] = 2 * M_PI * S.fsc * (j / fs) + ph0;
            double C;
            if (pal) C = U[i] * std::sin(w[i]) + sw[r] * V[i] * std::cos(w[i]);
            else {
                double I = U[i] * c33 + V[i] * (-s33) , Q = U[i] * s33 + V[i] * c33;   // (U, V) @ rot(33).T
                C = Q * std::sin(w[i]) + I * std::cos(w[i]);
            }
            comp[i] = Y[i] + C;
        }
    }
    if (p.link_snr_db < 200) {
        double k = std::pow(10.0, -p.link_snr_db / 20.0);
        std::vector<double> n(M);
        fill_normal(n, rng.stream(1000));
        for (size_t i = 0; i < M; ++i) comp[i] += n[i] * k;
    }
    lowpass(comp, L, N, S.luma_bw, fs, 10);
    const double bw = std::max(S.c_bw0, S.c_bw1) * 0.9;
    auto band = [=](double f) { return std::exp(-0.5 * std::pow((f - S.fsc) / std::max(bw, 1.0), 2)); };
    std::vector<double> Cs(M), Ys(M);
    if (p.comb) {
        const int k = S.comb_step;
        for (int r = 0; r < L; ++r) {
            int q = r >= k ? r - k : r;
            for (int j = 0; j < N; ++j) Cs[(size_t)r * N + j] = 0.5 * (comp[(size_t)r * N + j] - comp[(size_t)q * N + j]);
        }
        filter_rows(Cs, L, N, fs, band);
        for (size_t i = 0; i < M; ++i) Ys[i] = comp[i] - Cs[i];
    } else {
        Cs = comp;
        Ys = comp;
        filter_rows(Cs, L, N, fs, band);
        filter_rows(Ys, L, N, fs, [=](double f) { return 1.0 - band(f); });
    }
    const double hue = pal ? 0.0 : p.hue_deg * M_PI / 180;
    std::vector<double> a(M), b(M);
    for (size_t i = 0; i < M; ++i) {
        a[i] = 2 * Cs[i] * std::sin(w[i] + hue);
        b[i] = 2 * Cs[i] * std::cos(w[i] + hue);
    }
    lowpass(a, L, N, S.c_bw0, fs, 2);
    lowpass(b, L, N, S.c_bw0, fs, 2);
    Y.swap(Ys);
    if (pal) {
        for (int r = 0; r < L; ++r)
            for (int j = 0; j < N; ++j) b[(size_t)r * N + j] *= sw[r];
        average_prev_field_line(a, L, N);
        average_prev_field_line(b, L, N);
        U.swap(a);
        V.swap(b);
    } else {
        std::vector<double> I2 = b, Q2 = a;
        lowpass(I2, L, N, S.c_bw0, fs, 2);
        lowpass(Q2, L, N, S.c_bw1, fs, 2);
        for (size_t i = 0; i < M; ++i) {               // (I, Q) @ rot(33)
            U[i] = I2[i] * c33 + Q2[i] * s33;
            V[i] = -I2[i] * s33 + Q2[i] * c33;
        }
    }
}

}  // namespace

void analog_apply(std::vector<float>& img, int w, int h, int64_t frame, const AnalogParams& p) {
    if (p.standard == Standard::Off) return;
    if (p.use_gpu) {
        Timer tg;
        const bool ok = gpu::analog_apply(img, w, h, frame, p);
        if (log_enabled()) log("  analogique sur la carte graphique : %s (%.1f ms)", ok ? "oui" : "non (processeur)", tg.total_ms());
        if (ok) return;
    }
    const bool pal = p.standard == Standard::PAL;
    const Std& S = pal ? PAL : NTSC;
    const double fs = 4.0 * S.fsc;
    const int L = S.lines, N = (int)std::nearbyint(S.active_us * 1e-6 * fs);
    const double par = (4.0 / 3.0) / ((double)N / L);
    int hr, wr;
    sensor_grid(N, L, par, 1.0, h, w, &hr, &wr);
    if (p.anamorphic) { hr = L; wr = N; }
    const int y0 = (L - hr) / 2, x0 = (N - wr) / 2;
    Timer tm;
    // Signal video : BT.709, Y'UV, trame.
    std::vector<double> rgb((size_t)w * h * 3);
#pragma omp parallel for
    for (long long i = 0; i < (long long)w * h; ++i) {
        double a[3] = {img[i * 3], img[i * 3 + 1], img[i * 3 + 2]}, r[3];
        mat3(AP1_TO_REC709, a, r);
        for (int k = 0; k < 3; ++k) rgb[i * 3 + k] = enc709(std::clamp(r[k], 0.0, 1.0));
    }
    tm.lap("analogique : conversion d'entree");
    std::vector<double> small = resize_cv_area(rgb, w, h, 3, wr, hr);
    tm.lap("analogique : reduction a la trame");
    const size_t M = (size_t)L * N;
    std::vector<double> Y(M, 0.0), U(M, 0.0), V(M, 0.0);
    for (int r = 0; r < hr; ++r)
        for (int c = 0; c < wr; ++c) {
            const double* s = &small[((size_t)r * wr + c) * 3];
            size_t i = (size_t)(y0 + r) * N + (x0 + c);
            Y[i] = YUV[0] * s[0] + YUV[1] * s[1] + YUV[2] * s[2];
            U[i] = YUV[3] * s[0] + YUV[4] * s[1] + YUV[5] * s[2];
            V[i] = YUV[6] * s[0] + YUV[7] * s[1] + YUV[8] * s[2];
        }
    Rng rng(frame_seed(p, frame));
    if (p.tape != Tape::None)
        for (int g = 0; g < std::max(1, p.generations); ++g) {
            const TapePlan plan = plan_tape(L, N, fs, p, TAPES[(int)p.tape], rng, g);
            tape_pass(Y, U, V, L, N, fs, p, TAPES[(int)p.tape], plan);
        }
    tm.lap("analogique : bande (et mise en trame)");
    if (p.composite) composite_pass(Y, U, V, L, N, fs, S, pal, p, rng, frame);
    if (p.composite) tm.lap("analogique : composite");
    double inv[9];
    inv3(YUV, inv);
    std::vector<double> crop((size_t)hr * wr * 3);
    for (int r = 0; r < hr; ++r)
        for (int c = 0; c < wr; ++c) {
            size_t i = (size_t)(y0 + r) * N + (x0 + c);
            double yuv[3] = {Y[i], U[i], V[i]}, o[3];
            mat3(inv, yuv, o);
            for (int k = 0; k < 3; ++k) crop[((size_t)r * wr + c) * 3 + k] = o[k];
        }
    tm.lap("analogique : sortie de trame");
    std::vector<double> big = resize_linear(crop, wr, hr, 3, w, h);
    tm.lap("analogique : agrandissement");
#pragma omp parallel for
    for (long long i = 0; i < (long long)w * h; ++i) {
        double lin[3], o[3];
        for (int k = 0; k < 3; ++k) lin[k] = dec709(std::clamp(big[i * 3 + k], 0.0, 1.2));
        mat3(REC709_TO_AP1, lin, o);
        for (int k = 0; k < 3; ++k) img[i * 3 + k] = (float)o[k];
    }
    tm.lap("analogique : conversion de sortie");
}

}  // namespace camsim
