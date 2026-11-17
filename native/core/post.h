// Etages appliques apres la camera : etage video (camdsp.py) et signal analogique (analog.py).
#pragma once
#include <cstdint>
#include <vector>

namespace camsim {

struct DspParams {
    bool enabled = false;
    double knee_point = 0.85, knee_slope = 0.25, white_clip = 1.07;
    double detail_h = 0.6, detail_v = 0.4, coring = 0.01, limit = 0.12, highlight_chroma = 0.8;
};

// img : h x w x 3, ACEScg lineaire. gain : niveau du signal de la camera (2^ev x exposition auto).
void dsp_apply(std::vector<float>& img, int w, int h, const DspParams& p, bool interlaced, double gain);

enum class Standard { Off = 0, PAL = 1, NTSC = 2 };
enum class Tape { None = 0, VHS = 1, VHS_LP = 2, SVHS = 3, Video8 = 4, Hi8 = 5 };

struct AnalogParams {
    Standard standard = Standard::Off;
    Tape tape = Tape::None;
    bool composite = true;
    bool comb = false;                 // decodeur en peigne (sinon coupe-bande)
    double link_snr_db = 55.0, hue_deg = 0.0;
    // Valeurs de la bande ; < 0 : valeur du format
    double luma_bw_mhz = -1, chroma_bw_mhz = -1, snr_db = -1, chroma_snr_db = -1, chroma_delay_us = -1;
    double jitter_us = -1, skew_us = -1, head_switch_lines = -1, head_switch_us = -1;
    double dropouts = 0.0;
    bool dropout_comp = true;
    double tracking = 0.0;
    int generations = 1;
    double sharpness = 0.0;
    bool anamorphic = false;           // l'image remplit toute la trame 4:3 (sinon bandes noires)
    uint32_t seed = 1;
    bool use_gpu = false;              // sur la carte graphique NVIDIA si possible
};

void analog_apply(std::vector<float>& img, int w, int h, int64_t frame, const AnalogParams& p);

}  // namespace camsim
