// CameraSim, noyaux de la carte graphique (CUDA). Compiles par NVRTC au moment de la construction
// (native/gpu/build_kernels.cpp) pour chaque generation de carte NVIDIA, plus du PTX pour les futures.
// Chaque noyau refait exactement une etape de native/core/camsim.cpp (meme ordre, memes formules, calcul en
// double, meme generateur de hasard a compteur) : la carte graphique et le processeur donnent la meme image.
#include "gpu_params.h"

typedef unsigned long long u64;
typedef long long i64;
typedef unsigned int u32;

#define PI_D 3.14159265358979323846

// ------------------------------------------------------------------ hasard reproductible (camsim.cpp)
__device__ __forceinline__ u64 mix(u64 z) {
    z += 0x9E3779B97F4A7C15ULL;
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ULL;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBULL;
    return z ^ (z >> 31);
}
__device__ __forceinline__ u64 key(u32 seed, u32 stream, i64 frame, i64 index) {
    return mix(mix(mix((u64)seed * 0x100000001B3ULL + stream) ^ (u64)frame) ^ (u64)index);
}
__device__ __forceinline__ double u01(u64 h) {
    double u = (double)(h >> 11) * (1.0 / 9007199254740992.0);
    return fmin(fmax(u, 1e-12), 1.0 - 1e-12);
}
__device__ __forceinline__ double normal(u64 h) {
    double a = u01(mix(h)), b = u01(mix(h ^ 0xD1B54A32D192ED03ULL));
    return sqrt(-2.0 * log(a)) * cos(6.283185307179586 * b);
}
__device__ double poisson(double lam, u64 h) {
    if (lam <= 0.0) return 0.0;
    if (lam < 30.0) {
        double u = u01(mix(h)), p = exp(-lam), c = p, k = 0.0;
        while (u > c && k < 1000.0) { k += 1.0; p *= lam / k; c += p; }
        return k;
    }
    return fmax(0.0, floor(lam + sqrt(lam) * normal(h) + 0.5));
}

enum { S_PRNU = 1, S_DSNU = 2, S_HOT = 3, S_DEAD = 4, S_STUCK = 5, S_SHOT = 10, S_READ = 11, S_ROW = 12, S_SPARKLE = 15 };

__device__ __forceinline__ int reflect(int i, int n) {          // comme numpy "reflect"
    if (n == 1) return 0;
    while (i < 0 || i >= n) i = (i < 0) ? -i : 2 * (n - 1) - i;
    return i;
}
__device__ __forceinline__ int clampi(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }
__device__ __forceinline__ int bayer(int gx, int gy) { return ((gy & 1) << 1) | (gx & 1); }

// ------------------------------------------------------------------ 1. scene -> RVB de la camera
extern "C" __global__ void k_scene_to_cam(const float* rgb, float* cam, const float* lut, GpuBayerParams P) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= P.w * P.h) return;
    double v[3] = {fmax(0.0, (double)rgb[i * 3]), fmax(0.0, (double)rgb[i * 3 + 1]), fmax(0.0, (double)rgb[i * 3 + 2])};
    double o[3] = {0, 0, 0};
    if (P.spectral) {
        const int G = P.lut_size;
        int k = 0;
        if (v[1] > v[k]) k = 1;
        if (v[2] > v[k]) k = 2;
        const double m = v[k];
        if (m > 0) {
            const int a = (k == 0) ? 1 : 0, b = (k == 2) ? 1 : 2;
            const double u = v[a] / m * (G - 1), q = v[b] / m * (G - 1);
            const int iu = clampi((int)floor(u), 0, G - 2), iq = clampi((int)floor(q), 0, G - 2);
            const double fu = u - iu, fq = q - iq;
            const float* L = lut + (long long)k * G * G * 3;
            for (int c = 0; c < 3; ++c) {
                double r = L[((long long)iu * G + iq) * 3 + c] * (1 - fu) * (1 - fq) + L[((long long)(iu + 1) * G + iq) * 3 + c] * fu * (1 - fq) +
                           L[((long long)iu * G + iq + 1) * 3 + c] * (1 - fu) * fq + L[((long long)(iu + 1) * G + iq + 1) * 3 + c] * fu * fq;
                o[c] = r * m;
            }
        }
    } else {
        for (int c = 0; c < 3; ++c) o[c] = fmax(0.0, P.a2c[c * 3] * v[0] + P.a2c[c * 3 + 1] * v[1] + P.a2c[c * 3 + 2] * v[2]);
    }
    for (int c = 0; c < 3; ++c) cam[i * 3 + c] = (float)o[c];
}

// ------------------------------------------------------------------ 2. filtre passe-bas optique (filter_axis)
// Somme compensee en simple precision (Neumaier) : la carte grand public calcule 64 fois plus lentement en
// double ; la somme compensee donne la meme precision qu'une somme en double, pour un cout de simple precision.
__device__ __forceinline__ void kadd(float& s, float& c, float v) {
    const float t = s + v;
    c += (fabsf(s) >= fabsf(v)) ? (s - t) + v : (v - t) + s;
    s = t;
}

extern "C" __global__ void k_filter_axis(const float* in, float* out, int w, int h, int ch, const double* k, int R, int horizontal) {
    __shared__ float kh[129], kl[129];                    // coefficient en deux morceaux (haut + bas = double)
    for (int t = threadIdx.x; t <= 2 * R && t < 129; t += blockDim.x) {
        kh[t] = (float)k[t];
        kl[t] = (float)(k[t] - (double)kh[t]);
    }
    __syncthreads();
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= w * h * ch) return;
    const int c = i % ch, px = i / ch, x = px % w, y = px / w;
    float s = 0.0f, e = 0.0f;
    // produit exact du coefficient (deux morceaux) par la valeur (simple) : deux termes, partie basse en fma
    #define TAP(t, v) { const float vv = (v); const float p1 = kh[t] * vv; kadd(s, e, p1);                         e += fmaf(kh[t], vv, -p1) + kl[t] * vv; }
    if (horizontal) {
        const float* row = in + (long long)y * w * ch + c;
        if (x - R >= 0 && x + R < w) {
            const float* p = row + (long long)(x - R) * ch;
            for (int t = 0; t <= 2 * R; ++t) TAP(t, p[(long long)t * ch])
        } else {
            for (int t = 0; t <= 2 * R; ++t) TAP(t, row[(long long)reflect(x + t - R, w) * ch])
        }
    } else {
        const long long stride = (long long)w * ch;
        const float* col = in + (long long)x * ch + c;
        if (y - R >= 0 && y + R < h) {
            const float* p = col + (long long)(y - R) * stride;
            for (int t = 0; t <= 2 * R; ++t) TAP(t, p[t * stride])
        } else {
            for (int t = 0; t <= 2 * R; ++t) TAP(t, col[(long long)reflect(y + t - R, h) * stride])
        }
    }
    #undef TAP
    const double acc = (double)s + (double)e;
    out[i] = (float)fmax(acc, 0.0);
}

// ------------------------------------------------------------------ 3. capteur, premiere partie (avant adressage)
// Non-uniformite, ombrage, courant d'obscurite, photons, saturation, gain (de colonne), noir, biais par voie.
extern "C" __global__ void k_expose_a(const float* cam, double* dn, const double* col_gain, GpuBayerParams P) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    const int w = P.w, h = P.h;
    if (i >= w * h) return;
    const int x = i % w, y = i / w;
    const int cfa = bayer(P.x0 + x, P.y0 + y);
    double v = cam[(long long)i * 3 + ((cfa == 0) ? 0 : (cfa == 3 ? 2 : 1))] * P.scale;
    const i64 gi = (i64)(P.y0 + y) * 1000003 + (P.x0 + x);
    if (P.prnu > 0) v *= 1.0 + P.prnu * normal(key(P.seed, S_PRNU, 0, gi));
    if (P.shade > 0) {
        const double sy = fmax((h - 1) / 2.0, 1.0), sx = fmax((w - 1) / 2.0, 1.0);
        const double yy = (y - (h - 1) / 2.0) / sy, xx = (x - (w - 1) / 2.0) / sx;
        v *= 1.0 - P.shade * 0.5 * (yy * yy + xx * xx);
    }
    v = fmax(v, 0.0);
    if (P.dark_ref > 0) {
        double dm = (P.dsnu > 0) ? exp(-0.5 * P.sg * P.sg + P.sg * normal(key(P.seed, S_DSNU, 0, gi))) : 1.0;
        if (P.hot_frac > 0 && u01(mix(key(P.seed, S_HOT, 0, gi))) < P.hot_frac)
            dm *= P.hot_gain * u01(mix(key(P.seed, S_HOT, 1, gi)));
        v += P.dark * dm;
    }
    if (P.shot_noise) v = poisson(v, key(P.seed, S_SHOT, P.frame, gi));
    if (P.clip_full_well) v = fmin(v, P.full_well);
    v = P.K * v;
    if (P.has_col_gain) v *= col_gain[x];
    v += P.black;
    v += P.cbias[(cfa == 0) ? 0 : (cfa == 1 ? 1 : (cfa == 3 ? 2 : 3))];   // ordre ELD : R, G1, B, G2
    dn[i] = v;
}

// ------------------------------------------------------------------ 4. capteur, seconde partie
// Pannes d'adressage (tables), pixels morts ou colles, bruit de lecture, bruit de ligne, decalage des colonnes,
// parasite, convertisseur (marches, courbure, bit colle, bits inverses).
__device__ __forceinline__ double expose_b(const double* src, int x, int y, const int* rm, const int* cm,
                                           const signed char* st_col, const signed char* st_row, const double* col_off,
                                           const double* thr, const GpuBayerParams& P) {
    const int w = P.w;
    double v;
    if (rm || cm) {
        const int ry = rm ? rm[y] : y, cx = cm ? cm[x] : x;
        v = (ry >= 0 && cx >= 0) ? src[(long long)ry * w + cx] : P.black;
    } else {
        v = src[(long long)y * w + x];
    }
    const i64 gi = (i64)(P.y0 + y) * 1000003 + (P.x0 + x);
    int state = st_col[x] > st_row[y] ? st_col[x] : st_row[y];
    if (state == 0 && P.dead_frac > 0 && u01(mix(key(P.seed, S_DEAD, 0, gi))) < P.dead_frac) state = 1;
    if (P.stuck_frac > 0 && u01(mix(key(P.seed, S_STUCK, 0, gi))) < P.stuck_frac) state = 2;
    if (st_col[x] == 2 || st_row[y] == 2) state = 2;
    if (state == 1) v = P.black;
    else if (state == 2) v = P.white;
    if (P.read_gain > 0 && P.read_model != 2) {
        const u64 hk = key(P.seed, S_READ, P.frame, gi);
        if (P.read_model == 0) {
            const double u = u01(mix(hk));
            const double q = (fabs(P.lam) < 1e-9) ? log(u) - log1p(-u) : (pow(u, P.lam) - pow(1.0 - u, P.lam)) / P.lam;
            v += P.read_gain * P.read_tukey * q;
        } else {
            v += P.read_gain * P.read_gauss * normal(hk);
        }
    }
    if (P.row_gain > 0) v += P.row_gain * P.row_sigma * normal(key(P.seed, S_ROW, P.frame, P.y0 + y));
    if (P.has_col_off) v += col_off[x];
    if (P.interf_dn > 0) v += P.interf_dn * sin(2.0 * PI_D * (P.istart + (double)y * w + x) / P.interf_period);
    if (P.quantize) {
        double xq = v / P.step;
        if (P.adc_inl != 0) xq += P.adc_inl * sin(PI_D * fmin(fmax(xq / P.top, 0.0), 1.0));
        double code;
        if (P.n_thresholds > 0) {                     // upper_bound sur les seuils
            int lo = 0, hi = P.n_thresholds;
            while (lo < hi) { const int mid = (lo + hi) >> 1; if (thr[mid] <= xq) lo = mid + 1; else hi = mid; }
            code = (double)lo;
        } else {
            code = rint(xq);
        }
        v = fmin(fmax(code, 0.0), P.top) * P.step;
        i64 iv = (i64)v;
        if (P.adc_stuck_bit >= 0) iv = P.adc_stuck_value ? (iv | (1LL << P.adc_stuck_bit)) : (iv & ~(1LL << P.adc_stuck_bit));
        if (P.sparkle_rate > 0) {
            const u64 hk = key(P.seed, S_SPARKLE, P.frame, gi);
            if (u01(mix(hk)) < P.sparkle_rate) iv ^= (1LL << ((int)(u01(mix(hk ^ 0x99)) * P.native) % P.native));
        }
        v = (double)iv;
    }
    return v;
}

extern "C" __global__ void k_expose_b(const double* src, double* dst, const int* rm, const int* cm, const signed char* st_col,
                                      const signed char* st_row, const double* col_off, const double* thr, GpuBayerParams P) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= P.w * P.h) return;
    dst[i] = expose_b(src, i % P.w, i / P.w, rm, cm, st_col, st_row, col_off, thr, P);
}

// Liaison serie : glissement de bits (chaque valeur prend les bits de poids fort de sa voisine).
extern "C" __global__ void k_bitslip(const double* src, double* dst, GpuBayerParams P) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= P.w * P.h) return;
    const int x = i % P.w, nb = P.bitslip, native = P.native;
    const i64 mask = (1LL << native) - 1;
    const i64 v = (i64)src[i];
    const i64 nx = (x + 1 < P.w) ? (i64)src[i + 1] : 0;
    dst[i] = (double)(((v << nb) & mask) | (nx >> (native - nb)));
}

// Lignes qui glissent : decalage par ligne calcule par le processeur (shift[y]).
extern "C" __global__ void k_lineshift(const double* src, double* dst, const i64* shift, GpuBayerParams P) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    const int w = P.w;
    if (i >= w * P.h) return;
    const int x = i % w, y = i / w;
    const i64 k = shift[y];
    if (k == 0) { dst[i] = src[i]; return; }
    const i64 sx = x - k;
    if (P.line_persist) dst[i] = src[(long long)y * w + (((sx % w) + w) % w)];
    else dst[i] = (sx >= 0 && sx < w) ? src[(long long)y * w + sx] : P.black;
}

// ------------------------------------------------------------------ 5. traitement : noir, balance des blancs, ecretage
extern "C" __global__ void k_isp_prep(double* dn, GpuBayerParams P) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= P.w * P.h) return;
    const int x = i % P.w, y = i / P.w;
    double d = dn[i];
    if (P.quantize) d = fmin(fmax(d, 0.0), P.max_code);
    double v = (d - P.black - P.isp_black_offset) / P.span * P.gw[bayer(P.x0 + x, P.y0 + y)];
    if (P.clip_highlights) v = fmin(v, 1.0);
    dn[i] = v;
}

// ------------------------------------------------------------------ 6. dematricage et matrice
__constant__ double K_GR_GB[25] = {0, 0, -1, 0, 0, 0, 0, 2, 0, 0, -1, 2, 4, 2, -1, 0, 0, 2, 0, 0, 0, 0, -1, 0, 0};
__constant__ double K_RG_RB[25] = {0, 0, 0.5, 0, 0, 0, -1, 0, -1, 0, -1, 4, 5, 4, -1, 0, -1, 0, -1, 0, 0, 0, 0.5, 0, 0};
__constant__ double K_RB_BB[25] = {0, 0, -1.5, 0, 0, 0, 2, 0, 2, 0, -1.5, 0, 6, 0, -1.5, 0, 2, 0, 2, 0, 0, 0, -1.5, 0, 0};
__constant__ double KB_RB[9] = {0.25, 0.5, 0.25, 0.5, 1.0, 0.5, 0.25, 0.5, 0.25};
__constant__ double KB_G[9] = {0, 0.25, 0, 0.25, 1.0, 0.25, 0, 0.25, 0};

__device__ __forceinline__ double conv5(const double* m, int w, int h, int x, int y, const double* k, bool transpose) {
    double acc = 0.0;
    for (int i = 0; i < 5; ++i)
        for (int j = 0; j < 5; ++j) {
            const double c = transpose ? k[j * 5 + i] : k[i * 5 + j];
            if (c != 0.0) acc += c * m[(long long)reflect(y + i - 2, h) * w + reflect(x + j - 2, w)];
        }
    return acc / 8.0;
}

// Plan bilineaire : valeur du photosite si sa couleur est celle demandee (0 R, 1 G, 2 B), sinon 0.
__device__ __forceinline__ double conv3_masked(const double* m, int w, int h, int x, int y, const double* k, int want, int x0, int y0) {
    double acc = 0.0;
    for (int i = 0; i < 3; ++i)
        for (int j = 0; j < 3; ++j) {
            const int yy = reflect(y + i - 1, h), xx = reflect(x + j - 1, w);
            const int c = bayer(x0 + xx, y0 + yy);
            const int col = (c == 0) ? 0 : (c == 3 ? 2 : 1);
            if (col == want) acc += k[i * 3 + j] * m[(long long)yy * w + xx];
        }
    return acc;
}

extern "C" __global__ void k_demosaic(const double* dn, float* out, GpuBayerParams P) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    const int w = P.w, h = P.h;
    if (i >= w * h) return;
    const int x = i % w, y = i / w, x0 = P.x0, y0 = P.y0;
    const int c = bayer(x0 + x, y0 + y);
    double R, Gc, B;
    if (P.demosaic == 2) {
        const int bx = x - ((x0 + x) & 1), by = y - ((y0 + y) & 1);
#define AT(xx, yy) dn[(long long)clampi((yy), 0, h - 1) * w + clampi((xx), 0, w - 1)]
        R = AT(bx, by);
        B = AT(bx + 1, by + 1);
        Gc = ((y0 + y) & 1) ? AT(bx, by + 1) : AT(bx + 1, by);
#undef AT
    } else if (P.demosaic == 1) {
        R = conv3_masked(dn, w, h, x, y, KB_RB, 0, x0, y0);
        Gc = conv3_masked(dn, w, h, x, y, KB_G, 1, x0, y0);
        B = conv3_masked(dn, w, h, x, y, KB_RB, 2, x0, y0);
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
        out[(long long)i * 3 + k] = (float)((P.c2a[k * 3] * cw[0] + P.c2a[k * 3 + 1] * cw[1] + P.c2a[k * 3 + 2] * cw[2]) * P.out_scale);
}

// ================================================================== mesure du mouvement (shutter.cpp, DIS)
// Memes etapes que dis_flow() du processeur : luminance 8 bits, pyramide, recherche par blocs (propagation
// et descente), densification, affinage variationnel, retour a la pleine resolution.

// Luminance (moyenne des trois voies, positive) et sa cle de tri (les bits d'un double positif sont ordonnes).
extern "C" __global__ void k_lum(const float* rgb, double* lum, u64* keys, int n) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const double l = fmax(((double)rgb[i * 3] + rgb[i * 3 + 1] + rgb[i * 3 + 2]) / 3.0, 0.0);
    lum[i] = l;
    keys[i] = (u64)__double_as_longlong(l);
}

// Histogramme sur 16 bits de la cle, parmi les cles dont les bits superieurs valent prefix (selection exacte
// du centile en quatre passes, sans trier).
extern "C" __global__ void k_hist16(const u64* keys, int n, int shift, u64 mask, u64 prefix, unsigned int* hist) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const u64 k = keys[i];
    if ((k & mask) != prefix) return;
    atomicAdd(&hist[(k >> shift) & 0xFFFFULL], 1u);
}

extern "C" __global__ void k_gray(const double* lum, float* g, int n, double scale) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    g[i] = (float)floor(sqrt(fmin(fmax(lum[i] / fmax(scale, 1e-6), 0.0), 1.0)) * 255.0 + 0.5);
}

// Redimensionnement separable (tables de cv::resize) : d'abord les lignes, puis les colonnes.
extern "C" __global__ void k_resize_h(const float* src, double* tmp, int w, int h, int nw, const int* start,
                                      const int* sidx, const double* wt) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= h * nw) return;
    const int x = i % nw, y = i / nw;
    double acc = 0.0;
    for (int t = start[x]; t < start[x + 1]; ++t) acc += wt[t] * src[(long long)y * w + sidx[t]];
    tmp[i] = acc;
}

extern "C" __global__ void k_resize_v(const double* tmp, float* dst, int nw, int nh, const int* start, const int* sidx,
                                      const double* wt, int round8) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= nh * nw) return;
    const int x = i % nw, y = i / nw;
    double acc = 0.0;
    for (int t = start[y]; t < start[y + 1]; ++t) acc += wt[t] * tmp[(long long)sidx[t] * nw + x];
    dst[i] = round8 ? (float)fmin(fmax(rint(acc), 0.0), 255.0) : (float)acc;
}

extern "C" __global__ void k_scale2(float* u, float* v, int n, double kx, double ky) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    u[i] = (float)(u[i] * kx);
    v[i] = (float)(v[i] * ky);
}

__device__ __forceinline__ int reflect101(int i, int n) {
    if (n == 1) return 0;
    while (i < 0 || i >= n) i = i < 0 ? -i : 2 * n - 2 - i;
    return i;
}

extern "C" __global__ void k_sobel(const float* a, float* gx, float* gy, int w, int h) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= w * h) return;
    const int x = i % w, y = i / w;
    const int ym = reflect101(y - 1, h), yp = reflect101(y + 1, h), xm = reflect101(x - 1, w), xp = reflect101(x + 1, w);
#define I(xx, yy) a[(long long)(yy) * w + (xx)]
    gx[i] = (I(xp, ym) - I(xm, ym)) + 2 * (I(xp, y) - I(xm, y)) + (I(xp, yp) - I(xm, yp));
    gy[i] = (I(xm, yp) - I(xm, ym)) + 2 * (I(x, yp) - I(x, ym)) + (I(xp, yp) - I(xp, ym));
#undef I
}

__device__ __forceinline__ float bil(const float* p, int w, int h, float x, float y) {
    x = fminf(fmaxf(x, 0.0f), (float)(w - 1));
    y = fminf(fmaxf(y, 0.0f), (float)(h - 1));
    const int x0 = min((int)x, max(w - 2, 0)), y0 = min((int)y, max(h - 2, 0));
    const int x1 = min(x0 + 1, w - 1), y1 = min(y0 + 1, h - 1);
    const float ax = x - x0, ay = y - y0;
    const float* r0 = p + (long long)y0 * w;
    const float* r1 = p + (long long)y1 * w;
    return (1 - ay) * ((1 - ax) * r0[x0] + ax * r0[x1]) + ay * ((1 - ax) * r1[x0] + ax * r1[x1]);
}

// Hessien de chaque bloc (gradients de I0, moyenne retiree) : Hxx, Hyy, Hxy, somme gx, somme gy.
extern "C" __global__ void k_patch_hess(const float* Ix, const float* Iy, int w, int ws, int hs, int psz, int pstr, float* H) {
    const int b = blockIdx.x * blockDim.x + threadIdx.x;
    if (b >= ws * hs) return;
    const int is = b / ws, js = b % ws;
    double xx = 0, yy = 0, xy = 0, sx = 0, sy = 0;
    for (int r = 0; r < psz; ++r)
        for (int c = 0; c < psz; ++c) {
            const long long k = (long long)(is * pstr + r) * w + js * pstr + c;
            const double gx = Ix[k], gy = Iy[k];
            xx += gx * gx; yy += gy * gy; xy += gx * gy; sx += gx; sy += gy;
        }
    const double np = (double)(psz * psz);
    xx -= sx * sx / np; yy -= sy * sy / np; xy -= sx * sy / np;
    H[b * 5] = (float)xx; H[b * 5 + 1] = (float)yy; H[b * 5 + 2] = (float)xy; H[b * 5 + 3] = (float)sx; H[b * 5 + 4] = (float)sy;
}

extern "C" __global__ void k_patch_init(const float* U, const float* V, int uw, float* Sx, float* Sy, int ws, int hs, int psz, int pstr) {
    const int b = blockIdx.x * blockDim.x + threadIdx.x;
    if (b >= ws * hs) return;
    const int is = b / ws, js = b % ws;
    const long long k = (long long)(is * pstr + psz / 2) * uw + js * pstr + psz / 2;
    Sx[b] = U[k];
    Sy[b] = V[k];
}

__device__ __forceinline__ double warp_sum(double v) {
    for (int o = 16; o > 0; o >>= 1) v += __shfl_xor_sync(0xffffffffu, v, o);
    return v;
}

// Ecart (moyenne retiree) entre le bloc de I0 et celui de I1 deplace de (ux, uy) ; 32 fils par bloc de 8 x 8.
__device__ double patch_cost(const float* I0, const float* I1, int w, int h, int i, int j, float ux, float uy, int lane) {
    double s = 0, s2 = 0;
    for (int p = lane; p < 64; p += 32) {
        const int r = p >> 3, c = p & 7;
        const double d = bil(I1, w, h, j + c + ux, i + r + uy) - I0[(long long)(i + r) * w + j + c];
        s += d; s2 += d * d;
    }
    s = warp_sum(s); s2 = warp_sum(s2);
    return s2 - s * s / 64.0;
}

// Une passe de recherche par blocs pour une bande de lignes de blocs (un groupe de fils par bande).
// Passe 0 : de haut en bas et de gauche a droite (voisins de gauche et du haut) ; passe 1 : en sens inverse.
// Les blocs sont traites par diagonales : chacun attend son voisin de gauche et celui du haut, comme sur le
// processeur, donc meme resultat.
extern "C" __global__ void k_patch_pass(const float* I0, const float* I1, const float* Ix, const float* Iy, const float* H,
                                        float* Sx, float* Sy, int w, int h, int ws, int hs, int n_str, int pass, int iters) {
    const int st = blockIdx.x;
    const int r0 = st * hs / n_str, r1 = (st + 1) * hs / n_str, nrows = r1 - r0;
    const int warp = threadIdx.x >> 5, lane = threadIdx.x & 31, nwarps = blockDim.x >> 5;
    const int psz = 8, pstr = 3;
    for (int d = 0; d < nrows + ws - 1; ++d) {
        for (int k = warp; k < nrows; k += nwarps) {
            const int m = d - k;
            if (m < 0 || m >= ws) continue;
            const int is = pass == 0 ? r0 + k : r1 - 1 - k;
            const int js = pass == 0 ? m : ws - 1 - m;
            const int b = is * ws + js;
            const int i = is * pstr, j = js * pstr;
            float sx = Sx[b], sy = Sy[b];
            double best = patch_cost(I0, I1, w, h, i, j, sx, sy, lane);
            const int nj = pass == 0 ? js - 1 : js + 1, ni = pass == 0 ? is - 1 : is + 1;
            if (nj >= 0 && nj < ws) {
                const int q = is * ws + nj;
                const double c = patch_cost(I0, I1, w, h, i, j, Sx[q], Sy[q], lane);
                if (c < best) { best = c; sx = Sx[q]; sy = Sy[q]; }
            }
            if (ni >= r0 && ni < r1) {
                const int q = ni * ws + js;
                const double c = patch_cost(I0, I1, w, h, i, j, Sx[q], Sy[q], lane);
                if (c < best) { best = c; sx = Sx[q]; sy = Sy[q]; }
            }
            // Descente de gradient compositionnelle inverse.
            const int n_iter = pass == 0 ? iters / 2 : iters - iters / 2;
            const double hxx = H[b * 5], hyy = H[b * 5 + 1], hxy = H[b * 5 + 2], gxs = H[b * 5 + 3], gys = H[b * 5 + 4];
            double det = hxx * hyy - hxy * hxy;
            if (fabs(det) < 1e-6) det += 1e-6;
            float ux = sx, uy = sy;
            const float ux0 = ux, uy0 = uy;
            float bx = ux, by = uy;                       // best : deja l'ecart en (sx, sy)
            for (int t = 0; t < n_iter; ++t) {
                double bxs = 0, bys = 0, sd = 0;
                for (int p = lane; p < 64; p += 32) {
                    const int r = p >> 3, c = p & 7;
                    const long long kk = (long long)(i + r) * w + j + c;
                    const double dd = bil(I1, w, h, j + c + ux, i + r + uy) - I0[kk];
                    bxs += dd * Ix[kk]; bys += dd * Iy[kk]; sd += dd;
                }
                bxs = warp_sum(bxs); bys = warp_sum(bys); sd = warp_sum(sd);
                bxs -= sd * gxs / 64.0;
                bys -= sd * gys / 64.0;
                const double dx = (hyy * bxs - hxy * bys) / det, dy = (hxx * bys - hxy * bxs) / det;
                ux -= (float)dx;
                uy -= (float)dy;
                const double cst = patch_cost(I0, I1, w, h, i, j, ux, uy, lane);
                if (cst < best) { best = cst; bx = ux; by = uy; }
                if (fabs(dx) < 0.01 && fabs(dy) < 0.01) break;
            }
            ux = bx; uy = by;
            if ((ux - ux0) * (ux - ux0) + (uy - uy0) * (uy - uy0) > (float)(psz * psz)) { ux = ux0; uy = uy0; }
            if (lane == 0) { Sx[b] = ux; Sy[b] = uy; }
            __syncwarp();
        }
        __syncthreads();
    }
}

extern "C" __global__ void k_densify(const float* I0, const float* I1, const float* Sx, const float* Sy, float* U, float* V,
                                     int w, int h, int ws, int hs, int psz, int pstr) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= w * h) return;
    const int x = i % w, y = i / w;
    int i_lo = (y - psz + 1 + pstr - 1) / pstr, i_hi = y / pstr;
    int j_lo = (x - psz + 1 + pstr - 1) / pstr, j_hi = x / pstr;
    if (y - psz + 1 < 0) i_lo = 0;
    if (x - psz + 1 < 0) j_lo = 0;
    i_hi = min(i_hi, hs - 1); j_hi = min(j_hi, ws - 1);
    i_lo = min(i_lo, i_hi); j_lo = min(j_lo, j_hi);
    const float v0 = I0[i];
    double su = 0, sv = 0, sw = 0;
    for (int is = i_lo; is <= i_hi; ++is)
        for (int js = j_lo; js <= j_hi; ++js) {
            const int b = is * ws + js;
            const double d = bil(I1, w, h, x + Sx[b], y + Sy[b]) - v0;
            const double wt = 1.0 / fmax(1.0, fabs(d));
            su += wt * Sx[b]; sv += wt * Sy[b]; sw += wt;
        }
    U[i] = (float)(su / sw);
    V[i] = (float)(sv / sw);
}

// ------------------------------------------------------------------ affinage variationnel (refine)
__device__ __forceinline__ float at_c(const float* a, int w, int h, int x, int y) {
    return a[(long long)clampi(y, 0, h - 1) * w + clampi(x, 0, w - 1)];
}

extern "C" __global__ void k_scale_plane(const float* a, float* o, int n, double k) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) o[i] = (float)(a[i] * k);
}

extern "C" __global__ void k_deriv5(const float* a, float* gx, float* gy, int w, int h) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= w * h) return;
    const int x = i % w, y = i / w;
    gx[i] = (at_c(a, w, h, x - 2, y) - 8 * at_c(a, w, h, x - 1, y) + 8 * at_c(a, w, h, x + 1, y) - at_c(a, w, h, x + 2, y)) / 12.0f;
    gy[i] = (at_c(a, w, h, x, y - 2) - 8 * at_c(a, w, h, x, y - 1) + 8 * at_c(a, w, h, x, y + 1) - at_c(a, w, h, x, y + 2)) / 12.0f;
}

extern "C" __global__ void k_warp3(const float* I1, const float* I1x, const float* I1y, const float* U, const float* V,
                                   float* W, float* Wx, float* Wy, int w, int h) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= w * h) return;
    const float sx = (i % w) + U[i], sy = (i / w) + V[i];
    W[i] = bil(I1, w, h, sx, sy);
    Wx[i] = bil(I1x, w, h, sx, sy);
    Wy[i] = bil(I1y, w, h, sx, sy);
}

// Termes de l'energie : Ix Iy Iz Ixx Ixy Iyy Ixz Iyz (8 plans de n valeurs dans T).
extern "C" __global__ void k_terms(const float* W, const float* Wx, const float* Wy, const float* Wxx, const float* Wxy,
                                   const float* Wyx, const float* Wyy, const float* I0, const float* I0x, const float* I0y,
                                   const float* I0xx, const float* I0xy, const float* I0yx, const float* I0yy, float* T, int n) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    T[i] = 0.5f * (Wx[i] + I0x[i]);
    T[n + i] = 0.5f * (Wy[i] + I0y[i]);
    T[2 * n + i] = W[i] - I0[i];
    T[3 * n + i] = 0.5f * (Wxx[i] + I0xx[i]);
    T[4 * n + i] = 0.25f * (Wxy[i] + I0xy[i] + Wyx[i] + I0yx[i]);
    T[5 * n + i] = 0.5f * (Wyy[i] + I0yy[i]);
    T[6 * n + i] = Wx[i] - I0x[i];
    T[7 * n + i] = Wy[i] - I0y[i];
}

// Poids robustes et systeme lineaire de chaque pixel : ws, A11, A12, A22, b1, b2 (6 plans dans S).
extern "C" __global__ void k_weights(const float* T, const float* U, const float* V, const float* du, const float* dv,
                                     float* S, int w, int h, double alpha, double delta, double gamma, double e2) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    const int n = w * h;
    if (i >= n) return;
    const int x = i % w, y = i / w;
    const float Ix = T[i], Iy = T[n + i], Iz = T[2 * n + i], Ixx = T[3 * n + i], Ixy = T[4 * n + i], Iyy = T[5 * n + i],
                Ixz = T[6 * n + i], Iyz = T[7 * n + i];
    const double rd = Iz + Ix * du[i] + Iy * dv[i];
    const double rgx = Ixz + Ixx * du[i] + Ixy * dv[i];
    const double rgy = Iyz + Ixy * du[i] + Iyy * dv[i];
    const float wd = (float)(delta / (2 * sqrt(rd * rd + e2)));
    const float wg = (float)(gamma / (2 * sqrt(rgx * rgx + rgy * rgy + e2)));
    const long long k00 = i;
    const long long kx1 = (long long)y * w + min(x + 1, w - 1), ky1 = (long long)min(y + 1, h - 1) * w + x;
    const double u0 = (double)U[k00] + du[k00], v0 = (double)V[k00] + dv[k00];
    const double ux = ((double)U[kx1] + du[kx1]) - u0, uy = ((double)U[ky1] + du[ky1]) - u0;
    const double vx = ((double)V[kx1] + dv[kx1]) - v0, vy = ((double)V[ky1] + dv[ky1]) - v0;
    S[i] = (float)(alpha / (2 * sqrt(ux * ux + uy * uy + vx * vx + vy * vy + e2)));
    S[n + i] = (float)(wd * Ix * Ix + wg * (Ixx * Ixx + Ixy * Ixy));
    S[2 * n + i] = (float)(wd * Ix * Iy + wg * (Ixx * Ixy + Ixy * Iyy));
    S[3 * n + i] = (float)(wd * Iy * Iy + wg * (Ixy * Ixy + Iyy * Iyy));
    S[4 * n + i] = (float)(-wd * Ix * Iz - wg * (Ixx * Ixz + Ixy * Iyz));
    S[5 * n + i] = (float)(-wd * Iy * Iz - wg * (Ixy * Ixz + Iyy * Iyz));
}

// SOR rouge-noir : une couleur par lancement (les voisins sont de l'autre couleur).
extern "C" __global__ void k_sor(const float* S, const float* U, const float* V, float* du, float* dv, int w, int h, int color, double omega) {
    const int t = blockIdx.x * blockDim.x + threadIdx.x;
    const int half = (w + 1) / 2;
    const int y = t / half;
    if (y >= h) return;
    const int x = 2 * (t % half) + ((y + color) & 1);
    if (x >= w) return;
    const int n = w * h;
    const long long i = (long long)y * w + x;
    double su = 0, sv = 0, sw = 0;
    const int qxs[4] = {x - 1, x + 1, x, x}, qys[4] = {y, y, y - 1, y + 1};
    for (int q = 0; q < 4; ++q) {
        const int qx = qxs[q], qy = qys[q];
        if (qx < 0 || qx >= w || qy < 0 || qy >= h) continue;
        const long long k = (long long)qy * w + qx;
        const double wt = (qx < x || qy < y) ? S[k] : S[i];
        su += wt * ((double)U[k] + du[k] - U[i]);
        sv += wt * ((double)V[k] + dv[k] - V[i]);
        sw += wt;
    }
    const double a11 = S[n + i] + sw, a22 = S[3 * n + i] + sw;
    const double nu = (S[4 * n + i] + su - S[2 * n + i] * dv[i]) / a11;
    du[i] = (float)((1 - omega) * du[i] + omega * nu);
    const double nv = (S[5 * n + i] + sv - S[2 * n + i] * du[i]) / a22;
    dv[i] = (float)((1 - omega) * dv[i] + omega * nv);
}

extern "C" __global__ void k_add2(float* U, float* V, const float* du, const float* dv, int n) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) { U[i] += du[i]; V[i] += dv[i]; }
}

// Retour a la pleine resolution : flux (x, y) entrelace.
extern "C" __global__ void k_flow_up(const float* U, const float* V, int uw, int uh, float* flow, int w, int h, double kx, double ky) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= w * h) return;
    const int x = i % w, y = i / w;
    const double sx = (double)uw / w, sy = (double)uh / h;
    const float fx = (float)((x + 0.5) * sx - 0.5), fy = (float)((y + 0.5) * sy - 0.5);
    flow[(long long)i * 2] = (float)(bil(U, uw, uh, fx, fy) * kx);
    flow[(long long)i * 2 + 1] = (float)(bil(V, uw, uh, fx, fy) * ky);
}

// Obturateur deroulant : chaque ligne lue a son instant (tau en images) ; avant le centre, on suit le flux
// vers l'image precedente, apres, vers la suivante.
extern "C" __global__ void k_rolling(const float* img, const float* fnext, const float* fprev, const double* tau, float* out, int w, int h) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= w * h) return;
    const int x = i % w, y = i / w;
    const double t = tau[y];
    const float* fl = t >= 0 ? fnext : fprev;
    const double tt = t >= 0 ? t : -t;
    const double sx = fmin(fmax(x - tt * fl[(long long)i * 2], 0.0), (double)(w - 1));
    const double sy = fmin(fmax(y - tt * fl[(long long)i * 2 + 1], 0.0), (double)(h - 1));
    const int x1 = min((int)sx, max(w - 2, 0)), y1 = min((int)sy, max(h - 2, 0));
    const int x2 = min(x1 + 1, w - 1), y2 = min(y1 + 1, h - 1);
    const double ax = sx - x1, ay = sy - y1;
    for (int k = 0; k < 3; ++k)
        out[(long long)i * 3 + k] = (float)((1 - ay) * ((1 - ax) * img[((long long)y1 * w + x1) * 3 + k] + ax * img[((long long)y1 * w + x2) * 3 + k]) +
                                            ay * ((1 - ax) * img[((long long)y2 * w + x1) * 3 + k] + ax * img[((long long)y2 * w + x2) * 3 + k]));
}

// Redimensionnement d'une image a c canaux entrelaces (tables de cv::resize), lignes puis colonnes.
extern "C" __global__ void k_resize_hc(const float* src, double* tmp, int w, int h, int nw, int c, const int* start,
                                       const int* sidx, const double* wt) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= h * nw * c) return;
    const int k = i % c, px = i / c, x = px % nw, y = px / nw;
    double acc = 0.0;
    for (int t = start[x]; t < start[x + 1]; ++t) acc += wt[t] * src[((long long)y * w + sidx[t]) * c + k];
    tmp[i] = acc;
}

extern "C" __global__ void k_resize_vc(const double* tmp, float* dst, int nw, int nh, int c, const int* start, const int* sidx,
                                       const double* wt) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= nh * nw * c) return;
    const int k = i % c, px = i / c, x = px % nw, y = px / nw;
    double acc = 0.0;
    for (int t = start[y]; t < start[y + 1]; ++t) acc += wt[t] * tmp[((long long)sidx[t] * nw + x) * c + k];
    dst[i] = (float)acc;
}

// ================================================================== signal analogique (post.cpp, analog_apply)
// La trame video (L lignes x N echantillons) en double ; les filtres en frequence sont des convolutions
// circulaires par ligne (reponse impulsionnelle calculee par le processeur), en simple precision compensee.

#define A709_D 1.09929682680944
#define B709_D 0.018053968510807

__device__ __forceinline__ double enc709_d(double x) { x = fmax(x, 0.0); return x < B709_D ? 4.5 * x : A709_D * pow(x, 0.45) - (A709_D - 1.0); }
__device__ __forceinline__ double dec709_d(double v) { return v < 4.5 * B709_D ? v / 4.5 : pow((fmax(v, 0.0) + A709_D - 1.0) / A709_D, 1 / 0.45); }

__constant__ double AN_AP1_TO_709[9] = {1.7050509927, -0.6217921207, -0.0832588720, -0.1302564175, 1.1408047366,
                                        -0.0105483191, -0.0240033568, -0.1289689761, 1.1529723329};
__constant__ double AN_709_TO_AP1[9] = {0.6130974024, 0.3395231462, 0.0473794514, 0.0701937225, 0.9163538791,
                                        0.0134523985, 0.0206155929, 0.1095697729, 0.8698146342};

extern "C" __global__ void k_an_in(const float* img, double* rgb, int n) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const double a[3] = {img[i * 3], img[i * 3 + 1], img[i * 3 + 2]};
    for (int k = 0; k < 3; ++k) {
        const double r = AN_AP1_TO_709[k * 3] * a[0] + AN_AP1_TO_709[k * 3 + 1] * a[1] + AN_AP1_TO_709[k * 3 + 2] * a[2];
        rgb[i * 3 + k] = enc709_d(fmin(fmax(r, 0.0), 1.0));
    }
}

extern "C" __global__ void k_an_final(const double* big, float* img, int n) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    double lin[3];
    for (int k = 0; k < 3; ++k) lin[k] = dec709_d(fmin(fmax(big[(long long)i * 3 + k], 0.0), 1.2));
    for (int k = 0; k < 3; ++k)
        img[(long long)i * 3 + k] = (float)(AN_709_TO_AP1[k * 3] * lin[0] + AN_709_TO_AP1[k * 3 + 1] * lin[1] + AN_709_TO_AP1[k * 3 + 2] * lin[2]);
}

// Redimensionnement en double (tables de cv::resize), c canaux.
extern "C" __global__ void k_resize_hd(const double* src, double* tmp, int w, int h, int nw, int c, const int* start,
                                       const int* sidx, const double* wt) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= h * nw * c) return;
    const int k = i % c, px = i / c, x = px % nw, y = px / nw;
    double acc = 0.0;
    for (int t = start[x]; t < start[x + 1]; ++t) acc += wt[t] * src[((long long)y * w + sidx[t]) * c + k];
    tmp[i] = acc;
}

extern "C" __global__ void k_resize_vd(const double* tmp, double* dst, int nw, int nh, int c, const int* start, const int* sidx,
                                       const double* wt) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= nh * nw * c) return;
    const int k = i % c, px = i / c, x = px % nw, y = px / nw;
    double acc = 0.0;
    for (int t = start[y]; t < start[y + 1]; ++t) acc += wt[t] * tmp[((long long)sidx[t] * nw + x) * c + k];
    dst[i] = acc;
}

__constant__ double AN_YUV[9] = {0.299, 0.587, 0.114, -0.14713, -0.28886, 0.436, 0.615, -0.51499, -0.10001};

extern "C" __global__ void k_an_raster(const double* small, double* Y, double* U, double* V, int wr, int hr, int x0, int y0, int N) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= wr * hr) return;
    const int c = i % wr, r = i / wr;
    const double* s = small + (long long)i * 3;
    const long long o = (long long)(y0 + r) * N + (x0 + c);
    Y[o] = AN_YUV[0] * s[0] + AN_YUV[1] * s[1] + AN_YUV[2] * s[2];
    U[o] = AN_YUV[3] * s[0] + AN_YUV[4] * s[1] + AN_YUV[5] * s[2];
    V[o] = AN_YUV[6] * s[0] + AN_YUV[7] * s[1] + AN_YUV[8] * s[2];
}

extern "C" __global__ void k_an_out(const double* Y, const double* U, const double* V, double* crop, int wr, int hr, int x0, int y0,
                                    int N, double i0, double i1, double i2, double i3, double i4, double i5, double i6, double i7, double i8) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= wr * hr) return;
    const int c = i % wr, r = i / wr;
    const long long o = (long long)(y0 + r) * N + (x0 + c);
    const double y = Y[o], u = U[o], v = V[o];
    crop[(long long)i * 3] = i0 * y + i1 * u + i2 * v;
    crop[(long long)i * 3 + 1] = i3 * y + i4 * u + i5 * v;
    crop[(long long)i * 3 + 2] = i6 * y + i7 * u + i8 * v;
}

// Filtre d'une ligne : y[j] = somme h[t] x[(j - t) mod N] (une ligne par groupe de fils).
extern "C" __global__ void k_an_filt(const double* x, double* y, int N, const double* h) {
    extern __shared__ float sh[];                         // xh, xl, hh, hl : 4 N valeurs
    float* xh = sh;
    float* xl = sh + N;
    float* hh = sh + 2 * N;
    float* hl = sh + 3 * N;
    const long long row = (long long)blockIdx.x * N;
    for (int j = threadIdx.x; j < N; j += blockDim.x) {
        const double xv = x[row + j], hv = h[j];
        xh[j] = (float)xv; xl[j] = (float)(xv - (double)xh[j]);
        hh[j] = (float)hv; hl[j] = (float)(hv - (double)hh[j]);
    }
    __syncthreads();
    for (int j = threadIdx.x; j < N; j += blockDim.x) {
        float s = 0.0f, e = 0.0f;
        int q = j;                                        // (j - t) mod N, t croissant
        for (int t = 0; t < N; ++t) {
            const float a = hh[t], b = xh[q];
            const float p1 = a * b;
            kadd(s, e, p1);
            e += fmaf(a, b, -p1) + a * xl[q] + hl[t] * b;
            q = q == 0 ? N - 1 : q - 1;
        }
        y[row + j] = (double)s + (double)e;
    }
}

__device__ __forceinline__ u64 mix64d(u64 z) { return mix(z); }
__device__ double ctr_normal_d(u64 base, u64 i) {
    const u64 hh = mix(base ^ mix(i));
    double a = (double)(mix(hh) >> 11) * (1.0 / 9007199254740992.0), b = (double)(mix(hh ^ 0xD1B54A32D192ED03ULL) >> 11) * (1.0 / 9007199254740992.0);
    a = fmin(fmax(a, 1e-12), 1.0 - 1e-12);
    return sqrt(-2.0 * log(a)) * cos(6.283185307179586 * b);
}

extern "C" __global__ void k_an_noise(double* n, int M, u64 base) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < M) n[i] = ctr_normal_d(base, (u64)i);
}

extern "C" __global__ void k_axpy(double* y, const double* x, int n, double a) {   // y += a x
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) y[i] += a * x[i];
}

extern "C" __global__ void k_an_sharp(double* Y, const double* lo, int n, double s) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) Y[i] += s * (Y[i] - lo[i]);
}

// Sommes partielles par groupe de 256 : mode 0 somme de x, mode 1 somme de (x - m)^2.
extern "C" __global__ void k_sum_partial(const double* x, int n, double m, int mode, double* out) {
    __shared__ double buf[256];
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    double v = 0.0;
    if (i < n) v = mode == 0 ? x[i] : (x[i] - m) * (x[i] - m);
    buf[threadIdx.x] = v;
    __syncthreads();
    for (int s = blockDim.x / 2; s > 0; s >>= 1) {
        if (threadIdx.x < s) buf[threadIdx.x] += buf[threadIdx.x + s];
        __syncthreads();
    }
    if (threadIdx.x == 0) out[blockIdx.x] = buf[0];
}

extern "C" __global__ void k_an_shift(const double* x, double* out, int L, int N, const double* shift) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= L * N) return;
    const int r = i / N, j = i % N;
    const double idx = j - shift[r];
    const int i0 = (int)floor(idx);
    const double f = idx - i0;
    const double a = x[(long long)r * N + clampi(i0, 0, N - 1)], b = x[(long long)r * N + clampi(i0 + 1, 0, N - 1)];
    out[i] = a * (1 - f) + b * f;
}

extern "C" __global__ void k_an_avgprev(const double* x, double* out, int L, int N) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= L * N) return;
    const int r = i / N, j = i % N, q = r >= 2 ? r - 2 : r;
    out[i] = 0.5 * (x[i] + x[(long long)q * N + j]);
}

extern "C" __global__ void k_an_head(double* Y, const double* ramp, const u64* seed, int L, int N) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= L * N) return;
    const int r = i / N, j = i % N;
    if (ramp[r] > 0) Y[i] += ctr_normal_d(seed[r], (u64)j) * 0.05 * ramp[r];
}

// Une perte : copie de la ligne r-2 (compensee) ou valeurs tirees, sur [x0, x1).
extern "C" __global__ void k_an_drop(double* Y, double* U, double* V, int line, int x0, int x1, int val, const double* vals, int N) {
    const int j = x0 + blockIdx.x * blockDim.x + threadIdx.x;
    if (j >= x1) return;
    const long long i = (long long)line * N + j;
    if (val < 0) {
        Y[i] = Y[i - 2 * (long long)N]; U[i] = U[i - 2 * (long long)N]; V[i] = V[i - 2 * (long long)N];
    } else {
        Y[i] = vals[val + (j - x0)]; U[i] = 0.0; V[i] = 0.0;
    }
}

// Calage : un groupe de fils par ligne touchee (segment de bruit, puis decalage circulaire de s).
extern "C" __global__ void k_an_track(double* Y, const int* info, const double* base, const double* tvals, int N) {
    extern __shared__ double rowbuf[];
    const int* t = info + blockIdx.x * 6;                 // ligne, touche, x0, x1, debut des valeurs, s
    const int line = t[0], hit = t[1], x0 = t[2], x1 = t[3], val = t[4], s = t[5];
    double* row = Y + (long long)line * N;
    if (hit)
        for (int j = x0 + threadIdx.x; j < x1; j += blockDim.x) row[j] = base[blockIdx.x] + 0.4 * tvals[val + (j - x0)];
    __syncthreads();
    for (int j = threadIdx.x; j < N; j += blockDim.x) rowbuf[j] = row[((j - s) % N + N) % N];
    __syncthreads();
    for (int j = threadIdx.x; j < N; j += blockDim.x) row[j] = rowbuf[j];
}

// Composite : modulation de la sous-porteuse, plus le bruit de la liaison.
__device__ __forceinline__ double carrier_phase(int r, int j, double fsc, double fs, int total_lines, double cpl, long long frame) {
    const double scan = (r % 2) * (total_lines / 2.0) + r / 2 + (double)frame * total_lines;
    const double ph0 = 2 * PI_D * fmod(scan * cpl, 1.0);
    return 2 * PI_D * fsc * (j / fs) + ph0;
}

extern "C" __global__ void k_an_mod(const double* Y, const double* U, const double* V, double* comp, int L, int N, int pal,
                                    double fsc, double fs, int total_lines, double cpl, long long frame, double link_k, u64 base) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= L * N) return;
    const int r = i / N, j = i % N;
    const double w = carrier_phase(r, j, fsc, fs, total_lines, cpl, frame);
    double C;
    if (pal) {
        const double sw = (r % 4 < 2) ? 1.0 : -1.0;
        C = U[i] * sin(w) + sw * V[i] * cos(w);
    } else {
        const double c33 = cos(33.0 * PI_D / 180), s33 = sin(33.0 * PI_D / 180);
        const double I = U[i] * c33 + V[i] * (-s33), Q = U[i] * s33 + V[i] * c33;
        C = Q * sin(w) + I * cos(w);
    }
    double v = Y[i] + C;
    if (link_k > 0) v += ctr_normal_d(base, (u64)i) * link_k;
    comp[i] = v;
}

extern "C" __global__ void k_an_comb(const double* comp, double* Cs, int L, int N, int k) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= L * N) return;
    const int r = i / N, j = i % N, q = r >= k ? r - k : r;
    Cs[i] = 0.5 * (comp[i] - comp[(long long)q * N + j]);
}

extern "C" __global__ void k_sub(const double* a, const double* b, double* o, int n) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) o[i] = a[i] - b[i];
}

extern "C" __global__ void k_an_demod(const double* Cs, double* a, double* b, int L, int N, double fsc, double fs, int total_lines,
                                      double cpl, long long frame, double hue) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= L * N) return;
    const int r = i / N, j = i % N;
    const double w = carrier_phase(r, j, fsc, fs, total_lines, cpl, frame);
    a[i] = 2 * Cs[i] * sin(w + hue);
    b[i] = 2 * Cs[i] * cos(w + hue);
}

extern "C" __global__ void k_an_palsw(double* b, int L, int N) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= L * N) return;
    if ((i / N) % 4 >= 2) b[i] = -b[i];
}

extern "C" __global__ void k_an_ntsc(const double* I2, const double* Q2, double* U, double* V, int n) {
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) return;
    const double c33 = cos(33.0 * PI_D / 180), s33 = sin(33.0 * PI_D / 180);
    U[i] = I2[i] * c33 + Q2[i] * s33;
    V[i] = -I2[i] * s33 + Q2[i] * c33;
}
