// Zone d'une grille (capteur, trame video) qui recoit une image sans la deformer (resolution.sensor_grid).
#pragma once
#include <algorithm>
#include <cmath>

namespace camsim {

// W x H : grille ; ph, pv : forme des cellules ; h x w : image. Renvoie hs x ws (nombres pairs).
inline void sensor_grid(int W, int H, double ph, double pv, int h, int w, int* hs_out, int* ws_out) {
    const double aspect = (double)w / h;
    double hs, ws;
    if (aspect >= (W * ph) / (H * pv)) {
        ws = W;
        hs = W * ph / aspect / pv;
    } else {
        hs = H;
        ws = H * pv * aspect / ph;
    }
    int hi = std::max(2, 2 * (int)std::nearbyint(hs / 2.0));
    int wi = std::max(2, 2 * (int)std::nearbyint(ws / 2.0));
    *hs_out = std::min(hi, H + H % 2);
    *ws_out = std::min(wi, W + W % 2);
}

}  // namespace camsim
