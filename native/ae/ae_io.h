// Lecture et ecriture des images After Effects (8, 16 ou 32 bits) en ACEScg lineaire, partagees par les deux
// effets. Calcul en parallele ; en 8 et 16 bits, le decodage sRGB passe par une table exacte (pas de puissance
// a chaque pixel).
#pragma once
#include <omp.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <vector>

namespace aeio {

enum Space { SPACE_ACESCG = 1, SPACE_SRGB = 2, SPACE_LIN709 = 3 };

// Rec.709 lineaire <-> ACEScg (memes matrices que engine/camsim/sources.py, Bradford D65 -> D60).
const double REC709_TO_AP1[9] = {0.6130974024, 0.3395231462, 0.0473794514, 0.0701937225, 0.9163538791,
                                 0.0134523985, 0.0206155929, 0.1095697729, 0.8698146342};
const double AP1_TO_REC709[9] = {1.7050509927, -0.6217921207, -0.0832588720, -0.1302564175, 1.1408047366,
                                 -0.0105483191, -0.0240033568, -0.1289689761, 1.1529723329};

inline double srgb_decode(double v) { return v <= 0.04045 ? v / 12.92 : std::pow((v + 0.055) / 1.055, 2.4); }
inline double srgb_encode(double x) { x = std::max(x, 0.0); return x <= 0.0031308 ? 12.92 * x : 1.055 * std::pow(x, 1 / 2.4) - 0.055; }

inline void mat3(const double* m, const double* a, double* b) {
    for (int k = 0; k < 3; ++k) b[k] = m[k * 3] * a[0] + m[k * 3 + 1] * a[1] + m[k * 3 + 2] * a[2];
}

// Valeur lineaire de chaque code 8 ou 16 bits (sRGB decode, ou code / max).
inline const std::vector<double>& decode_table(int maxv, bool srgb) {
    static std::vector<double> t8s, t8l, t16s, t16l;
    static bool init = false;
    #pragma omp critical(aeio_tables)
    {
        if (!init) {
            t8s.resize(256); t8l.resize(256); t16s.resize(32769); t16l.resize(32769);
            for (int i = 0; i < 256; ++i) { t8l[i] = i / 255.0; t8s[i] = srgb_decode(t8l[i]); }
            for (int i = 0; i <= 32768; ++i) { t16l[i] = i / 32768.0; t16s[i] = srgb_decode(t16l[i]); }
            init = true;
        }
    }
    if (maxv == 255) return srgb ? t8s : t8l;
    return srgb ? t16s : t16l;
}

// Image AE -> ACEScg (w x h x 3) et alpha. Renvoie faux si l'image est entierement transparente.
inline bool load(const PF_EffectWorld* src, PF_PixelFormat fmt, int w, int h, int space, std::vector<float>& rgb,
                 std::vector<float>* alpha) {
    if (!src || src->width < w || src->height < h) return false;
    rgb.assign((size_t)w * h * 3, 0.0f);
    if (alpha) alpha->assign((size_t)w * h, 1.0f);
    const bool srgb = space == SPACE_SRGB;
    const std::vector<double>* tab = nullptr;
    if (fmt == PF_PixelFormat_ARGB64) tab = &decode_table(PF_MAX_CHAN16, srgb);
    else if (fmt != PF_PixelFormat_ARGB128) tab = &decode_table(PF_MAX_CHAN8, srgb);
    int any = 0;
#pragma omp parallel for reduction(| : any)
    for (int y = 0; y < h; ++y) {
        const char* row = reinterpret_cast<const char*>(src->data) + (size_t)y * src->rowbytes;
        for (int x = 0; x < w; ++x) {
            double lin[3], ap1[3];
            float a;
            if (fmt == PF_PixelFormat_ARGB128) {
                const PF_PixelFloat* p = reinterpret_cast<const PF_PixelFloat*>(row) + x;
                const double c[3] = {p->red, p->green, p->blue};
                for (int k = 0; k < 3; ++k) lin[k] = srgb ? srgb_decode(c[k]) : c[k];
                a = p->alpha;
            } else if (fmt == PF_PixelFormat_ARGB64) {
                const PF_Pixel16* p = reinterpret_cast<const PF_Pixel16*>(row) + x;
                lin[0] = (*tab)[p->red]; lin[1] = (*tab)[p->green]; lin[2] = (*tab)[p->blue];
                a = p->alpha / (float)PF_MAX_CHAN16;
            } else {
                const PF_Pixel8* p = reinterpret_cast<const PF_Pixel8*>(row) + x;
                lin[0] = (*tab)[p->red]; lin[1] = (*tab)[p->green]; lin[2] = (*tab)[p->blue];
                a = p->alpha / (float)PF_MAX_CHAN8;
            }
            if (a > 0) any = 1;
            if (space == SPACE_ACESCG) std::memcpy(ap1, lin, sizeof(ap1));
            else mat3(REC709_TO_AP1, lin, ap1);
            const size_t i = (size_t)y * w + x;
            for (int k = 0; k < 3; ++k) rgb[i * 3 + k] = (float)ap1[k];
            if (alpha) (*alpha)[i] = a;
        }
    }
    return any != 0;
}

// ACEScg -> image AE.
inline void store(PF_EffectWorld* dst, PF_PixelFormat fmt, int w, int h, int space, const std::vector<float>& rgb,
                  const std::vector<float>& alpha) {
    const bool srgb = space == SPACE_SRGB;
#pragma omp parallel for
    for (int y = 0; y < h; ++y) {
        char* row = reinterpret_cast<char*>(dst->data) + (size_t)y * dst->rowbytes;
        for (int x = 0; x < w; ++x) {
            const size_t i = (size_t)y * w + x;
            double ap1[3] = {rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2]}, c[3];
            if (space == SPACE_ACESCG) std::memcpy(c, ap1, sizeof(c));
            else mat3(AP1_TO_REC709, ap1, c);
            if (srgb) for (int k = 0; k < 3; ++k) c[k] = srgb_encode(c[k]);
            if (fmt == PF_PixelFormat_ARGB128) {
                PF_PixelFloat* p = reinterpret_cast<PF_PixelFloat*>(row) + x;
                p->alpha = alpha[i]; p->red = (float)c[0]; p->green = (float)c[1]; p->blue = (float)c[2];
            } else if (fmt == PF_PixelFormat_ARGB64) {
                PF_Pixel16* p = reinterpret_cast<PF_Pixel16*>(row) + x;
                auto q = [](double v) { return (A_u_short)std::clamp(v * PF_MAX_CHAN16 + 0.5, 0.0, (double)PF_MAX_CHAN16); };
                p->alpha = q(alpha[i]); p->red = q(c[0]); p->green = q(c[1]); p->blue = q(c[2]);
            } else {
                PF_Pixel8* p = reinterpret_cast<PF_Pixel8*>(row) + x;
                auto q = [](double v) { return (A_u_char)std::clamp(v * PF_MAX_CHAN8 + 0.5, 0.0, (double)PF_MAX_CHAN8); };
                p->alpha = q(alpha[i]); p->red = q(c[0]); p->green = q(c[1]); p->blue = q(c[2]);
            }
        }
    }
}

}  // namespace aeio
