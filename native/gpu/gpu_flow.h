// Mesure du mouvement et obturateur deroulant sur la carte graphique (memes resultats que shutter.cpp).
// Chaque fonction renvoie faux si la carte n'est pas disponible : le processeur prend alors le relais.
#pragma once
#include <vector>

namespace camsim {
namespace gpu {

// Flux de a vers b (RVB w x h x 3) : w x h x 2.
bool estimate_flow(const float* a, const float* b, int w, int h, int finest, std::vector<float>& flow);

// Obturateur deroulant (tau : instant de chaque ligne, en images). flow_next : recoit le flux vers next.
bool rolling_shutter(const float* cur, const float* prev, const float* next, int w, int h, const std::vector<double>& tau,
                     int finest, std::vector<float>& out, std::vector<float>* flow_next);

// Redimensionnement d'une image RVB (methode de resize_taps).
bool resize_rgb(const float* src, int w, int h, int nw, int nh, int method, std::vector<float>& out);

}  // namespace gpu
}  // namespace camsim
