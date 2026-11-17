// Obturateur deroulant, scintillement de l'eclairage, seconde trame une demi-image plus tard
// (engine/camsim/shutter.py). Le mouvement entre deux images est estime par un flux optique dense.
#pragma once
#include <cstdint>
#include <vector>

namespace camsim {

// Flux optique dense de l'image a vers l'image b (RVB lineaire, w x h x 3), en pixels : (w x h x 2).
// Methode DIS (Kroeger et al. 2016) avec les reglages de cv2.DISOpticalFlow, preset MEDIUM, utilise par le
// moteur Python : blocs de 8 px tous les 3 px, 25 iterations, affinage variationnel (5 points fixes).
// finest : derniere echelle calculee (1 : moitie de la resolution, comme OpenCV ; 2 : quart, plus rapide).
// use_gpu : sur la carte graphique NVIDIA si possible (meme methode), sinon sur le processeur.
std::vector<float> estimate_flow(const float* a, const float* b, int w, int h, int finest = 1, bool use_gpu = false);

// Reglages de l'estimation du mouvement (ceux d'OpenCV ; refine_norm : echelle des intensites de l'affinage).
struct FlowTuning {
    int grad_iters = 25, refine_iters = 5;
    double alpha = 20.0, delta = 5.0, gamma = 10.0, eps = 0.001, refine_norm = 1.0 / 255.0;   // intensites ramenees a [0, 1]
};
extern FlowTuning g_flow;

// Deplace chaque ligne de tau[y] fois le flux (lecture bilineaire, bords prolonges). img : w x h x c.
std::vector<float> warp_rows(const std::vector<float>& img, int w, int h, int c, const std::vector<float>& flow,
                             const std::vector<double>& tau);

// Instant de lecture de chaque ligne, en images, par rapport au centre de l'image.
std::vector<double> row_offsets_in_frames(int h, double fps, double readout_time_s, bool bottom_to_top);

// Obturateur deroulant. prev / next : images voisines (nullptr si absentes). Renvoie l'image cur si rien a faire.
// flow_next : si non nul, recoit le flux vers l'image suivante (reutilise pour la seconde trame).
std::vector<float> rolling_shutter(const float* cur, const float* prev, const float* next, int w, int h, double fps,
                                   double readout_time_s, bool bottom_to_top, std::vector<float>* flow_next,
                                   int finest = 1, bool use_gpu = false);

// Gain par ligne du a un eclairage qui scintille (vide si aucun).
std::vector<double> flicker_gain(int h, int64_t frame, double fps, double readout_time_s, double exposure_time_s,
                                 double flicker_hz, double depth, double phase, bool bottom_to_top);

}  // namespace camsim
