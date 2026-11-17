// Signal analogique : constantes des normes et des bandes, hasard, et plan des evenements tires au hasard
// (base de temps, commutation des tetes, pertes, calage). Le plan ne depend que des reglages et du numero
// d'image : il est calcule sur le processeur, puis applique par le processeur (post.cpp) ou par la carte
// graphique (gpu/gpu_analog.cpp), qui obtiennent ainsi les memes defauts.
#pragma once
#include <cstdint>
#include <random>
#include <vector>

#include "post.h"

namespace camsim {
namespace analog_detail {

struct Std {
    double fsc, active_us, luma_bw, c_bw0, c_bw1, cycles_per_line;
    int lines, comb_step, total_lines;
};
extern const Std PAL, NTSC;

struct TapeSpec {
    double luma, chroma, snr, csnr, cdelay, jitter, skew, head, head_shift;
};
extern const TapeSpec TAPES[6];

extern const double YUV[9];                       // Y'UV (BT.470 / BT.601)

uint64_t mix64(uint64_t z);
double ctr_normal(uint64_t base, uint64_t i);      // bruit "a compteur" : une valeur par echantillon

struct Rng {                                       // generateur sequentiel, graine par image
    std::mt19937_64 g;
    uint64_t base;                                 // graine du bruit a compteur
    explicit Rng(uint64_t s) : g(s), base(mix64(s)) {}
    uint64_t stream(uint64_t k) const { return mix64(base + 0x632BE59BD9B4E019ULL * (k + 1)); }
    double uniform() { return std::uniform_real_distribution<double>(0.0, 1.0)(g); }
    double normal() { return std::normal_distribution<double>(0.0, 1.0)(g); }
};

uint64_t frame_seed(const AnalogParams& p, int64_t frame);

// Reglages effectifs d'une bande (valeurs de la bande, ou reglages de l'utilisateur).
struct TapeSettings {
    double luma, chroma, snr, csnr, cdelay, jitter, skew, head_shift;
    int head;
};
TapeSettings tape_settings(const AnalogParams& p, const TapeSpec& t);

struct TapePlan {
    std::vector<double> shift;                     // decalage de chaque ligne, en echantillons
    std::vector<double> head_ramp;                 // bruit de commutation : rampe de chaque ligne (0 : aucun)
    std::vector<uint64_t> head_seed;               // et sa graine
    struct Drop { int line, x0, x1, val; };        // perte : ligne, debut, fin, debut des valeurs (-1 : compensee)
    std::vector<Drop> drops;
    std::vector<double> drop_vals;
    struct Track { int line, hit, x0, x1, val, s; double base; };   // calage : bruit sur un segment, puis decalage s
    std::vector<Track> tracks;
    std::vector<double> track_vals;
    uint64_t noise_y = 0, noise_u = 0, noise_v = 0;
};

// Tous les tirages d'un passage sur bande, dans l'ordre (gen : numero de copie).
TapePlan plan_tape(int L, int N, double fs, const AnalogParams& p, const TapeSpec& t, Rng& rng, int gen);

}  // namespace analog_detail
}  // namespace camsim
