// CameraSim, coeur natif. Chaque etape suit engine/camsim (sensor.py, ccd.py, faults.py, isp.py,
// camcorder.py, filters.py, spectral.py), dans le meme ordre et avec les memes formules. Seul le
// generateur de hasard differe : ici un generateur "a compteur" (une valeur par photosite, par image et
// par usage), pour que le resultat ne depende pas de l'ordre de calcul des images ni des fils d'execution.
#define _USE_MATH_DEFINES
#include "camsim.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>

#include "grid.h"
#include "log.h"
#include "../gpu/gpu.h"
#include "../gpu/gpu_params.h"
#include "profiles_data.h"
#include "util.h"

namespace camsim {

namespace {


// ------------------------------------------------------------------ hasard reproductible
inline uint64_t mix(uint64_t z) {
    z += 0x9E3779B97F4A7C15ULL;
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}

inline uint64_t key(uint32_t seed, uint32_t stream, int64_t frame, int64_t index) {
    return mix(mix(mix((uint64_t)seed * 0x100000001B3ULL + stream) ^ (uint64_t)frame) ^ (uint64_t)index);
}

inline double u01(uint64_t h) {
    double u = (double)(h >> 11) * (1.0 / 9007199254740992.0);
    return std::min(std::max(u, 1e-12), 1.0 - 1e-12);
}

inline double normal(uint64_t h) {           // Box-Muller sur deux valeurs tirees du meme compteur
    double a = u01(mix(h)), b = u01(mix(h ^ 0xD1B54A32D192ED03ULL));
    return std::sqrt(-2.0 * std::log(a)) * std::cos(6.283185307179586 * b);
}

inline double poisson(double lam, uint64_t h) {
    if (lam <= 0.0) return 0.0;
    if (lam < 30.0) {                          // inversion exacte
        double u = u01(mix(h)), p = std::exp(-lam), c = p;
        double k = 0.0;
        while (u > c && k < 1000.0) {
            k += 1.0;
            p *= lam / k;
            c += p;
        }
        return k;
    }
    return std::max(0.0, std::floor(lam + std::sqrt(lam) * normal(h) + 0.5));   // approximation normale
}

enum Stream : uint32_t { PRNU = 1, DSNU = 2, HOT = 3, DEAD = 4, STUCK = 5, COLOFF = 6, COLGAIN = 7, DEADLINE = 8, DNL = 9,
                         SHOT = 10, READ = 11, ROW = 12, ROWADDR = 13, COLADDR = 14, SPARKLE = 15, LINESHIFT = 16,
                         SMEAR = 17, BLOCKED = 18 };

// Flux propres a un capteur (trois capteurs) et a une trame (lecture entrelacee).
inline uint32_t fixed_stream(uint32_t s, int chip) { return s + 100u * (uint32_t)chip; }
inline uint32_t frame_stream(uint32_t s, int chip, int field) { return s + 100u * (uint32_t)chip + 1000u * (uint32_t)field; }

// ------------------------------------------------------------------ physique du silicium
double dark_factor(double tc, double ref = 20.0) {
    auto f = [](double c) {
        double T = c + 273.15;
        double eg = 1.1557 - 7.021e-4 * T * T / (T + 1108.0);
        return std::pow(T, 1.5) * std::exp(-eg / (2.0 * 8.617333262e-5 * T));
    };
    return f(tc) / f(ref);
}

inline int reflect(int i, int n) {             // comme numpy "reflect" : -1 -> 1, n -> n-2
    if (n == 1) return 0;
    while (i < 0 || i >= n) i = (i < 0) ? -i : 2 * (n - 1) - i;
    return i;
}

// ------------------------------------------------------------------ filtres optiques
// Filtre passe-bas optique a 4 points : reponse cos(pi d f) sur chaque axe (filters.optical_low_pass).
std::vector<double> olpf_kernel(double d) {
    const int N = 256, R = 31;
    std::vector<double> k(2 * R + 1, 0.0);
    for (int n = -R; n <= R; ++n) {
        double acc = 0.0;
        for (int j = 0; j < N; ++j) {
            double f = (j <= N / 2 ? j : j - N) / (double)N;
            acc += std::cos(M_PI * d * f) * std::cos(2.0 * M_PI * f * n);
        }
        k[n + R] = acc / N;
    }
    return k;
}

void filter_axis(std::vector<float>& img, int w, int h, int ch, const std::vector<double>& k, bool horizontal) {
    if (k.size() <= 1) return;
    const int R = (int)k.size() / 2, T = 2 * R + 1;
    const size_t rowlen = (size_t)w * ch;
    std::vector<float> out(img.size());
    if (horizontal) {
        // Ligne recopiee avec ses bords en miroir, puis convolution contigue.
#pragma omp parallel
        {
            std::vector<double> pad((size_t)(w + 2 * R) * ch);
#pragma omp for
            for (int y = 0; y < h; ++y) {
                const float* src = &img[(size_t)y * rowlen];
                for (int x = -R; x < w + R; ++x) {
                    const float* p = src + (size_t)reflect(x, w) * ch;
                    for (int c = 0; c < ch; ++c) pad[(size_t)(x + R) * ch + c] = p[c];
                }
                float* dst = &out[(size_t)y * rowlen];
                for (int x = 0; x < w; ++x)
                    for (int c = 0; c < ch; ++c) {
                        const double* q = &pad[(size_t)x * ch + c];
                        double acc = 0.0;
                        for (int t = 0; t < T; ++t) acc += k[t] * q[(size_t)t * ch];
                        dst[(size_t)x * ch + c] = (float)std::max(acc, 0.0);   // la lumiere reste positive
                    }
            }
        }
    } else {
        // Lignes entieres additionnees : acces contigus.
#pragma omp parallel
        {
            std::vector<double> acc(rowlen);
#pragma omp for
            for (int y = 0; y < h; ++y) {
                std::fill(acc.begin(), acc.end(), 0.0);
                for (int t = 0; t < T; ++t) {
                    const float* src = &img[(size_t)reflect(y + t - R, h) * rowlen];
                    const double kt = k[t];
                    for (size_t j = 0; j < rowlen; ++j) acc[j] += kt * src[j];
                }
                float* dst = &out[(size_t)y * rowlen];
                for (size_t j = 0; j < rowlen; ++j) dst[j] = (float)std::max(acc[j], 0.0);
            }
        }
    }
    img.swap(out);
}

// Decalage sous-pixel d'un plan par la phase de Fourier, bords en miroir sur 16 pixels (filters.subpixel_shift).
void subpixel_shift(std::vector<float>& img, int w, int h, int ch, int c, double dx, double dy) {
    if (dx == 0 && dy == 0) return;
    const int pad = 16, W = w + 2 * pad, H = h + 2 * pad;
    std::vector<cplx> a((size_t)W * H);
    for (int y = 0; y < H; ++y)
        for (int x = 0; x < W; ++x) a[(size_t)y * W + x] = img[((size_t)reflect(y - pad, h) * w + reflect(x - pad, w)) * ch + c];
    // Meme calcul que numpy rfft2 / irfft2 : demi-spectre en x (frequences 0..W/2 positives),
    // spectre complet en y (frequences signees, Nyquist a -0,5), puis reconstruction reelle en x.
    FFT fx(W), fy(H);
    const int K = W / 2 + 1;
    std::vector<cplx> row(W), col(H), half((size_t)H * K);
    for (int y = 0; y < H; ++y) {
        std::copy(a.begin() + (size_t)y * W, a.begin() + (size_t)(y + 1) * W, row.begin());
        fx.forward(row);
        for (int k = 0; k < K; ++k) half[(size_t)y * K + k] = row[k];
    }
    for (int k = 0; k < K; ++k) {
        for (int y = 0; y < H; ++y) col[y] = half[(size_t)y * K + k];
        fy.forward(col);
        const double fxv = k / (double)W;
        for (int y = 0; y < H; ++y) {
            double fyv = (y < (H + 1) / 2 ? y : y - H) / (double)H;
            double ang = -2 * M_PI * (fxv * dx + fyv * dy);
            col[y] *= cplx(std::cos(ang), std::sin(ang));
        }
        fy.inverse(col);
        for (int y = 0; y < H; ++y) half[(size_t)y * K + k] = col[y];
    }
    for (int y = 0; y < H; ++y) {
        for (int k = 0; k < K; ++k) row[k] = half[(size_t)y * K + k];
        row[0] = cplx(row[0].real(), 0.0);
        if (W % 2 == 0) row[W / 2] = cplx(row[W / 2].real(), 0.0);
        for (int k = 1; k < (W + 1) / 2; ++k) row[W - k] = std::conj(row[k]);
        fx.inverse(row);
        std::copy(row.begin(), row.end(), a.begin() + (size_t)y * W);
    }
    for (int y = 0; y < h; ++y)
        for (int x = 0; x < w; ++x) img[((size_t)y * w + x) * ch + c] = (float)std::max(a[(size_t)(y + pad) * W + x + pad].real(), 0.0);
}

// ------------------------------------------------------------------ CCD (ccd.py)
void blooming(std::vector<double>& e, int h, int w, double fw, double antibloom) {
    const double lim = fw * std::max(antibloom, 1.0);
    std::vector<int> cols;
    for (int x = 0; x < w; ++x)
        for (int y = 0; y < h; ++y)
            if (e[(size_t)y * w + x] - lim > 0) { cols.push_back(x); break; }
    for (int x : cols) {
        std::vector<double> sub(h), up(h), down(h);
        for (int y = 0; y < h; ++y) {
            double v = e[(size_t)y * w + x];
            double ex = std::max(v - lim, 0.0);
            sub[y] = std::min(v, fw);
            up[y] = down[y] = 0.5 * ex;
        }
        for (int it = 0; it < std::max(400, h); ++it) {
            double mu = *std::max_element(up.begin(), up.end()), md = *std::max_element(down.begin(), down.end());
            if (mu <= 1e-6 && md <= 1e-6) break;
            std::vector<double> moved(h, 0.0);
            for (int y = 0; y + 1 < h; ++y) moved[y] = up[y + 1];
            for (int y = 0; y < h; ++y) { double take = std::min(moved[y], fw - sub[y]); sub[y] += take; up[y] = moved[y] - take; }
            std::fill(moved.begin(), moved.end(), 0.0);
            for (int y = 1; y < h; ++y) moved[y] = down[y - 1];
            for (int y = 0; y < h; ++y) { double take = std::min(moved[y], fw - sub[y]); sub[y] += take; down[y] = moved[y] - take; }
        }
        for (int y = 0; y < h; ++y) e[(size_t)y * w + x] = sub[y];
    }
    for (auto& v : e) v = std::min(v, fw);
}

void transfer_inefficiency(std::vector<double>& e, int h, int w, double eps_v, double eps_h, int terms = 5) {
    for (int axis = 0; axis < 2; ++axis) {
        const double eps = axis == 0 ? eps_v : eps_h;
        if (eps <= 0) continue;
        std::vector<double> acc(e.size(), 0.0);
        for (int y = 0; y < h; ++y)
            for (int x = 0; x < w; ++x) {
                const double lam = ((axis == 0 ? y : x) + 1) * eps;
                double wgt = std::exp(-lam);
                const double v = e[(size_t)y * w + x];
                for (int k = 0; k <= terms; ++k) {
                    int ty = axis == 0 ? y + k : y, tx = axis == 0 ? x : x + k;
                    if (ty < h && tx < w) acc[(size_t)ty * w + tx] += v * wgt;
                    wgt *= lam / (k + 1);
                }
            }
        e.swap(acc);
    }
}

// ------------------------------------------------------------------ dematricage (isp.py)
const double K_GR_GB[25] = {0, 0, -1, 0, 0, 0, 0, 2, 0, 0, -1, 2, 4, 2, -1, 0, 0, 2, 0, 0, 0, 0, -1, 0, 0};
const double K_RG_RB[25] = {0, 0, 0.5, 0, 0, 0, -1, 0, -1, 0, -1, 4, 5, 4, -1, 0, -1, 0, -1, 0, 0, 0, 0.5, 0, 0};
const double K_RB_BB[25] = {0, 0, -1.5, 0, 0, 0, 2, 0, 2, 0, -1.5, 0, 6, 0, -1.5, 0, 2, 0, 2, 0, 0, 0, -1.5, 0, 0};

inline double conv5(const std::vector<double>& m, int w, int h, int x, int y, const double* k, bool transpose) {
    double acc = 0.0;
    for (int i = 0; i < 5; ++i)
        for (int j = 0; j < 5; ++j) {
            double c = transpose ? k[j * 5 + i] : k[i * 5 + j];
            if (c != 0.0) acc += c * m[(size_t)reflect(y + i - 2, h) * w + reflect(x + j - 2, w)];
        }
    return acc / 8.0;
}

inline double conv3(const std::vector<double>& m, int w, int h, int x, int y, const double* k) {
    double acc = 0.0;
    for (int i = 0; i < 3; ++i)
        for (int j = 0; j < 3; ++j) acc += k[i * 3 + j] * m[(size_t)reflect(y + i - 1, h) * w + reflect(x + j - 1, w)];
    return acc;
}

// couleur du photosite : 0 = R, 1 = G (ligne R), 2 = G (ligne B), 3 = B, motif RGGB
inline int bayer(int gx, int gy) { return ((gy & 1) << 1) | (gx & 1); }

// ------------------------------------------------------------------ constantes d'une image
struct Ctx {
    const Profile* P;
    double span, K, read_gauss, read_tukey, row_sigma, lam, cbias[4], full_well, dn_per, scale;
    double prnu, dsnu, dark, dark_ref, sg, shade;
    int native, bits;
    double step, top, max_code;
    std::vector<double> thresholds;
};

// ------------------------------------------------------------------ exposition d'un plan (sensor._expose_plane)
// e : charges attendues (h x w). bin_offset < 0 : pas de lecture entrelacee. Renvoie le brut (DN) et la hauteur.
// Photosites de meme couleur regroupes pour chaque sortie, le long d'un axe (video._same_colour_indices).
std::vector<std::vector<int>> same_colour_groups(int length, int n) {
    std::vector<std::vector<int>> out;
    if (n <= 1) return out;
    int count = (n % 2 == 1) ? (length - 1) / n + 1 : 2 * (length / (2 * n)) + std::min(length % (2 * n), 2);
    for (int k = 0; k < count; ++k) {
        std::vector<int> g(n);
        for (int m = 0; m < n; ++m) g[m] = (n % 2 == 1) ? n * k + 2 * (m - (n - 1) / 2) : 2 * n * (k / 2) + (k % 2) + 2 * m;
        const int par = k % 2;
        const int high = ((length - 1) % 2 == par) ? length - 1 : length - 2;
        for (int& v : g) v = v < 0 ? par : (v > length - 1 ? high : v);
        out.push_back(g);
    }
    return out;
}

std::vector<double> expose_plane(const Ctx& C, const Settings& s, int64_t frame, std::vector<double> e, int h, int w,
                                 int chip, int field, bool is_bayer, int bin_offset, int x0, int y0, int* h_out,
                                 int bin_v = 1, int bin_h = 1, int* w_out = nullptr) {
    const Profile& P = *C.P;
    size_t N0 = (size_t)h * w;
    // 1-2. Non-uniformite, ombrage, courant d'obscurite.
    const double sy = std::max((h - 1) / 2.0, 1.0), sx = std::max((w - 1) / 2.0, 1.0);
#pragma omp parallel for
    for (int y = 0; y < h; ++y)
        for (int x = 0; x < w; ++x) {
            const int64_t gi = (int64_t)(y0 + y) * 1000003 + (x0 + x);
            size_t i = (size_t)y * w + x;
            double v = e[i];
            if (C.prnu > 0) v *= 1.0 + C.prnu * normal(key(s.seed, fixed_stream(PRNU, chip), 0, gi));
            if (C.shade > 0) {
                double yy = (y - (h - 1) / 2.0) / sy, xx = (x - (w - 1) / 2.0) / sx;
                v *= 1.0 - C.shade * 0.5 * (yy * yy + xx * xx);
            }
            v = std::max(v, 0.0);
            if (C.dark_ref > 0) {
                double dm = (C.dsnu > 0) ? std::exp(-0.5 * C.sg * C.sg + C.sg * normal(key(s.seed, fixed_stream(DSNU, chip), 0, gi))) : 1.0;
                if (s.hot_pixel_fraction > 0 && u01(mix(key(s.seed, fixed_stream(HOT, chip), 0, gi))) < s.hot_pixel_fraction)
                    dm *= s.hot_pixel_gain * u01(mix(key(s.seed, fixed_stream(HOT, chip), 1, gi)));
                v += C.dark * dm;
            }
            e[i] = v;
        }
    // 2b. Lecture video : regroupement de photosites de meme couleur. Chaque photosite sature d'abord seul ;
    //     la somme de n lois de Poisson est une loi de Poisson de la somme (le bruit est tire sur la somme).
    int n_bin = 1;
    if (bin_v * bin_h > 1) {
        if (s.clip_full_well)
            for (auto& v : e) v = std::min(v, C.full_well);
        std::vector<std::vector<int>> gh = same_colour_groups(w, bin_h), gv = same_colour_groups(h, bin_v);
        const int nw = bin_h > 1 ? (int)gh.size() : w, nh = bin_v > 1 ? (int)gv.size() : h;
        std::vector<double> tmp((size_t)h * nw, 0.0), out((size_t)nh * nw, 0.0);
        for (int y = 0; y < h; ++y)
            for (int x = 0; x < nw; ++x) {
                if (bin_h > 1) for (int c : gh[x]) tmp[(size_t)y * nw + x] += e[(size_t)y * w + c];
                else tmp[(size_t)y * nw + x] = e[(size_t)y * w + x];
            }
        for (int y = 0; y < nh; ++y)
            for (int x = 0; x < nw; ++x) {
                if (bin_v > 1) for (int r : gv[y]) out[(size_t)y * nw + x] += tmp[(size_t)r * nw + x];
                else out[(size_t)y * nw + x] = tmp[(size_t)y * nw + x];
            }
        e.swap(out);
        h = nh;
        w = nw;
        N0 = (size_t)h * w;
        n_bin = bin_v * bin_h;
        chip += 50;
    }
    const std::vector<double> expected = P.is_ccd ? e : std::vector<double>();
    // 3. Photons.
    if (s.shot_noise) {
#pragma omp parallel for
        for (int y = 0; y < h; ++y)
            for (int x = 0; x < w; ++x) {
                const int64_t gi = (int64_t)(y0 + y) * 1000003 + (x0 + x);
                size_t i = (size_t)y * w + x;
                e[i] = poisson(e[i], key(s.seed, frame_stream(SHOT, chip, field), frame, gi));
            }
    }
    if (n_bin > 1)
        for (auto& v : e) v /= n_bin;
    double K = C.K;
    int hh = h;
    if (P.is_ccd) {
        // 4. CCD : eblouissement, trainee verticale, colonnes bloquees, transfert imparfait.
        if (s.clip_full_well) blooming(e, h, w, C.full_well, s.ccd_antiblooming >= 0 ? s.ccd_antiblooming : P.antiblooming);
        const double sf = P.smear_fraction * s.ccd_smear_gain;
        if (sf > 0) {
            const double k = 10.0 * sf * P.smear_ref_s / std::max(s.exposure_time_s, 1e-9);
            for (int x = 0; x < w; ++x) {
                double m = 0;
                for (int y = 0; y < h; ++y) m += expected[(size_t)y * w + x];
                const double col = k * m / h;
                for (int y = 0; y < h; ++y) {
                    const int64_t gi = (int64_t)(y0 + y) * 1000003 + (x0 + x);
                    e[(size_t)y * w + x] += s.shot_noise ? poisson(col, key(s.seed, frame_stream(SMEAR, chip, field), frame, gi)) : col;
                }
            }
        }
        for (int b = 0; b < s.ccd_blocked_columns; ++b) {
            uint64_t hk = key(s.seed, fixed_stream(BLOCKED, chip), 0, b);
            int bx = (int)(u01(mix(hk)) * w) % w, by = (int)(u01(mix(hk ^ 0x21)) * std::max(h / 2, 1));
            for (int y = by; y < h; ++y) e[(size_t)y * w + bx] = 0.0;
        }
        const double ev = s.ccd_cti_v >= 0 ? s.ccd_cti_v : P.cti, eh = s.ccd_cti_h >= 0 ? s.ccd_cti_h : P.cti;
        if (ev > 0 || eh > 0) transfer_inefficiency(e, h, w, ev, eh);
        if (bin_offset >= 0) {                        // deux lignes additionnees dans le registre vertical
            int rows = h - bin_offset;
            if (rows % 2) rows += 1;
            hh = rows / 2;
            std::vector<double> b((size_t)hh * w);
            for (int r = 0; r < hh; ++r) {
                int ya = std::min(bin_offset + 2 * r, h - 1), yb = std::min(bin_offset + 2 * r + 1, h - 1);
                for (int x = 0; x < w; ++x) b[(size_t)r * w + x] = e[(size_t)ya * w + x] + e[(size_t)yb * w + x];
            }
            e.swap(b);
            K = K / 2.0;
        }
    } else if (s.clip_full_well) {
        for (auto& v : e) v = std::min(v, C.full_well);
    }
    h = hh;
    const size_t N = (size_t)h * w;
    (void)N0;
    // Ligne de photosites d'origine de chaque ligne lue (pour les defauts figes).
    auto src_row = [&](int r) { return bin_offset >= 0 ? std::min(bin_offset + 2 * r, (int)(N0 / w) - 1) : r; };

    // Defauts figes des colonnes et des lignes.
    std::vector<double> col_off, col_gain;
    if (s.column_fpn_dn > 0) {
        col_off.resize(w);
        for (int x = 0; x < w; ++x) col_off[x] = s.column_fpn_dn * normal(key(s.seed, fixed_stream(COLOFF, chip), 0, x0 + x));
    }
    if (s.column_gain_sigma > 0) {
        col_gain.resize(w);
        for (int x = 0; x < w; ++x) col_gain[x] = 1.0 + s.column_gain_sigma * normal(key(s.seed, fixed_stream(COLGAIN, chip), 0, x0 + x));
    }
    const int h_src = (int)(N0 / w);
    std::vector<int8_t> st_col(w, 0), st_row(h_src, 0);       // 1 = noire, 2 = blanche
    for (int k = 0; k < s.dead_columns; ++k) {
        uint64_t hk = key(s.seed, fixed_stream(DEADLINE, chip), 0, k);
        st_col[(int)(u01(mix(hk)) * w) % w] = (u01(mix(hk ^ 0x55)) < 0.3) ? 2 : 1;
    }
    for (int k = 0; k < s.dead_rows; ++k) {
        uint64_t hk = key(s.seed, fixed_stream(DEADLINE, chip), 1, k);
        st_row[(int)(u01(mix(hk)) * h_src) % h_src] = (u01(mix(hk ^ 0x55)) < 0.3) ? 2 : 1;
    }

    // 5. Charges -> DN (gain de chaque colonne), niveau noir, biais par voie.
    std::vector<double> dn(N);
#pragma omp parallel for
    for (int y = 0; y < h; ++y)
        for (int x = 0; x < w; ++x) {
            size_t i = (size_t)y * w + x;
            double v = K * e[i];
            if (!col_gain.empty()) v *= col_gain[x];
            v += P.black;
            if (is_bayer) {
                const int cfa = bayer(x0 + x, y0 + y);
                v += C.cbias[(cfa == 0) ? 0 : (cfa == 1 ? 1 : (cfa == 3 ? 2 : 3))];   // ordre ELD : R, G1, B, G2
            }
            dn[i] = v;
        }

    // 5b. Pannes d'adressage.
    auto address_map = [&](int n, int bit, int val, double rate, uint32_t stream) {
        std::vector<int> idx(n);
        int nbits = std::max((int)std::ceil(std::log2((double)n)), 1);
        for (int k = 0; k < n; ++k) {
            long long a = k;
            if (bit >= 0) a = val ? (a | (1LL << bit)) : (a & ~(1LL << bit));
            if (rate > 0) {
                uint64_t hk = key(s.seed, frame_stream(stream, chip, field), frame, k);
                if (u01(mix(hk)) < rate) a ^= (1LL << ((int)(u01(mix(hk ^ 0x77)) * nbits) % nbits));
            }
            idx[k] = (a < n && a >= 0) ? (int)a : -1;
        }
        return idx;
    };
    const bool use_r = s.row_address_bit >= 0 || s.row_address_error_rate > 0;
    const bool use_c = s.col_address_bit >= 0 || s.col_address_error_rate > 0;
    if (use_r || use_c) {
        std::vector<int> rm = address_map(h, s.row_address_bit, s.row_address_value, s.row_address_error_rate, ROWADDR);
        std::vector<int> cm = address_map(w, s.col_address_bit, s.col_address_value, s.col_address_error_rate, COLADDR);
        std::vector<double> tmp(N);
        for (int y = 0; y < h; ++y)
            for (int x = 0; x < w; ++x) {
                int ry = use_r ? rm[y] : y;
                tmp[(size_t)y * w + x] = (ry >= 0) ? dn[(size_t)ry * w + x] : P.black;
            }
        for (int y = 0; y < h; ++y)
            for (int x = 0; x < w; ++x) {
                int cx = use_c ? cm[x] : x;
                dn[(size_t)y * w + x] = (cx >= 0) ? tmp[(size_t)y * w + cx] : P.black;
            }
    }

    // 5c-8. Pixels morts ou colles, bruit de lecture, bruit de ligne, colonnes, parasite, convertisseur.
    const ReadModel model = (P.is_ccd && s.read_model == ReadModel::Tukey) ? ReadModel::Gauss : s.read_model;
    const double istart = std::fmod((double)frame * h * w, std::max(s.interference_period_px, 1e-9));
#pragma omp parallel for
    for (int y = 0; y < h; ++y) {
        const int sr = src_row(y);
        const double row = (s.row_noise_gain > 0) ? s.row_noise_gain * C.row_sigma * normal(key(s.seed, frame_stream(ROW, chip, field), frame, y0 + y)) : 0.0;
        for (int x = 0; x < w; ++x) {
            const int64_t gi = (int64_t)(y0 + y) * 1000003 + (x0 + x);
            const int64_t gs = (int64_t)(y0 + sr) * 1000003 + (x0 + x);
            size_t i = (size_t)y * w + x;
            double v = dn[i];
            int state = std::max(st_col[x], st_row[sr]);
            if (state == 0 && s.dead_pixel_fraction > 0 && u01(mix(key(s.seed, fixed_stream(DEAD, chip), 0, gs))) < s.dead_pixel_fraction) state = 1;
            if (s.stuck_pixel_fraction > 0 && u01(mix(key(s.seed, fixed_stream(STUCK, chip), 0, gs))) < s.stuck_pixel_fraction) state = 2;
            if (st_col[x] == 2 || st_row[sr] == 2) state = 2;
            if (state == 1) v = P.black;
            else if (state == 2) v = P.white;
            if (s.read_noise_gain > 0 && model != ReadModel::Off) {
                uint64_t hk = key(s.seed, frame_stream(READ, chip, field), frame, gi);
                if (model == ReadModel::Tukey) {
                    double u = u01(mix(hk));
                    double q = (std::fabs(C.lam) < 1e-9) ? std::log(u) - std::log1p(-u) : (std::pow(u, C.lam) - std::pow(1.0 - u, C.lam)) / C.lam;
                    v += s.read_noise_gain * C.read_tukey * q;
                } else {
                    v += s.read_noise_gain * C.read_gauss * normal(hk);
                }
            }
            v += row;
            if (!col_off.empty()) v += col_off[x];
            if (s.interference_dn > 0)
                v += s.interference_dn * std::sin(2.0 * M_PI * (istart + (double)y * w + x) / s.interference_period_px);
            if (s.quantize) {
                double xq = v / C.step;
                if (s.adc_inl_lsb) xq += s.adc_inl_lsb * std::sin(M_PI * std::clamp(xq / C.top, 0.0, 1.0));
                double code = !C.thresholds.empty()
                    ? (double)(std::upper_bound(C.thresholds.begin(), C.thresholds.end(), xq) - C.thresholds.begin())
                    : std::nearbyint(xq);
                v = std::clamp(code, 0.0, C.top) * C.step;
                long long iv = (long long)v;
                if (s.adc_stuck_bit >= 0) iv = s.adc_stuck_value ? (iv | (1LL << s.adc_stuck_bit)) : (iv & ~(1LL << s.adc_stuck_bit));
                if (s.adc_sparkle_rate > 0) {
                    uint64_t hk = key(s.seed, frame_stream(SPARKLE, chip, field), frame, gi);
                    if (u01(mix(hk)) < s.adc_sparkle_rate) iv ^= (1LL << ((int)(u01(mix(hk ^ 0x99)) * C.native) % C.native));
                }
                v = (double)iv;
            }
            dn[i] = v;
        }
    }

    // 9. Liaison serie, puis limites du convertisseur.
    if (s.quantize) {
        const int native = C.native;
        const int nb = ((s.bitslip % native) + native) % native;
        if (nb) {
            const long long mask = (1LL << native) - 1;
            std::vector<double> tmp(dn);
            for (int y = 0; y < h; ++y)
                for (int x = 0; x < w; ++x) {
                    long long v = (long long)tmp[(size_t)y * w + x];
                    long long nx = (x + 1 < w) ? (long long)tmp[(size_t)y * w + x + 1] : 0;
                    dn[(size_t)y * w + x] = (double)(((v << nb) & mask) | (nx >> (native - nb)));
                }
        }
        if (s.line_shift_rate > 0 && s.line_shift_max_px > 0) {
            std::vector<double> src(dn);
            long long acc = 0;
            for (int y = 0; y < h; ++y) {
                uint64_t hk = key(s.seed, frame_stream(LINESHIFT, chip, field), frame, y0 + y);
                long long slip = 0;
                if (u01(mix(hk)) < s.line_shift_rate)
                    slip = (long long)(u01(mix(hk ^ 0x33)) * (2 * s.line_shift_max_px + 1)) - s.line_shift_max_px;
                acc += slip;
                long long off = s.line_shift_persist ? acc : slip;
                if (!off) continue;
                long long k = s.line_shift_persist ? ((off % w) + w) % w : off;
                for (int x = 0; x < w; ++x) {
                    long long sxx = x - k;
                    dn[(size_t)y * w + x] = s.line_shift_persist ? src[(size_t)y * w + (((sxx % w) + w) % w)]
                                                                 : ((sxx >= 0 && sxx < w) ? src[(size_t)y * w + sxx] : P.black);
                }
            }
        }
        for (auto& v : dn) v = std::clamp(v, 0.0, C.max_code);
    }
    *h_out = h;
    if (w_out) *w_out = w;
    return dn;
}

// ------------------------------------------------------------------ camescope (camcorder.py)
// Mosaique complementaire Sony ICX409AK : Cy Ye / G Mg / Cy Ye / Mg G.
const int CMYG[4][2] = {{0, 1}, {2, 3}, {0, 1}, {3, 2}};          // 0 Cy, 1 Ye, 2 G, 3 Mg
const double CMYG_F[4][3] = {{0, 1, 1}, {1, 1, 0}, {0, 1, 0}, {1, 0, 1}};

inline int line_type(int r) { return (r % 4) < 2 ? 1 : 0; }      // 1 = ligne "rouge" (Cy+G), 0 = "bleue"

std::vector<double> hfilter(const std::vector<double>& x, int h, int w, const std::vector<double>& k) {
    const int R = (int)k.size() / 2;
    std::vector<double> out(x.size(), 0.0);
#pragma omp parallel for
    for (int y = 0; y < h; ++y)
        for (int c = 0; c < w; ++c) {
            double acc = 0;
            for (int t = -R; t <= R; ++t) acc += k[t + R] * x[(size_t)y * w + reflect(c + t, w)];
            out[(size_t)y * w + c] = acc;
        }
    return out;
}

std::vector<double> gaussian_kernel(double f3db) {
    if (f3db <= 0 || f3db >= 0.5) return {1.0};
    double sigma = std::sqrt(std::log(2.0) / 2.0) / (M_PI * f3db);
    int r = std::max(1, (int)std::ceil(3.0 * sigma));
    std::vector<double> k(2 * r + 1);
    double sum = 0;
    for (int i = -r; i <= r; ++i) sum += (k[i + r] = std::exp(-0.5 * (i / sigma) * (i / sigma)));
    for (auto& v : k) v /= sum;
    return k;
}

// Sommes de photosites -> RVB lineaire de la camera (h x w x 3).
std::vector<double> separate(const std::vector<double>& p, int h, int w, double f3db, bool vertical_2h, const double* white) {
    std::vector<double> ysum = hfilter(p, h, w, {0.5, 1.0, 0.5});
    std::vector<double> q(p.size());
    for (int y = 0; y < h; ++y)
        for (int x = 0; x < w; ++x) q[(size_t)y * w + x] = p[(size_t)y * w + x] * ((x % 2 == 0) ? 1.0 : -1.0);
    std::vector<double> d = hfilter(q, h, w, {0.5, 1.0, 0.5});
    const double rw = white[0], gw = white[1], bw = white[2];
    const double y_w = 2 * rw + 3 * gw + 2 * bw, cr_w = 2 * rw - gw, cb_w = 2 * bw - gw;
    std::vector<double> own(p.size());
    for (int y = 0; y < h; ++y) {
        const int t = line_type(y);
        const double ref = (t == 1 ? cr_w : cb_w) / y_w;
        for (int x = 0; x < w; ++x) {
            size_t i = (size_t)y * w + x;
            own[i] = (t == 1 ? -d[i] : d[i]) - ref * ysum[i];
        }
    }
    if (f3db > 0) own = hfilter(own, h, w, gaussian_kernel(f3db));
    std::vector<double> other(p.size());
    for (int y = 0; y < h; ++y)
        for (int x = 0; x < w; ++x) {
            double above = own[(size_t)(y >= 2 ? y - 2 : y) * w + x];
            double v = above;
            if (vertical_2h) v = 0.5 * (above + own[(size_t)(y < h - 2 ? y + 2 : y) * w + x]);
            other[(size_t)y * w + x] = v;
        }
    if (h >= 4)
        for (int y = 0; y < 2; ++y)
            for (int x = 0; x < w; ++x) other[(size_t)y * w + x] = own[(size_t)(y + 2) * w + x];
    std::vector<double> rgb(p.size() * 3);
    for (int y = 0; y < h; ++y) {
        const int t = line_type(y);
        for (int x = 0; x < w; ++x) {
            size_t i = (size_t)y * w + x;
            double cr = (t == 1 ? own[i] : other[i]) + cr_w / y_w * ysum[i];
            double cb = (t == 0 ? own[i] : other[i]) + cb_w / y_w * ysum[i];
            double g = (ysum[i] - cr - cb) / 5.0;
            rgb[i * 3] = 0.5 * (cr + g);
            rgb[i * 3 + 1] = g;
            rgb[i * 3 + 2] = 0.5 * (cb + g);
        }
    }
    return rgb;
}

// ------------------------------------------------------------------ carte graphique (native/gpu/kernels.cu)
// Chaine complete d'un appareil photo (mosaique de Bayer, sans lecture video) sur la carte graphique.
// Le processeur prepare les petites tables (defauts des colonnes et des lignes, pannes d'adressage, seuils du
// convertisseur, glissements de lignes), exactement comme expose_plane ; la carte fait le travail par photosite.
bool bayer_gpu(const Ctx& C, const Settings& s, int64_t frame, const float* rgb_in, int w, int h, int x0, int y0,
               bool spectral, const double* a2c, const double* wbp, const double* c2a, std::vector<float>& result) {
    if (!gpu::available()) return false;
    const Profile& P = *C.P;
    const size_t N = (size_t)w * h;
    GpuBayerParams G{};
    G.w = w; G.h = h; G.x0 = x0; G.y0 = y0; G.seed = s.seed; G.frame = frame;
    G.spectral = spectral ? 1 : 0;
    G.lut_size = P.lut_size;
    for (int k = 0; k < 9; ++k) { G.a2c[k] = a2c[k]; G.c2a[k] = c2a[k]; }
    G.scale = C.scale; G.prnu = C.prnu; G.shade = C.shade; G.dark_ref = C.dark_ref; G.dark = C.dark; G.dsnu = C.dsnu; G.sg = C.sg;
    G.hot_frac = s.hot_pixel_fraction; G.hot_gain = s.hot_pixel_gain;
    G.shot_noise = s.shot_noise; G.clip_full_well = s.clip_full_well; G.full_well = C.full_well; G.K = C.K;
    G.black = P.black; G.white = P.white;
    for (int c = 0; c < 4; ++c) G.cbias[c] = C.cbias[c];
    G.dead_frac = s.dead_pixel_fraction; G.stuck_frac = s.stuck_pixel_fraction;
    G.read_model = (int)s.read_model; G.read_gain = s.read_noise_gain; G.read_tukey = C.read_tukey; G.read_gauss = C.read_gauss; G.lam = C.lam;
    G.row_gain = s.row_noise_gain; G.row_sigma = C.row_sigma;
    G.interf_dn = s.interference_dn; G.interf_period = s.interference_period_px;
    G.istart = std::fmod((double)frame * h * w, std::max(s.interference_period_px, 1e-9));
    G.quantize = s.quantize; G.native = C.native; G.adc_stuck_bit = s.adc_stuck_bit; G.adc_stuck_value = s.adc_stuck_value;
    G.step = C.step; G.top = C.top; G.adc_inl = s.adc_inl_lsb; G.sparkle_rate = s.adc_sparkle_rate; G.max_code = C.max_code;
    G.n_thresholds = (int)C.thresholds.size();
    G.bitslip = s.quantize ? ((s.bitslip % C.native) + C.native) % C.native : 0;
    G.line_persist = s.line_shift_persist;
    G.span = C.span; G.isp_black_offset = s.isp_black_offset_dn;
    const double gw[4] = {wbp[0] * s.wb_red, wbp[1], wbp[1], wbp[2] * s.wb_blue};
    for (int c = 0; c < 4; ++c) G.gw[c] = gw[c];
    G.clip_highlights = s.clip_highlights; G.demosaic = (int)s.demosaic;
    G.out_scale = C.span / C.dn_per;

    // Tables preparees par le processeur (memes tirages que expose_plane).
    std::vector<double> col_off, col_gain;
    if (s.column_fpn_dn > 0) {
        col_off.resize(w);
        for (int x = 0; x < w; ++x) col_off[x] = s.column_fpn_dn * normal(key(s.seed, fixed_stream(COLOFF, 0), 0, x0 + x));
    }
    if (s.column_gain_sigma > 0) {
        col_gain.resize(w);
        for (int x = 0; x < w; ++x) col_gain[x] = 1.0 + s.column_gain_sigma * normal(key(s.seed, fixed_stream(COLGAIN, 0), 0, x0 + x));
    }
    G.has_col_gain = !col_gain.empty();
    G.has_col_off = !col_off.empty();
    std::vector<signed char> st_col(w, 0), st_row(h, 0);
    for (int k = 0; k < s.dead_columns; ++k) {
        uint64_t hk = key(s.seed, fixed_stream(DEADLINE, 0), 0, k);
        st_col[(int)(u01(mix(hk)) * w) % w] = (u01(mix(hk ^ 0x55)) < 0.3) ? 2 : 1;
    }
    for (int k = 0; k < s.dead_rows; ++k) {
        uint64_t hk = key(s.seed, fixed_stream(DEADLINE, 0), 1, k);
        st_row[(int)(u01(mix(hk)) * h) % h] = (u01(mix(hk ^ 0x55)) < 0.3) ? 2 : 1;
    }
    auto address_map = [&](int n, int bit, int val, double rate, uint32_t stream) {
        std::vector<int> idx(n);
        int nbits = std::max((int)std::ceil(std::log2((double)n)), 1);
        for (int k = 0; k < n; ++k) {
            long long a = k;
            if (bit >= 0) a = val ? (a | (1LL << bit)) : (a & ~(1LL << bit));
            if (rate > 0) {
                uint64_t hk = key(s.seed, frame_stream(stream, 0, 0), frame, k);
                if (u01(mix(hk)) < rate) a ^= (1LL << ((int)(u01(mix(hk ^ 0x77)) * nbits) % nbits));
            }
            idx[k] = (a < n && a >= 0) ? (int)a : -1;
        }
        return idx;
    };
    const bool use_r = s.row_address_bit >= 0 || s.row_address_error_rate > 0;
    const bool use_c = s.col_address_bit >= 0 || s.col_address_error_rate > 0;
    std::vector<int> rm, cm;
    if (use_r) rm = address_map(h, s.row_address_bit, s.row_address_value, s.row_address_error_rate, ROWADDR);
    if (use_c) cm = address_map(w, s.col_address_bit, s.col_address_value, s.col_address_error_rate, COLADDR);
    std::vector<long long> shift;
    if (s.quantize && s.line_shift_rate > 0 && s.line_shift_max_px > 0) {
        shift.assign(h, 0);
        long long acc = 0;
        for (int y = 0; y < h; ++y) {
            uint64_t hk = key(s.seed, frame_stream(LINESHIFT, 0, 0), frame, y0 + y);
            long long slip = 0;
            if (u01(mix(hk)) < s.line_shift_rate)
                slip = (long long)(u01(mix(hk ^ 0x33)) * (2 * s.line_shift_max_px + 1)) - s.line_shift_max_px;
            acc += slip;
            const long long off = s.line_shift_persist ? acc : slip;
            shift[y] = !off ? 0 : (s.line_shift_persist ? ((off % w) + w) % w : off);
        }
    }

    Timer tg;
    gpu::Session S;
    if (!S.ok()) return false;
    auto up = [&](const void* data, size_t bytes) {
        gpu::DevPtr p = S.alloc(bytes);
        if (p && bytes) S.upload(p, data, bytes);
        return p;
    };
    gpu::DevPtr d_rgb = up(rgb_in, N * 3 * sizeof(float));
    gpu::DevPtr d_cam = S.alloc(N * 3 * sizeof(float)), d_tmp = S.alloc(N * 3 * sizeof(float));
    gpu::DevPtr d_lut = spectral ? up(P.lut, (size_t)3 * P.lut_size * P.lut_size * 3 * sizeof(float)) : 0;
    gpu::DevPtr d_a = S.alloc(N * sizeof(double)), d_b = S.alloc(N * sizeof(double));
    gpu::DevPtr d_colg = col_gain.empty() ? 0 : up(col_gain.data(), w * sizeof(double));
    gpu::DevPtr d_colo = col_off.empty() ? 0 : up(col_off.data(), w * sizeof(double));
    gpu::DevPtr d_stc = up(st_col.data(), w), d_str = up(st_row.data(), h);
    gpu::DevPtr d_rm = rm.empty() ? 0 : up(rm.data(), h * sizeof(int)), d_cm = cm.empty() ? 0 : up(cm.data(), w * sizeof(int));
    gpu::DevPtr d_thr = C.thresholds.empty() ? 0 : up(C.thresholds.data(), C.thresholds.size() * sizeof(double));
    gpu::DevPtr d_shift = shift.empty() ? 0 : up(shift.data(), h * sizeof(long long));
    gpu::DevPtr d_out = S.alloc(N * 3 * sizeof(float));
    if (!S.ok()) return false;
    S.sync();
    tg.lap("carte : memoire et envoi");

    // 1. Scene -> camera ; 2. filtre passe-bas (horizontal puis vertical).
    { void* a[] = {&d_rgb, &d_cam, &d_lut, &G}; S.launch("k_scene_to_cam", N, a); }
    int ch = 3, horiz;
    for (int pass = 0; pass < 2; ++pass) {
        const double d = pass == 0 ? s.olpf_h_px : s.olpf_v_px;
        if (d <= 0) continue;
        const std::vector<double> k = olpf_kernel(d);
        int R = (int)k.size() / 2;
        gpu::DevPtr d_k = up(k.data(), k.size() * sizeof(double));
        horiz = pass == 0 ? 1 : 0;
        void* a[] = {&d_cam, &d_tmp, &w, &h, &ch, &d_k, &R, &horiz};
        S.launch("k_filter_axis", N * 3, a);
        std::swap(d_cam, d_tmp);
    }
    // 3-4. Capteur ; 5. traitement ; 6. dematricage.
    { void* a[] = {&d_cam, &d_a, &d_colg, &G}; S.launch("k_expose_a", N, a); }
    { void* a[] = {&d_a, &d_b, &d_rm, &d_cm, &d_stc, &d_str, &d_colo, &d_thr, &G}; S.launch("k_expose_b", N, a); }
    if (G.bitslip) { void* a[] = {&d_b, &d_a, &G}; S.launch("k_bitslip", N, a); std::swap(d_a, d_b); }
    if (d_shift) { void* a[] = {&d_b, &d_a, &d_shift, &G}; S.launch("k_lineshift", N, a); std::swap(d_a, d_b); }
    { void* a[] = {&d_b, &G}; S.launch("k_isp_prep", N, a); }
    { void* a[] = {&d_b, &d_out, &G}; S.launch("k_demosaic", N, a); }
    S.sync();
    tg.lap("carte : calcul");
    result.assign(N * 3, 0.0f);
    const bool ok = S.download(result.data(), d_out, N * 3 * sizeof(float));
    tg.lap("carte : retour");
    return ok;
}

}  // namespace

const Profile& get_profile(int i) { return *PROFILES[std::clamp(i, 0, PROFILE_COUNT - 1)]; }
int profile_count() { return PROFILE_COUNT; }
const char* profile_name(int i) { return PROFILES[std::clamp(i, 0, PROFILE_COUNT - 1)]->name; }
const char* profile_key(int i) { return PROFILES[std::clamp(i, 0, PROFILE_COUNT - 1)]->key; }
void profile_defaults(int i, double* prnu, double* dsnu, double* dark) {
    const Profile& P = *PROFILES[std::clamp(i, 0, PROFILE_COUNT - 1)];
    *prnu = P.prnu;
    *dsnu = P.dsnu;
    *dark = P.dark_e_per_s_20C;
}
int profile_kind(int i) { return PROFILES[std::clamp(i, 0, PROFILE_COUNT - 1)]->kind; }
bool profile_has_spectral(int i) { return PROFILES[std::clamp(i, 0, PROFILE_COUNT - 1)]->lut != nullptr; }
bool profile_dsp(int i, double* v) {
    const Profile& P = *PROFILES[std::clamp(i, 0, PROFILE_COUNT - 1)];
    for (int k = 0; k < 8; ++k) v[k] = P.dsp[k];
    return P.dsp_enabled;
}

// Chaine complete d'une image. video : nul pour une simulation photosite par photosite.
// Renvoie l'image ACEScg (taille ow x oh : celle de l'entree, ou celle de la video).
std::vector<float> process_impl(const float* rgb_in, int w, int h, int64_t frame, const Settings& s, int x0, int y0,
                                const VideoSpec* video, int* ow, int* oh, const float* rgb_field2) {
    const Profile& P = *PROFILES[std::clamp(s.profile, 0, PROFILE_COUNT - 1)];
    size_t N = (size_t)w * h;
    Ctx C;
    C.P = &P;
    C.span = P.white - P.black;

    // Gain, bruit et exposition (sensor.Sensor.set_exposure).
    C.K = P.K_min * s.iso / P.base_iso;
    const double logK = std::log(C.K);
    auto loglin = [&](const LogLin& d) { return std::exp(d.slope * logK + d.bias); };
    C.read_gauss = loglin(P.read_gauss);
    C.read_tukey = loglin(P.read_tukey);
    C.row_sigma = loglin(P.row);
    double t = (logK - std::log(P.K_min)) / (std::log(P.K_max) - std::log(P.K_min));
    t = std::clamp(t, 0.0, 1.0);
    const double xi = t * (P.n_iso - 1);
    const int i0 = (int)std::floor(xi), i1 = std::min(i0 + 1, P.n_iso - 1);
    const double fi = xi - i0;
    C.lam = P.tukey_shape[i0] * (1 - fi) + P.tukey_shape[i1] * fi;
    for (int c = 0; c < 4; ++c) C.cbias[c] = (P.color_bias[i0 * 4 + c] * (1 - fi) + P.color_bias[i1 * 4 + c] * fi) * s.color_bias_gain;
    C.full_well = C.span / P.K_min;
    C.dn_per = (GREY_FRACTION_OF_SATURATION / SCENE_GREY) * C.span * std::pow(2.0, s.ev);
    C.scale = C.dn_per / C.K;
    C.prnu = s.prnu_sigma >= 0 ? s.prnu_sigma : P.prnu;
    C.dsnu = s.dsnu_rel_sigma >= 0 ? s.dsnu_rel_sigma : P.dsnu;
    C.dark_ref = s.dark_current >= 0 ? s.dark_current : P.dark_e_per_s_20C;
    C.dark = C.dark_ref * dark_factor(s.temperature_c) * s.exposure_time_s;
    C.sg = std::sqrt(std::log(1.0 + C.dsnu * C.dsnu));
    C.shade = s.sensor_shading >= 0 ? s.sensor_shading : P.shading;
    C.native = P.bits;
    C.bits = s.adc_bits > 0 ? std::min(s.adc_bits, C.native) : C.native;
    C.step = std::pow(2.0, C.native - C.bits);
    C.top = std::floor(P.white / C.step);
    C.max_code = std::pow(2.0, C.native) - 1.0;
    if (s.adc_dnl_lsb > 0) {                          // convertisseur a marches irregulieres : seuils figes
        const int n_codes = (int)std::floor(P.white / C.step) + 1;
        std::vector<double> wd(n_codes);
        double sum = 0;
        for (int c = 0; c < n_codes; ++c) { wd[c] = std::max(1.0 + s.adc_dnl_lsb * normal(key(s.seed, DNL, 0, c)), 0.0); sum += wd[c]; }
        C.thresholds.resize(n_codes - 1);
        double acc = 0;
        for (int c = 0; c < n_codes - 1; ++c) { acc += wd[c] * n_codes / sum; C.thresholds[c] = acc - 0.5; }
    }
    const bool spectral = s.spectral && P.lut != nullptr;
    const double* a2c = spectral ? P.s_ap1_to_cam : P.ap1_to_cam;
    const double* wbp = spectral ? P.s_wb_gains : P.wb_gains;
    const double* c2a = spectral ? P.s_cam_to_ap1_wb : P.cam_to_ap1_wb;

    // Appareil photo sans lecture video : toute la chaine sur la carte graphique si demande et possible.
    if (s.use_gpu && P.kind == KIND_BAYER && !video && !rgb_field2) {
        std::vector<float> res;
        const bool ok = bayer_gpu(C, s, frame, rgb_in, w, h, x0, y0, spectral, a2c, wbp, c2a, res);
        if (log_enabled()) log("  camera sur la carte graphique : %s", ok ? "oui" : "non (processeur)");
        if (ok) {
            *ow = w;
            *oh = h;
            return res;
        }
    }

    // 1. Scene -> RVB de la camera (matrice, ou table spectrale de spectral.Spectral.scene_to_cam),
    //    puis decalage des capteurs (3 CCD) et filtre passe-bas (pipeline._to_camera).
    const int G = P.lut_size;
    auto to_camera = [&](const float* rgb_in) {
    std::vector<float> cam(N * 3);
#pragma omp parallel for
    for (int y = 0; y < h; ++y)
        for (int x = 0; x < w; ++x) {
            const size_t i = (size_t)y * w + x;
            double v[3] = {std::max(0.0f, rgb_in[i * 3]), std::max(0.0f, rgb_in[i * 3 + 1]), std::max(0.0f, rgb_in[i * 3 + 2])};
            double o[3] = {0, 0, 0};
            if (spectral) {
                int k = 0;
                if (v[1] > v[k]) k = 1;
                if (v[2] > v[k]) k = 2;
                double m = v[k];
                if (m > 0) {
                    int a = (k == 0) ? 1 : 0, b = (k == 2) ? 1 : 2;
                    double u = v[a] / m * (G - 1), q = v[b] / m * (G - 1);
                    int iu = std::clamp((int)std::floor(u), 0, G - 2), iq = std::clamp((int)std::floor(q), 0, G - 2);
                    double fu = u - iu, fq = q - iq;
                    const float* L = P.lut + (size_t)k * G * G * 3;
                    for (int c = 0; c < 3; ++c) {
                        double r = L[((size_t)iu * G + iq) * 3 + c] * (1 - fu) * (1 - fq) + L[((size_t)(iu + 1) * G + iq) * 3 + c] * fu * (1 - fq) +
                                   L[((size_t)iu * G + iq + 1) * 3 + c] * (1 - fu) * fq + L[((size_t)(iu + 1) * G + iq + 1) * 3 + c] * fu * fq;
                        o[c] = r * m;
                    }
                }
            } else {
                for (int c = 0; c < 3; ++c) o[c] = std::max(0.0, a2c[c * 3] * v[0] + a2c[c * 3 + 1] * v[1] + a2c[c * 3 + 2] * v[2]);
            }
            for (int c = 0; c < 3; ++c) cam[i * 3 + c] = (float)o[c];
        }
    // 1b. Trois capteurs : rouge et bleu decales (alignement du prisme).
    if (P.kind == KIND_3CHIP) {
        subpixel_shift(cam, w, h, 3, 0, s.registration_r_dx, s.registration_r_dy);
        subpixel_shift(cam, w, h, 3, 2, s.registration_b_dx, s.registration_b_dy);
    }
    if (!video) {
        // 2. Filtre passe-bas optique, a l'echelle des photosites.
        if (s.olpf_h_px > 0) filter_axis(cam, w, h, 3, olpf_kernel(s.olpf_h_px), true);
        if (s.olpf_v_px > 0) filter_axis(cam, w, h, 3, olpf_kernel(s.olpf_v_px), false);
    }
    return cam;
    };
    Timer tm;
    std::vector<float> cam = to_camera(rgb_in);
    tm.lap("scene -> camera, filtre passe-bas");
    const bool field2_used = (P.kind == KIND_CMYG) || (P.kind == KIND_3CHIP && (s.interlace < 0 ? P.interlace : s.interlace == 1));
    std::vector<float> cam2;
    if (rgb_field2 && !video && field2_used) cam2 = to_camera(rgb_field2);
    const std::vector<float>& camB = cam2.empty() ? cam : cam2;
    int bin_v = 1, bin_h = 1;
    double video_hs = 0, video_ws = 0;
    if (video) {
        // Lecture video : lignes recueillies a la vraie grille (filtre passe-bas vertical a cette echelle),
        // seules les lignes lues sont gardees, puis les colonnes (filtre horizontal), sans fabriquer toute la grille.
        int hs, ws;
        sensor_grid(video->window_w, video->window_h, P.pitch_h, P.pitch_v, h, w, &hs, &ws);
        video_hs = hs;
        video_ws = ws;
        std::vector<double> c64(cam.begin(), cam.end());
        c64 = resize_cv_area(c64, w, h, 3, w, hs);
        std::vector<float> cv(c64.begin(), c64.end());
        if (s.olpf_v_px > 0) filter_axis(cv, w, hs, 3, olpf_kernel(s.olpf_v_px), false);
        int rows = hs;
        if (video->skip > 1) {
            rows = (hs + video->skip - 1) / video->skip;
            std::vector<float> sk((size_t)rows * w * 3);
            for (int r = 0; r < rows; ++r)
                std::copy(cv.begin() + (size_t)(r * video->skip) * w * 3, cv.begin() + (size_t)(r * video->skip + 1) * w * 3,
                          sk.begin() + (size_t)r * w * 3);
            cv.swap(sk);
        }
        std::vector<double> d64(cv.begin(), cv.end());
        d64 = (ws < w) ? resize_cv_area(d64, w, rows, 3, ws, rows) : resize_linear(d64, w, rows, 3, ws, rows);
        cam.assign(d64.begin(), d64.end());
        w = ws;
        h = rows;
        N = (size_t)w * h;
        if (s.olpf_h_px > 0) filter_axis(cam, w, h, 3, olpf_kernel(s.olpf_h_px), true);
        bin_v = video->bin_v;
        bin_h = video->bin_h;
        x0 = y0 = 0;
    }
    *ow = w;
    *oh = h;
    std::vector<float> result;

    const double out_scale = C.span / C.dn_per;
    const bool interlaced = (P.kind == KIND_CMYG) || (s.interlace < 0 ? P.interlace : s.interlace == 1);

    if (P.kind == KIND_BAYER) {
        // 3. Mosaique de Bayer, capteur.
        std::vector<double> e(N);
        for (int y = 0; y < h; ++y)
            for (int x = 0; x < w; ++x) {
                const int cfa = bayer(x0 + x, y0 + y);
                e[(size_t)y * w + x] = cam[((size_t)y * w + x) * 3 + ((cfa == 0) ? 0 : (cfa == 3 ? 2 : 1))] * C.scale;
            }
        int hh, ww;
        std::vector<double> dn = expose_plane(C, s, frame, e, h, w, 0, 0, true, -1, x0, y0, &hh, bin_v, bin_h, &ww);
        tm.lap("capteur");
        h = hh;
        w = ww;
        N = (size_t)w * h;
        *ow = w;                                      // taille apres regroupement eventuel des photosites
        *oh = h;
        result.assign(N * 3, 0.0f);
        float* rgb_out = result.data();
        // 4. Noir, balance des blancs sur la mosaique, ecretage, dematricage, matrice.
        const double gw[4] = {wbp[0] * s.wb_red, wbp[1], wbp[1], wbp[2] * s.wb_blue};
        for (int y = 0; y < h; ++y)
            for (int x = 0; x < w; ++x) {
                const size_t i = (size_t)y * w + x;
                double v = (dn[i] - P.black - s.isp_black_offset_dn) / C.span * gw[bayer(x0 + x, y0 + y)];
                if (s.clip_highlights) v = std::min(v, 1.0);
                dn[i] = v;
            }
        static const double KB_RB[9] = {0.25, 0.5, 0.25, 0.5, 1.0, 0.5, 0.25, 0.5, 0.25};
        static const double KB_G[9] = {0, 0.25, 0, 0.25, 1.0, 0.25, 0, 0.25, 0};
        std::vector<double> mR, mG, mB;
        if (s.demosaic == Demosaic::Bilinear) {
            mR.assign(N, 0.0); mG.assign(N, 0.0); mB.assign(N, 0.0);
            for (int y = 0; y < h; ++y)
                for (int x = 0; x < w; ++x) {
                    const size_t i = (size_t)y * w + x;
                    int c = bayer(x0 + x, y0 + y);
                    (c == 0 ? mR : (c == 3 ? mB : mG))[i] = dn[i];
                }
        }
#pragma omp parallel for
        for (int y = 0; y < h; ++y)
            for (int x = 0; x < w; ++x) {
                const size_t i = (size_t)y * w + x;
                const int c = bayer(x0 + x, y0 + y);
                double R, Gc, B;
                if (s.demosaic == Demosaic::Nearest) {
                    const int bx = x - ((x0 + x) & 1), by = y - ((y0 + y) & 1);
                    auto at = [&](int xx, int yy) { return dn[(size_t)std::clamp(yy, 0, h - 1) * w + std::clamp(xx, 0, w - 1)]; };
                    R = at(bx, by);
                    B = at(bx + 1, by + 1);
                    Gc = ((y0 + y) & 1) ? at(bx, by + 1) : at(bx + 1, by);
                } else if (s.demosaic == Demosaic::Bilinear) {
                    R = conv3(mR, w, h, x, y, KB_RB);
                    Gc = conv3(mG, w, h, x, y, KB_G);
                    B = conv3(mB, w, h, x, y, KB_RB);
                } else {
                    const double v = dn[i];
                    const bool r_row = ((y0 + y) & 1) == 0, r_col = ((x0 + x) & 1) == 0;
                    Gc = (c == 0 || c == 3) ? conv5(dn, w, h, x, y, K_GR_GB, false) : v;
                    const double rbg_rbbr = conv5(dn, w, h, x, y, K_RG_RB, false);
                    const double rbg_brrb = conv5(dn, w, h, x, y, K_RG_RB, true);
                    const double rbgr_bbrr = conv5(dn, w, h, x, y, K_RB_BB, false);
                    if (c == 0) { R = v; B = rbgr_bbrr; }
                    else if (c == 3) { B = v; R = rbgr_bbrr; }
                    else if (r_row && !r_col) { R = rbg_rbbr; B = rbg_brrb; }
                    else { R = rbg_brrb; B = rbg_rbbr; }
                }
                const double cw[3] = {R, Gc, B};
                for (int k = 0; k < 3; ++k)
                    rgb_out[i * 3 + k] = (float)((c2a[k * 3] * cw[0] + c2a[k * 3 + 1] * cw[1] + c2a[k * 3 + 2] * cw[2]) * out_scale);
            }
        tm.lap("dematricage, matrice");
        return result;
    }

    if (P.kind == KIND_3CHIP) {
        // 3. Trois capteurs, un plan par couleur ; lecture entrelacee eventuelle (seconde trame une demi-image plus tard).
        std::vector<double> planes[3];
        for (int c = 0; c < 3; ++c) {
            std::vector<double> e(N);
            for (size_t i = 0; i < N; ++i) e[i] = cam[i * 3 + c] * C.scale;
            int hh;
            if (interlaced) {
                std::vector<double> e2(N);
                for (size_t i = 0; i < N; ++i) e2[i] = camB[i * 3 + c] * C.scale;
                std::vector<double> a = expose_plane(C, s, frame, e, h, w, c, 0, false, 0, x0, y0, &hh);
                std::vector<double> b = expose_plane(C, s, frame, e2, h, w, c, 1, false, 1, x0, y0, &hh);
                std::vector<double> dn(N);
                for (int y = 0; y < h; ++y) {
                    const std::vector<double>& src = (y % 2 == 0) ? a : b;
                    for (int x = 0; x < w; ++x) dn[(size_t)y * w + x] = src[(size_t)(y / 2) * w + x];
                }
                planes[c] = dn;
            } else {
                planes[c] = expose_plane(C, s, frame, e, h, w, c, 0, false, -1, x0, y0, &hh);
            }
        }
        const double g[3] = {wbp[0] * s.wb_red, wbp[1], wbp[2] * s.wb_blue};
        result.assign(N * 3, 0.0f);
        float* rgb_out = result.data();
        for (size_t i = 0; i < N; ++i) {
            double x[3];
            for (int c = 0; c < 3; ++c) {
                x[c] = (planes[c][i] - P.black - s.isp_black_offset_dn) / C.span * g[c];
                if (s.clip_highlights) x[c] = std::min(x[c], 1.0);
            }
            for (int k = 0; k < 3; ++k)
                rgb_out[i * 3 + k] = (float)((c2a[k * 3] * x[0] + c2a[k * 3 + 1] * x[1] + c2a[k * 3 + 2] * x[2]) * out_scale);
        }
        return result;
    }

    // 3. Camescope a mosaique complementaire : une ligne de photosites de plus, deux trames.
    const int hp = h + 1;
    auto mosaic = [&](const std::vector<float>& cm) {
        std::vector<double> e((size_t)hp * w);
        for (int y = 0; y < hp; ++y) {
            const int ys = std::min(y, h - 1);
            for (int x = 0; x < w; ++x) {
                const int f = CMYG[y % 4][x % 2];
                double v = 0;
                for (int c = 0; c < 3; ++c) v += CMYG_F[f][c] * P.cmyg_response[c] * cm[((size_t)ys * w + x) * 3 + c];
                e[(size_t)y * w + x] = v * C.scale;
            }
        }
        return e;
    };
    int ha, hb;
    std::vector<double> a = expose_plane(C, s, frame, mosaic(cam), hp, w, 0, 0, false, 0, x0, y0, &ha);
    std::vector<double> b = expose_plane(C, s, frame, mosaic(camB), hp, w, 0, 1, false, 1, x0, y0, &hb);
    std::vector<double> p(N);
    for (int y = 0; y < h; ++y) {
        const std::vector<double>& src = (y % 2 == 0) ? a : b;
        for (int x = 0; x < w; ++x) p[(size_t)y * w + x] = 2.0 * (src[(size_t)(y / 2) * w + x] - P.black - s.isp_black_offset_dn);
    }
    const double ff = s.cmyg_field_flicker >= 0 ? s.cmyg_field_flicker : P.field_flicker;
    const double lc = s.cmyg_line_crawl >= 0 ? s.cmyg_line_crawl : P.line_crawl;
    if (ff || lc)
        for (int y = 0; y < h; ++y) {
            double k = 1.0;
            if (ff && (y % 2 == 1)) k *= 1.0 + ff;
            if (lc) k *= line_type(y) == 1 ? 1.0 + 0.5 * lc : 1.0 - 0.5 * lc;
            for (int x = 0; x < w; ++x) p[(size_t)y * w + x] *= k;
        }
    std::vector<double> rgb = separate(p, h, w, s.cmyg_chroma_bw_mhz / P.pixel_clock_mhz, s.cmyg_vertical_2h, P.cmyg_response);
    const double g[3] = {wbp[0] / P.cmyg_response[0] * s.wb_red, wbp[1] / P.cmyg_response[1], wbp[2] / P.cmyg_response[2] * s.wb_blue};
    result.assign(N * 3, 0.0f);
    float* rgb_out = result.data();
    for (size_t i = 0; i < N; ++i) {
        double x[3];
        for (int c = 0; c < 3; ++c) x[c] = rgb[i * 3 + c] / C.span * g[c];
        if (s.cmyg_chroma_gain != 1.0) {
            double yy = (2 * x[0] + 3 * x[1] + 2 * x[2]) / 7.0;
            for (int c = 0; c < 3; ++c) x[c] = yy + (x[c] - yy) * s.cmyg_chroma_gain;
        }
        if (s.clip_highlights)
            for (int c = 0; c < 3; ++c) x[c] = std::min(x[c], 1.0);
        for (int k = 0; k < 3; ++k)
            rgb_out[i * 3 + k] = (float)((c2a[k * 3] * x[0] + c2a[k * 3 + 1] * x[1] + c2a[k * 3 + 2] * x[2]) * out_scale);
    }
    return result;
}

void process(const float* rgb_in, float* rgb_out, int w, int h, int64_t frame, const Settings& s, int x0, int y0,
             const float* rgb_field2) {
    int ow, oh;
    std::vector<float> r = process_impl(rgb_in, w, h, frame, s, x0, y0, nullptr, &ow, &oh, rgb_field2);
    std::copy(r.begin(), r.end(), rgb_out);
}


}  // namespace camsim
