// Signal analogique sur la carte graphique (memes resultats que analog_apply de core/post.cpp, aux arrondis
// des filtres pres). Renvoie faux si la carte n'est pas disponible : le processeur prend alors le relais.
#pragma once
#include <cstdint>
#include <vector>

#include "../core/post.h"

namespace camsim {
namespace gpu {

bool analog_apply(std::vector<float>& img, int w, int h, int64_t frame, const AnalogParams& p);

}  // namespace gpu
}  // namespace camsim
