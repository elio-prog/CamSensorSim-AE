// Parametres d'une image pour les noyaux de la carte graphique. Types simples seulement : ce fichier est lu
// a la fois par le compilateur C++ et par NVRTC (code de la carte, sans bibliotheque standard).
#pragma once

struct GpuBayerParams {
    int w, h, x0, y0;
    unsigned int seed;
    long long frame;
    // Scene -> camera
    int spectral, lut_size;
    double a2c[9];
    // Capteur (sensor._expose_plane, chip 0, trame 0)
    double scale, prnu, shade, dark_ref, dark, dsnu, sg, hot_frac, hot_gain;
    int shot_noise, clip_full_well;
    double full_well, K, black, white, cbias[4];
    int has_col_gain, has_col_off;
    double dead_frac, stuck_frac;
    int read_model;                   // 0 Tukey, 1 Gauss, 2 aucun
    double read_gain, read_tukey, read_gauss, lam;
    double row_gain, row_sigma;
    double interf_dn, interf_period, istart;
    int quantize, n_thresholds, native, adc_stuck_bit, adc_stuck_value;
    double step, top, adc_inl, sparkle_rate, max_code;
    int bitslip, line_persist;
    // Traitement (isp.py)
    double span, isp_black_offset, gw[4];
    int clip_highlights, demosaic;    // 0 Malvar, 1 bilineaire, 2 plus proche
    double c2a[9], out_scale;
};
