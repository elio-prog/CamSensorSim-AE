// Profil de camera (donnees exportees par tools/export_native.py depuis le moteur Python).
#pragma once

namespace camsim {

struct LogLin {          // valeur = exp(slope * ln(K) + bias)   (modele de bruit ELD, Wei et al. 2020)
    double slope, bias;
};

enum Kind { KIND_BAYER = 0, KIND_3CHIP = 1, KIND_CMYG = 2 };

struct Profile {
    const char* key;
    const char* name;
    int kind;                            // mosaique de Bayer, trois capteurs, mosaique complementaire
    bool is_ccd;
    int bits;
    double black, white, base_iso;
    double K_min, K_max;                 // gain du systeme, DN par electron
    LogLin read_gauss, read_tukey, row;  // bruit de lecture (gaussien / Tukey-lambda) et de ligne, en DN
    const double* tukey_shape;           // forme de Tukey-lambda par reglage ISO calibre
    int n_iso;
    const double* color_bias;            // biais du noir par voie (R, G1, B, G2), n_iso x 4
    double prnu, dsnu, dark_e_per_s_20C; // defauts fixes et courant d'obscurite a 20 C
    double ap1_to_cam[9], wb_gains[3], cam_to_ap1_wb[9];        // couleur par matrice
    double s_ap1_to_cam[9], s_wb_gains[3], s_cam_to_ap1_wb[9];  // couleur spectrale (matrice ajustee)
    const float* lut;                    // reponse spectrale : 3 x G x G x 3 (nul : pas de courbes mesurees)
    int lut_size;
    // CCD
    double smear_fraction, smear_ref_s, antiblooming, cti;
    bool interlace;
    // Mosaique complementaire (camescope)
    double cmyg_response[3], pixel_clock_mhz;
    double shading, field_flicker, line_crawl;
    // Etage video du profil : actif, coude, pente, ecretage, detail H, detail V, seuil, limite, couleurs
    bool dsp_enabled;
    double dsp[8];
    // Vraie grille du capteur (resolution.py) et modes video (video.py)
    int sensor_w, sensor_h;
    double pitch_h, pitch_v;
    int n_video;                         // 0, 1 ou 2 : modes "1080p" puis "4k"
    int video_window[2][2], video_size[2][2], video_readout[2];   // readout : 0 full, 1 skip3, 2 bin3x3, 3 vbin2
    bool video_has[2];                   // mode 1080p, mode 4k
};

}  // namespace camsim
