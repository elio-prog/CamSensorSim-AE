// Chaine complete d'une image (pipeline.CameraSim.process_frame, sans ce qui demande toute la sequence) :
// resolution de l'image, vraie grille du capteur ou mode video, obturateur deroulant, scintillement,
// seconde trame une demi-image plus tard, camera, etage video. Le signal analogique est un effet a part
// (analog_apply), applique ici seulement si fs.analog.standard n'est pas Off.
#pragma once
#include <cstdint>
#include <vector>

#include "camsim.h"
#include "post.h"

namespace camsim {

struct FrameSettings {
    Settings cam;
    int resolution = 0;        // 0 : un pixel par photosite ; 1 : vraie grille du capteur
    int upscale = 0;           // retour a la taille de l'image : 0 Lanczos, 1 bilineaire, 2 plus proche
    int video_mode = 0;        // 0 : photo ; 1 : 1080p ; 2 : 4K (modes du profil)
    int video_readout = -1;    // -1 : celle de l'appareil ; 0 tout lire, 1 une ligne sur 3, 2 regroupement 3x3, 3 vertical par 2
    int dsp_mode = 0;          // etage video : 0 profil, 1 actif, 2 coupe
    DspParams dsp;
    AnalogParams analog;
    // Temps (shutter.py) : cadence, lecture des lignes, eclairage.
    double fps = 25.0;
    double readout_time_s = 0.0;   // 0 : obturateur global
    bool bottom_to_top = false;    // sens de lecture des lignes
    double flicker_hz = 0.0, flicker_depth = 0.0, flicker_phase = 0.0;   // phase en radians
    bool fast_motion = false;      // mouvement estime au quart de la resolution (plus rapide, moins fin)
};

// prev / next : images voisines (meme taille, ACEScg), ou nullptr. Elles servent a estimer le mouvement :
// obturateur deroulant, et seconde trame prise une demi-image plus tard sur les cameras entrelacees.
// Renvoie faux si le mode video demande n'existe pas sur l'appareil (l'image est alors calculee en photo).
bool process_frame(const float* rgb_in, float* rgb_out, int w, int h, int64_t frame, const FrameSettings& fs,
                   const float* prev = nullptr, const float* next = nullptr);

// Rendu multi-images d'After Effects : tant qu'un objet ThreadShare existe, le calcul de cette image ne prend
// que sa part des coeurs du processeur (coeurs / images en cours). Imbricable dans un meme fil.
class ThreadShare {
public:
    ThreadShare();
    ~ThreadShare();
private:
    bool counted_ = false;
};

bool profile_video_mode(int profile, int mode);   // le mode 1080p (1) ou 4K (2) existe-t-il ?

// Faut-il les images voisines avec ces reglages ? (obturateur deroulant, ou camera entrelacee)
bool frame_needs_neighbors(const FrameSettings& fs);

}  // namespace camsim
