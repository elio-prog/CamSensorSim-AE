// CameraSim, coeur de calcul natif (C++17). Portage du moteur Python de reference (engine/camsim).
// Phase 1 : appareils photo CMOS a mosaique de Bayer, image par image.
#pragma once
#include <cstdint>
#include <vector>

#include "profile.h"

namespace camsim {

enum class ReadModel { Tukey = 0, Gauss = 1, Off = 2 };
enum class Demosaic { Malvar = 0, Bilinear = 1, Nearest = 2 };

struct Settings {
    int profile = 0;               // indice dans PROFILES (profiles_data.h)
    double iso = 800.0;
    double ev = 0.0;
    double exposure_time_s = 0.02;
    double temperature_c = 30.0;
    uint32_t seed = 1;
    bool shot_noise = true;
    ReadModel read_model = ReadModel::Tukey;
    double read_noise_gain = 1.0;
    double row_noise_gain = 1.0;
    double color_bias_gain = 1.0;
    double prnu_sigma = -1.0;      // < 0 : valeur du profil
    double dsnu_rel_sigma = -1.0;
    double dark_current = -1.0;    // e-/s a 20 C ; < 0 : profil
    double olpf_h_px = 1.0;
    double olpf_v_px = 1.0;
    bool clip_full_well = true;
    bool quantize = true;
    int adc_bits = 0;              // 0 : profondeur du profil
    Demosaic demosaic = Demosaic::Malvar;
    double wb_red = 1.0, wb_blue = 1.0;
    bool clip_highlights = true;
    bool spectral = true;          // couleur spectrale (sensibilites mesurees)

    // Defauts fixes et pannes (faults.py). Un bit "colle" vaut -1 quand il n'y en a pas.
    double hot_pixel_fraction = 0.0, hot_pixel_gain = 1000.0;
    double column_fpn_dn = 0.0, column_gain_sigma = 0.0;
    double dead_pixel_fraction = 0.0, stuck_pixel_fraction = 0.0;
    int dead_columns = 0, dead_rows = 0;
    int row_address_bit = -1, row_address_value = 0, col_address_bit = -1, col_address_value = 0;
    double row_address_error_rate = 0.0, col_address_error_rate = 0.0;
    double interference_dn = 0.0, interference_period_px = 517.3;
    double adc_dnl_lsb = 0.0, adc_inl_lsb = 0.0;
    int adc_stuck_bit = -1, adc_stuck_value = 1;
    double adc_sparkle_rate = 0.0;
    int bitslip = 0;
    double line_shift_rate = 0.0;
    int line_shift_max_px = 8;
    bool line_shift_persist = false;

    // Tous capteurs
    double sensor_shading = -1.0;         // < 0 : profil
    double isp_black_offset_dn = 0.0;
    // CCD (ccd.py)
    double ccd_smear_gain = 1.0;
    double ccd_antiblooming = -1.0;       // < 0 : profil
    double ccd_cti_v = -1.0, ccd_cti_h = -1.0;
    int ccd_blocked_columns = 0;
    int interlace = -1;                   // -1 : profil, 0 : non, 1 : par trames
    double registration_r_dx = 0, registration_r_dy = 0, registration_b_dx = 0, registration_b_dy = 0;
    // Camescope a mosaique complementaire (camcorder.py)
    double cmyg_chroma_bw_mhz = 1.0;
    bool cmyg_vertical_2h = false;
    double cmyg_chroma_gain = 1.0;
    double cmyg_field_flicker = -1.0, cmyg_line_crawl = -1.0;   // < 0 : profil
    // Calcul sur la carte graphique NVIDIA si elle est disponible (sinon processeur, meme resultat).
    bool use_gpu = false;
};

// Lecture video d'un appareil photo (video.video_frame) : fenetre lue, methode, taille de la video.
struct VideoSpec {
    int window_w, window_h, size_w, size_h, bin_v, bin_h, skip;
};

// Chaine de la camera ; video nul : photosite par photosite. Renvoie l'image (ow x oh).
// rgb_field2 : la scene une demi-image plus tard (seconde trame des cameras entrelacees) ; nul : meme scene.
std::vector<float> process_impl(const float* rgb_in, int w, int h, int64_t frame, const Settings& s, int x0, int y0,
                                const VideoSpec* video, int* ow, int* oh, const float* rgb_field2 = nullptr);

const Profile& get_profile(int i);

// Valeurs d'un profil utiles a l'interface.
int profile_kind(int i);                  // 0 : Bayer, 1 : trois capteurs, 2 : mosaique complementaire
bool profile_has_spectral(int i);
bool profile_dsp(int i, double* values8);   // etage video du profil (actif ?) et ses 8 valeurs

// rgb_in, rgb_out : w x h x 3, lumiere de scene ACEScg lineaire, entrelace (R, G, B).
// frame : numero d'image (le bruit en depend ; le resultat ne depend PAS de l'ordre de calcul).
// x0, y0 : position de l'image dans la grille de photosites (motifs figes stables quand on recadre).
void process(const float* rgb_in, float* rgb_out, int w, int h, int64_t frame, const Settings& s,
             int x0 = 0, int y0 = 0, const float* rgb_field2 = nullptr);

int profile_count();
const char* profile_name(int i);
const char* profile_key(int i);
// Valeurs du profil (pour les reglages exprimes en "x valeur du profil").
void profile_defaults(int i, double* prnu, double* dsnu, double* dark_e_per_s);

}  // namespace camsim
