// CameraSim, effet After Effects (SmartFX, 8 / 16 / 32 bits, rendu multi-images).
// Module mince : il relie les curseurs d'After Effects a la chaine du coeur natif (native/core/frame.h).
// Tout ce qui se calcule image par image y est : cameras CMOS, 3 CCD et camescope, defauts et pannes,
// vraie grille du capteur, mode video des appareils photo, etage video. Le signal analogique est un effet a part (CameraSimAnalog.cpp).
#define PF_DEEP_COLOR_AWARE 1
#define NOMINMAX
#include <windows.h>
#include "AEConfig.h"
#include "entry.h"
#include "AE_Effect.h"
#include "AE_EffectCB.h"
#include "AE_EffectCBSuites.h"
#include "AE_EffectSuites.h"
#include "AE_Macros.h"
#include "Param_Utils.h"
#include "String_Utils.h"
#include "SPBasic.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <new>
#include <string>
#include <vector>

#include "frame.h"
#include "profile.h"
#include "../gpu/gpu.h"
#include "ae_io.h"
#include "log.h"

namespace {

// ------------------------------------------------------------------ table des reglages
enum Kind { T_TOPIC, T_END, T_FLOAT, T_CHECK, T_POPUP };

struct Param {
    Kind kind;
    const char* name;
    double vmin, vmax, smin, smax, dflt;   // curseurs ; case a cocher : dflt ; menu : dflt = choix (1..)
    int precision;
    const char* choices;                   // menus : "a|b|c"
};

// Les noms courts (cle) servent a lire les valeurs ; l'ordre fixe les identifiants : ne pas le changer
// sans changer la version de l'effet (les projets enregistres en dependent).
enum Id {
    P_INPUT = 0,
    P_PROFILE, P_SPACE, P_DEVICE,
    G_SHOT, P_ISO, P_EV, P_EXPOSURE, P_TEMP, P_SEED, G_SHOT_END,
    G_RES, P_RESOLUTION, P_UPSCALE, P_VIDEO_MODE, P_VIDEO_READOUT, G_RES_END,
    G_TIME, P_READOUT, P_DIRECTION, P_MOTION, P_MOTION_QUALITY, P_FLICK_HZ, P_FLICK_DEPTH, P_FLICK_PHASE, G_TIME_END,
    G_NOISE, P_SHOT_NOISE, P_READ_MODEL, P_READ_GAIN, P_ROW_GAIN, P_BIAS_GAIN, P_PRNU_MULT, P_DARK_MULT, P_HOT_FRAC, P_HOT_GAIN, G_NOISE_END,
    G_SENSOR, P_OLPF_H, P_OLPF_V, P_ADC_BITS, P_FULL_WELL, P_SHADING, G_SENSOR_END,
    G_DEFECTS, P_COL_FPN, P_COL_GAIN, P_DEAD, P_STUCK, P_DEAD_COLS, P_DEAD_ROWS, G_DEFECTS_END,
    G_FAULTS, P_ROW_BIT, P_ROW_BIT_ONE, P_COL_BIT, P_COL_BIT_ONE, P_ROW_ERR, P_COL_ERR, P_INTERF, P_INTERF_PERIOD,
    P_BITSLIP, P_LINE_SHIFT, P_LINE_SHIFT_MAX, P_LINE_PERSIST, G_FAULTS_END,
    G_ADC, P_DNL, P_INL, P_ADC_BIT, P_ADC_BIT_ONE, P_SPARKLE, G_ADC_END,
    G_CCD, P_SMEAR, P_ANTIBLOOM, P_CTI_V, P_CTI_H, P_BLOCKED, P_INTERLACE, P_REG_RDX, P_REG_RDY, P_REG_BDX, P_REG_BDY, G_CCD_END,
    G_CAM, P_CHROMA_BW, P_CHROMA_2H, P_CHROMA_GAIN, P_FIELD_FLICKER, P_LINE_CRAWL, G_CAM_END,
    G_ISP, P_DEMOSAIC, P_WB_R, P_WB_B, P_CLIP, P_SPECTRAL, P_BLACK_OFFSET, G_ISP_END,
    G_DSP, P_DSP_MODE, P_KNEE, P_KNEE_SLOPE, P_WHITE_CLIP, P_DETAIL_H, P_DETAIL_V, P_CORING, P_LIMIT, P_HL_CHROMA, G_DSP_END,
    P_COUNT
};

const Param PARAMS[P_COUNT] = {
    {T_TOPIC, "", 0, 0, 0, 0, 0, 0, nullptr},
    {T_POPUP, "Camera", 0, 0, 0, 0, 1, 0, nullptr},                     // choix remplis au demarrage
    {T_POPUP, "Espace de travail", 0, 0, 0, 0, 1, 0, "ACEScg lineaire|sRGB (non lineaire)|Rec.709 lineaire"},
    {T_POPUP, "Calcul", 0, 0, 0, 0, 1, 0, "Carte graphique NVIDIA (si disponible)|Processeur"},
    {T_TOPIC, "Prise de vue"},
    {T_FLOAT, "ISO", 25, 409600, 50, 25600, 800, 0},
    {T_FLOAT, "Exposition (diaph.)", -10, 10, -4, 4, 0, 2},
    {T_FLOAT, "Temps de pose (s)", 0.00001, 10, 0.0005, 0.1, 0.02, 4},
    {T_FLOAT, "Temperature du capteur (C)", -40, 120, 0, 70, 30, 1},
    {T_FLOAT, "Numero de serie (graine)", 1, 99999, 1, 999, 1, 0},
    {T_END, ""},
    {T_TOPIC, "Resolution et lecture video"},
    {T_POPUP, "Resolution de la simulation", 0, 0, 0, 0, 1, 0, "Celle de l'image|Vraie grille du capteur"},
    {T_POPUP, "Agrandissement", 0, 0, 0, 0, 1, 0, "Lanczos|Bilineaire|Plus proche"},
    {T_POPUP, "Mode video (appareils photo)", 0, 0, 0, 0, 1, 0, "Photo|1080p|4K"},
    {T_POPUP, "Lecture video", 0, 0, 0, 0, 1, 0, "Celle de l'appareil|Tout lire|1 ligne sur 3|Regroupement 3x3|Regroupement vertical par 2"},
    {T_END, ""},
    {T_TOPIC, "Temps et mouvement"},
    {T_FLOAT, "Temps de lecture des lignes (ms, 0 = global)", 0, 200, 0, 60, 0, 1},
    {T_POPUP, "Sens de lecture", 0, 0, 0, 0, 1, 0, "Du haut vers le bas|Du bas vers le haut"},
    {T_POPUP, "Mouvement entre images", 0, 0, 0, 0, 1, 0, "Estime sur les images voisines|Ignore"},
    {T_POPUP, "Precision du mouvement", 0, 0, 0, 0, 1, 0, "Fine (comme le moteur de reference)|Rapide (deux fois moins fine)"},
    {T_FLOAT, "Scintillement de l'eclairage (Hz, 0 = aucun)", 0, 1000, 0, 120, 0, 1},
    {T_FLOAT, "Profondeur du scintillement", 0, 1, 0, 1, 0.3, 2},
    {T_FLOAT, "Phase du scintillement (degres)", -360, 360, -180, 180, 0, 1},
    {T_END, ""},
    {T_TOPIC, "Bruit"},
    {T_CHECK, "Bruit de photons", 0, 0, 0, 0, 1},
    {T_POPUP, "Bruit de lecture", 0, 0, 0, 0, 1, 0, "Tukey-lambda (calibre)|Gaussien|Aucun"},
    {T_FLOAT, "Bruit de lecture x", 0, 100, 0, 10, 1, 2},
    {T_FLOAT, "Bruit de ligne x", 0, 200, 0, 30, 1, 2},
    {T_FLOAT, "Biais du noir par voie x", 0, 200, 0, 20, 1, 2},
    {T_FLOAT, "Non-uniformite (%, -1 = profil)", -1, 50, -1, 5, -1, 2},
    {T_FLOAT, "Courant d'obscurite (e-/s a 20 C, -1 = profil)", -1, 1000000, -1, 500, -1, 1},
    {T_FLOAT, "Part de pixels chauds", 0, 0.1, 0, 0.005, 0, 4},
    {T_FLOAT, "Force des pixels chauds", 0, 100000, 0, 5000, 1000, 0},
    {T_END, ""},
    {T_TOPIC, "Capteur"},
    {T_FLOAT, "Filtre passe-bas horizontal (px)", 0, 4, 0, 2, 1, 2},
    {T_FLOAT, "Filtre passe-bas vertical (px)", 0, 4, 0, 2, 1, 2},
    {T_FLOAT, "Profondeur (bits, 0 = profil)", 0, 16, 0, 14, 0, 0},
    {T_CHECK, "Saturation du puits", 0, 0, 0, 0, 1},
    {T_FLOAT, "Ombrage vers les coins (-1 = profil)", -1, 1, -1, 0.6, -1, 2},
    {T_END, ""},
    {T_TOPIC, "Defauts fixes"},
    {T_FLOAT, "Decalage des colonnes (DN)", 0, 1000, 0, 40, 0, 1},
    {T_FLOAT, "Ecart de gain des colonnes", 0, 1, 0, 0.1, 0, 3},
    {T_FLOAT, "Part de pixels morts", 0, 1, 0, 0.01, 0, 4},
    {T_FLOAT, "Part de pixels colles", 0, 1, 0, 0.003, 0, 4},
    {T_FLOAT, "Colonnes mortes", 0, 200, 0, 12, 0, 0},
    {T_FLOAT, "Lignes mortes", 0, 200, 0, 8, 0, 0},
    {T_END, ""},
    {T_TOPIC, "Pannes"},
    {T_FLOAT, "Bit d'adresse de ligne colle (-1 = aucun)", -1, 15, -1, 12, -1, 0},
    {T_CHECK, "... colle a 1 (sinon a 0)", 0, 0, 0, 0, 0},
    {T_FLOAT, "Bit d'adresse de colonne colle (-1 = aucun)", -1, 15, -1, 12, -1, 0},
    {T_CHECK, "... colle a 1 (sinon a 0) ", 0, 0, 0, 0, 0},
    {T_FLOAT, "Lignes lues au mauvais endroit", 0, 1, 0, 0.08, 0, 3},
    {T_FLOAT, "Colonnes lues au mauvais endroit", 0, 1, 0, 0.08, 0, 3},
    {T_FLOAT, "Parasite d'alimentation (DN)", 0, 5000, 0, 200, 0, 1},
    {T_FLOAT, "Periode du parasite (px)", 2, 100000, 40, 4000, 517.3, 1},
    {T_FLOAT, "Glissement de bits", 0, 15, 0, 15, 0, 0},
    {T_FLOAT, "Lignes qui glissent", 0, 1, 0, 0.1, 0, 3},
    {T_FLOAT, "Glissement maximal (px)", 0, 1000, 0, 64, 8, 0},
    {T_CHECK, "Synchro perdue (glissements cumules)", 0, 0, 0, 0, 0},
    {T_END, ""},
    {T_TOPIC, "Convertisseur"},
    {T_FLOAT, "Marches irregulieres (LSB)", 0, 5, 0, 1.2, 0, 2},
    {T_FLOAT, "Courbure (LSB)", 0, 5000, 0, 400, 0, 1},
    {T_FLOAT, "Bit colle (-1 = aucun)", -1, 15, -1, 13, -1, 0},
    {T_CHECK, "... colle a 1 (sinon a 0)  ", 0, 0, 0, 0, 1},
    {T_FLOAT, "Bits inverses au hasard", 0, 1, 0, 0.02, 0, 4},
    {T_END, ""},
    {T_TOPIC, "CCD et 3 capteurs"},
    {T_FLOAT, "Trainee verticale x", 0, 10000, 0, 200, 1, 1},
    {T_FLOAT, "Protection contre l'eblouissement (0 = profil, 1 = aucune)", 0, 10000, 0, 300, 0, 0},
    {T_FLOAT, "Perte au transfert vertical (-1 = profil)", -1, 0.1, -1, 0.002, -1, 5},
    {T_FLOAT, "Perte au transfert horizontal (-1 = profil)", -1, 0.1, -1, 0.002, -1, 5},
    {T_FLOAT, "Colonnes bloquees", 0, 100, 0, 10, 0, 0},
    {T_POPUP, "Lecture entrelacee", 0, 0, 0, 0, 1, 0, "Profil|Non|Par trames"},
    {T_FLOAT, "Rouge decale en X (px)", -32, 32, -4, 4, 0, 2},
    {T_FLOAT, "Rouge decale en Y (px)", -32, 32, -4, 4, 0, 2},
    {T_FLOAT, "Bleu decale en X (px)", -32, 32, -4, 4, 0, 2},
    {T_FLOAT, "Bleu decale en Y (px)", -32, 32, -4, 4, 0, 2},
    {T_END, ""},
    {T_TOPIC, "Camescope"},
    {T_FLOAT, "Bande passante des couleurs (MHz)", 0, 7, 0.1, 4, 1, 2},
    {T_CHECK, "Couleur verticale moyennee (2H)", 0, 0, 0, 0, 0},
    {T_FLOAT, "Niveau de couleur x", 0, 5, 0, 2, 1, 2},
    {T_FLOAT, "Ecart entre trames (-1 = profil)", -1, 1, -1, 0.15, -1, 3},
    {T_FLOAT, "Ecart entre lignes (-1 = profil)", -1, 1, -1, 0.2, -1, 3},
    {T_END, ""},
    {T_TOPIC, "Traitement"},
    {T_POPUP, "Dematricage", 0, 0, 0, 0, 1, 0, "Malvar-He-Cutler|Bilineaire|Plus proche"},
    {T_FLOAT, "Balance des blancs : rouge x", 0.1, 10, 0.25, 4, 1, 3},
    {T_FLOAT, "Balance des blancs : bleu x", 0.1, 10, 0.25, 4, 1, 3},
    {T_CHECK, "Ecretage des hautes lumieres", 0, 0, 0, 0, 1},
    {T_CHECK, "Couleur spectrale (vraies courbes)", 0, 0, 0, 0, 1},
    {T_FLOAT, "Erreur de niveau noir (DN)", -2000, 2000, -150, 150, 0, 1},
    {T_END, ""},
    {T_TOPIC, "Etage video"},
    {T_POPUP, "Etage video (coude, contours)", 0, 0, 0, 0, 1, 0, "Profil|Actif|Coupe"},
    {T_FLOAT, "Point de coude (1 = 100 %)", 0.1, 2, 0.6, 1, 0.85, 3},
    {T_FLOAT, "Pente au-dessus du coude", 0, 1, 0.05, 1, 0.25, 3},
    {T_FLOAT, "Ecretage du blanc", 0.5, 2, 0.9, 1.1, 1.07, 3},
    {T_FLOAT, "Accentuation horizontale", 0, 10, 0, 2.5, 0.6, 2},
    {T_FLOAT, "Accentuation verticale", 0, 10, 0, 2, 0.4, 2},
    {T_FLOAT, "Seuil des details", 0, 0.5, 0, 0.05, 0.01, 3},
    {T_FLOAT, "Amplitude maximale des halos", 0, 2, 0.02, 0.4, 0.12, 3},
    {T_FLOAT, "Couleurs reduites dans les blancs", 0, 1, 0, 1, 0.8, 2},
    {T_END, ""},
};

enum Space { SPACE_ACESCG = 1, SPACE_SRGB = 2, SPACE_LIN709 = 3 };

// Rec.709 lineaire <-> ACEScg (memes matrices que engine/camsim/sources.py, Bradford D65 -> D60).
const double REC709_TO_AP1[9] = {0.6130974024, 0.3395231462, 0.0473794514, 0.0701937225, 0.9163538791,
                                 0.0134523985, 0.0206155929, 0.1095697729, 0.8698146342};
const double AP1_TO_REC709[9] = {1.7050509927, -0.6217921207, -0.0832588720, -0.1302564175, 1.1408047366,
                                 -0.0105483191, -0.0240033568, -0.1289689761, 1.1529723329};

inline double srgb_decode(double v) { return v <= 0.04045 ? v / 12.92 : std::pow((v + 0.055) / 1.055, 2.4); }
inline double srgb_encode(double x) { x = std::max(x, 0.0); return x <= 0.0031308 ? 12.92 * x : 1.055 * std::pow(x, 1 / 2.4) - 0.055; }

inline void mat3(const double* m, const double* a, double* b) {
    for (int k = 0; k < 3; ++k) b[k] = m[k * 3] * a[0] + m[k * 3 + 1] * a[1] + m[k * 3 + 2] * a[2];
}

PF_Err About(PF_InData* in_data, PF_OutData* out_data, PF_ParamDef* params[], PF_LayerDef* output) {
    const std::string gpu_info = camsim::gpu::describe();
    PF_SPRINTF(out_data->return_msg, "CameraSim 2.4\rCarte graphique : %s\rSimulation physique d'une camera : capteur, electronique, traitement, "
                                     "etage video. Signal analogique : effet CameraSim Analogique, a placer apres.", gpu_info.c_str());
    return PF_Err_NONE;
}

PF_Err GlobalSetup(PF_InData* in_data, PF_OutData* out_data, PF_ParamDef* params[], PF_LayerDef* output) {
    out_data->my_version = PF_VERSION(2, 4, 0, PF_Stage_DEVELOP, 1);
    // Journal des temps : %TEMP%\\CameraSim_temps.txt
    char tmp[MAX_PATH] = {0};
    if (GetTempPathA(MAX_PATH, tmp)) camsim::set_log_file((std::string(tmp) + "CameraSim_temps.txt").c_str());
    out_data->out_flags = PF_OutFlag_DEEP_COLOR_AWARE | PF_OutFlag_NON_PARAM_VARY | PF_OutFlag_SEND_UPDATE_PARAMS_UI;
    out_data->out_flags2 = PF_OutFlag2_SUPPORTS_SMART_RENDER | PF_OutFlag2_FLOAT_COLOR_AWARE |
                           PF_OutFlag2_SUPPORTS_THREADED_RENDERING;
    return PF_Err_NONE;
}

// Identifiant enregistre dans les projets (uu.id). After Effects y retrouve les valeurs de chaque reglage :
// un reglage garde le sien pour toujours, meme si sa place change. Les reglages de la version 2.1 gardent
// leur ancien numero (leur place en 2.1) ; ceux ajoutes en 2.2 (groupe Temps et mouvement) prennent 200 et plus.
// A chaque nouveau reglage : un numero neuf, jamais reutilise.
A_long disk_id(int index) {
    // 2.2 : G_TIME 200, P_READOUT 201, P_DIRECTION 202, P_MOTION 203, P_FLICK_HZ 204, P_FLICK_DEPTH 205,
    //       P_FLICK_PHASE 206, G_TIME_END 207. 2.3 : P_MOTION_QUALITY 208. 2.4 : P_DEVICE 209.
    if (index == P_DEVICE) return 209;
    const int j = index > P_DEVICE ? index - 1 : index;      // place dans la liste 2.3
    const int t0 = G_TIME - 1;                               // G_TIME dans la liste 2.3
    if (j < t0) return j;
    if (j <= t0 + 3) return 200 + (j - t0);
    if (j == t0 + 4) return 208;
    if (j <= t0 + 8) return 204 + (j - (t0 + 5));
    return j - 9;
}

int count_choices(const char* s) {
    int n = 1;
    for (; *s; ++s) n += (*s == '|');
    return n;
}

PF_Err ParamsSetup(PF_InData* in_data, PF_OutData* out_data, PF_ParamDef* params[], PF_LayerDef* output) {
    PF_Err err = PF_Err_NONE;
    PF_ParamDef def;
    std::string names;
    for (int i = 0; i < camsim::profile_count(); ++i) {
        if (i) names += "|";
        names += camsim::profile_name(i);
    }
    for (int id = 1; id < P_COUNT && !err; ++id) {
        const Param& p = PARAMS[id];
        AEFX_CLR_STRUCT(def);
        switch (p.kind) {
            case T_TOPIC:
                PF_ADD_TOPIC(p.name, disk_id(id));
                break;
            case T_END:
                PF_END_TOPIC(disk_id(id));
                break;
            case T_FLOAT:
                PF_ADD_FLOAT_SLIDERX(p.name, p.vmin, p.vmax, p.smin, p.smax, p.dflt, p.precision, PF_ValueDisplayFlag_NONE, PF_ParamFlag_SUPERVISE, disk_id(id));
                break;
            case T_CHECK:
                PF_ADD_CHECKBOXX(p.name, p.dflt != 0 ? 1 : 0, PF_ParamFlag_SUPERVISE, disk_id(id));
                break;
            case T_POPUP: {
                const char* ch = (id == P_PROFILE) ? names.c_str() : p.choices;
                PF_ADD_POPUPX(p.name, count_choices(ch), (int)p.dflt, ch, PF_ParamFlag_SUPERVISE, disk_id(id));
                break;
            }
        }
    }
    out_data->num_params = P_COUNT;
    return err;
}

const A_long ID_PREV = 1001, ID_NEXT = 1002;   // identifiants des images voisines (uniques)

PF_Err ReadParams(PF_InData* in_data, double* v);
camsim::FrameSettings Settings(const double* v);

bool NeedsNeighbors(const double* v) {
    return v[P_MOTION] == 1 && camsim::frame_needs_neighbors(Settings(v));
}

PF_Err PreRender(PF_InData* in_data, PF_OutData* out_data, PF_PreRenderExtra* extra) {
    PF_Err err = PF_Err_NONE;
    PF_RenderRequest req = extra->input->output_request;
    PF_CheckoutResult in_result;
    // Les motifs figes dependent de la position dans le calque, et plusieurs etages (grille du capteur,
    // signal analogique) travaillent sur l'image entiere : on demande toujours le calque entier, une seule
    // fois par identifiant (AE refuse les doublons).
    const auto scaled = [](A_long v, const PF_RationalScale& s) {
        return (A_long)std::ceil((double)v * s.num / (s.den ? s.den : 1));
    };
    req.rect.left = 0;
    req.rect.top = 0;
    req.rect.right = scaled(in_data->width, in_data->downsample_x);
    req.rect.bottom = scaled(in_data->height, in_data->downsample_y);
    ERR(extra->cb->checkout_layer(in_data->effect_ref, P_INPUT, P_INPUT, &req, in_data->current_time,
                                  in_data->time_step, in_data->time_scale, &in_result));
    if (!err) {
        extra->output->result_rect = in_result.result_rect;
        extra->output->max_result_rect = in_result.max_result_rect;
        extra->output->solid = FALSE;
    }
    // Obturateur deroulant et seconde trame : l'image precedente et la suivante, pour estimer le mouvement.
    double V[P_COUNT];
    std::memset(V, 0, sizeof(V));
    ERR(ReadParams(in_data, V));
    if (!err && NeedsNeighbors(V) && in_data->time_step != 0) {
        PF_CheckoutResult r;
        ERR(extra->cb->checkout_layer(in_data->effect_ref, P_INPUT, ID_PREV, &req, in_data->current_time - in_data->time_step,
                                      in_data->time_step, in_data->time_scale, &r));
        ERR(extra->cb->checkout_layer(in_data->effect_ref, P_INPUT, ID_NEXT, &req, in_data->current_time + in_data->time_step,
                                      in_data->time_step, in_data->time_scale, &r));
    }
    return err;
}

PF_Err ReadParams(PF_InData* in_data, double* v) {
    PF_Err err = PF_Err_NONE, err2 = PF_Err_NONE;
    for (int i = 1; i < P_COUNT && !err; ++i) {
        if (PARAMS[i].kind == T_TOPIC || PARAMS[i].kind == T_END) continue;   // pas des reglages
        PF_ParamDef p;
        AEFX_CLR_STRUCT(p);
        ERR(PF_CHECKOUT_PARAM(in_data, i, in_data->current_time, in_data->time_step, in_data->time_scale, &p));
        if (!err) {
            switch (p.param_type) {
                case PF_Param_FLOAT_SLIDER: v[i] = p.u.fs_d.value; break;
                case PF_Param_CHECKBOX: v[i] = p.u.bd.value ? 1.0 : 0.0; break;
                case PF_Param_POPUP: v[i] = p.u.pd.value; break;
                default: v[i] = 0.0; break;
            }
        }
        ERR2(PF_CHECKIN_PARAM(in_data, &p));
    }
    return err;
}

camsim::FrameSettings Settings(const double* v) {
    camsim::FrameSettings fs;
    camsim::Settings& s = fs.cam;
    const int prof = std::clamp((int)v[P_PROFILE] - 1, 0, camsim::profile_count() - 1);
    s.profile = prof;
    s.iso = v[P_ISO];
    s.ev = v[P_EV];
    s.exposure_time_s = v[P_EXPOSURE];
    s.temperature_c = v[P_TEMP];
    s.seed = (uint32_t)std::max(1.0, v[P_SEED]);
    fs.resolution = (int)v[P_RESOLUTION] - 1;
    fs.upscale = (int)v[P_UPSCALE] - 1;
    fs.video_mode = (int)v[P_VIDEO_MODE] - 1;
    fs.video_readout = (int)v[P_VIDEO_READOUT] - 2;             // 1 = celle de l'appareil -> -1
    s.shot_noise = v[P_SHOT_NOISE] != 0;
    s.read_model = (camsim::ReadModel)std::clamp((int)v[P_READ_MODEL] - 1, 0, 2);
    s.read_noise_gain = v[P_READ_GAIN];
    s.row_noise_gain = v[P_ROW_GAIN];
    s.color_bias_gain = v[P_BIAS_GAIN];
    s.prnu_sigma = v[P_PRNU_MULT] < 0 ? -1.0 : v[P_PRNU_MULT] / 100.0;
    s.dark_current = v[P_DARK_MULT] < 0 ? -1.0 : v[P_DARK_MULT];
    s.hot_pixel_fraction = v[P_HOT_FRAC];
    s.hot_pixel_gain = v[P_HOT_GAIN];
    s.olpf_h_px = v[P_OLPF_H];
    s.olpf_v_px = v[P_OLPF_V];
    s.adc_bits = (int)v[P_ADC_BITS];
    s.clip_full_well = v[P_FULL_WELL] != 0;
    s.sensor_shading = v[P_SHADING];
    s.column_fpn_dn = v[P_COL_FPN];
    s.column_gain_sigma = v[P_COL_GAIN];
    s.dead_pixel_fraction = v[P_DEAD];
    s.stuck_pixel_fraction = v[P_STUCK];
    s.dead_columns = (int)v[P_DEAD_COLS];
    s.dead_rows = (int)v[P_DEAD_ROWS];
    s.row_address_bit = (int)v[P_ROW_BIT];
    s.row_address_value = v[P_ROW_BIT_ONE] != 0;
    s.col_address_bit = (int)v[P_COL_BIT];
    s.col_address_value = v[P_COL_BIT_ONE] != 0;
    s.row_address_error_rate = v[P_ROW_ERR];
    s.col_address_error_rate = v[P_COL_ERR];
    s.interference_dn = v[P_INTERF];
    s.interference_period_px = v[P_INTERF_PERIOD];
    s.bitslip = (int)v[P_BITSLIP];
    s.line_shift_rate = v[P_LINE_SHIFT];
    s.line_shift_max_px = (int)v[P_LINE_SHIFT_MAX];
    s.line_shift_persist = v[P_LINE_PERSIST] != 0;
    s.adc_dnl_lsb = v[P_DNL];
    s.adc_inl_lsb = v[P_INL];
    s.adc_stuck_bit = (int)v[P_ADC_BIT];
    s.adc_stuck_value = v[P_ADC_BIT_ONE] != 0;
    s.adc_sparkle_rate = v[P_SPARKLE];
    s.ccd_smear_gain = v[P_SMEAR];
    s.ccd_antiblooming = v[P_ANTIBLOOM] > 0 ? v[P_ANTIBLOOM] : -1.0;
    s.ccd_cti_v = v[P_CTI_V];
    s.ccd_cti_h = v[P_CTI_H];
    s.ccd_blocked_columns = (int)v[P_BLOCKED];
    s.interlace = (int)v[P_INTERLACE] - 2;                      // Profil -1, Non 0, Par trames 1
    s.registration_r_dx = v[P_REG_RDX];
    s.registration_r_dy = v[P_REG_RDY];
    s.registration_b_dx = v[P_REG_BDX];
    s.registration_b_dy = v[P_REG_BDY];
    s.cmyg_chroma_bw_mhz = v[P_CHROMA_BW];
    s.cmyg_vertical_2h = v[P_CHROMA_2H] != 0;
    s.cmyg_chroma_gain = v[P_CHROMA_GAIN];
    s.cmyg_field_flicker = v[P_FIELD_FLICKER];
    s.cmyg_line_crawl = v[P_LINE_CRAWL];
    s.demosaic = (camsim::Demosaic)std::clamp((int)v[P_DEMOSAIC] - 1, 0, 2);
    s.wb_red = v[P_WB_R];
    s.wb_blue = v[P_WB_B];
    s.clip_highlights = v[P_CLIP] != 0;
    s.spectral = v[P_SPECTRAL] != 0;
    s.isp_black_offset_dn = v[P_BLACK_OFFSET];
    s.use_gpu = v[P_DEVICE] == 1;
    fs.dsp_mode = (int)v[P_DSP_MODE] - 1;
    fs.dsp.knee_point = v[P_KNEE];
    fs.dsp.knee_slope = v[P_KNEE_SLOPE];
    fs.dsp.white_clip = v[P_WHITE_CLIP];
    fs.dsp.detail_h = v[P_DETAIL_H];
    fs.dsp.detail_v = v[P_DETAIL_V];
    fs.dsp.coring = v[P_CORING];
    fs.dsp.limit = v[P_LIMIT];
    fs.dsp.highlight_chroma = v[P_HL_CHROMA];
    fs.readout_time_s = v[P_READOUT] / 1000.0;
    fs.bottom_to_top = v[P_DIRECTION] == 2;
    fs.fast_motion = v[P_MOTION_QUALITY] == 2;
    fs.flicker_hz = v[P_FLICK_HZ];
    fs.flicker_depth = v[P_FLICK_DEPTH];
    fs.flicker_phase = v[P_FLICK_PHASE] * 3.14159265358979323846 / 180.0;
    return fs;
}

// ------------------------------------------------------------------ reglages grises
// Un reglage qui n'agit pas avec la camera et les choix actuels est grise (il garde sa valeur).
double ui_value(const PF_ParamDef* p) {
    switch (p->param_type) {
        case PF_Param_FLOAT_SLIDER: return p->u.fs_d.value;
        case PF_Param_CHECKBOX: return p->u.bd.value ? 1.0 : 0.0;
        case PF_Param_POPUP: return p->u.pd.value;
        default: return 0.0;
    }
}

void Applicable(const double* v, bool* on) {
    const int prof = std::clamp((int)v[P_PROFILE] - 1, 0, camsim::profile_count() - 1);
    const int kind = camsim::profile_kind(prof);
    const bool bayer = kind == 0, three = kind == 1, cmyg = kind == 2, ccd = !bayer;
    const camsim::Profile& P = camsim::get_profile(prof);
    const bool has_video = bayer && (P.video_has[0] || P.video_has[1]);
    const int vmode = (int)v[P_VIDEO_MODE] - 1;
    const bool video = has_video && camsim::profile_video_mode(prof, vmode);
    double dspv[8];
    const bool dsp_profile = camsim::profile_dsp(prof, dspv);
    const int dsp_mode = (int)v[P_DSP_MODE] - 1;
    const bool dsp = dsp_mode == 1 || (dsp_mode == 0 && dsp_profile);
    double prnu, dsnu, dark;
    camsim::profile_defaults(prof, &prnu, &dsnu, &dark);
    const double dark_eff = v[P_DARK_MULT] >= 0 ? v[P_DARK_MULT] : dark;
    for (int i = 0; i < P_COUNT; ++i) on[i] = true;
    on[P_UPSCALE] = v[P_RESOLUTION] == 2 || video;
    on[P_VIDEO_MODE] = has_video;
    on[P_VIDEO_READOUT] = video;
    on[P_EXPOSURE] = on[P_TEMP] = dark_eff > 0;
    on[P_HOT_FRAC] = dark_eff > 0;
    on[P_HOT_GAIN] = dark_eff > 0 && v[P_HOT_FRAC] > 0;
    on[P_ROW_BIT_ONE] = v[P_ROW_BIT] >= 0;
    on[P_COL_BIT_ONE] = v[P_COL_BIT] >= 0;
    on[P_INTERF_PERIOD] = v[P_INTERF] > 0;
    on[P_LINE_SHIFT_MAX] = on[P_LINE_PERSIST] = v[P_LINE_SHIFT] > 0;
    on[P_ADC_BIT_ONE] = v[P_ADC_BIT] >= 0;
    for (int i = P_SMEAR; i <= P_INTERLACE; ++i) on[i] = ccd;
    for (int i = P_REG_RDX; i <= P_REG_BDY; ++i) on[i] = three;
    for (int i = P_CHROMA_BW; i <= P_LINE_CRAWL; ++i) on[i] = cmyg;
    on[P_DEMOSAIC] = bayer;
    on[P_SPECTRAL] = camsim::profile_has_spectral(prof);
    for (int i = P_KNEE; i <= P_HL_CHROMA; ++i) on[i] = dsp;
    on[P_CORING] = on[P_LIMIT] = dsp && (v[P_DETAIL_H] > 0 || v[P_DETAIL_V] > 0);
    // Temps : le sens de lecture compte pour l'obturateur et le scintillement ; le mouvement pour l'obturateur
    // et la seconde trame des cameras entrelacees.
    const bool fields = !video && (cmyg || (three && (v[P_INTERLACE] == 1 ? P.interlace : v[P_INTERLACE] == 3)));
    on[P_DIRECTION] = v[P_READOUT] > 0;
    on[P_MOTION] = v[P_READOUT] > 0 || fields;
    on[P_MOTION_QUALITY] = on[P_MOTION] && v[P_MOTION] == 1;
    on[P_FLICK_DEPTH] = on[P_FLICK_PHASE] = v[P_FLICK_HZ] > 0;
}

PF_Err UpdateParamsUI(PF_InData* in_data, PF_ParamDef* params[]) {
    PF_Err err = PF_Err_NONE;
    double v[P_COUNT];
    bool on[P_COUNT];
    for (int i = 0; i < P_COUNT; ++i) v[i] = params[i] ? ui_value(params[i]) : 0.0;
    Applicable(v, on);
    const PF_ParamUtilsSuite3* ps = nullptr;
    ERR(in_data->pica_basicP->AcquireSuite(kPFParamUtilsSuite, kPFParamUtilsSuiteVersion3, (const void**)&ps));
    for (int i = 1; i < P_COUNT && !err && ps; ++i) {
        if (PARAMS[i].kind == T_TOPIC || PARAMS[i].kind == T_END || !params[i]) continue;
        const bool disabled = (params[i]->ui_flags & PF_PUI_DISABLED) != 0;
        if (disabled == !on[i]) continue;
        PF_ParamDef copy = *params[i];
        if (on[i]) copy.ui_flags &= ~PF_PUI_DISABLED;
        else copy.ui_flags |= PF_PUI_DISABLED;
        ERR(ps->PF_UpdateParamUI(in_data->effect_ref, i, &copy));
    }
    if (ps) in_data->pica_basicP->ReleaseSuite(kPFParamUtilsSuite, kPFParamUtilsSuiteVersion3);
    return err;
}

template <typename Pix, int MAXV>
void to_float(const PF_EffectWorld* w, int x, int y, double* rgb, float* a) {
    const Pix* p = reinterpret_cast<const Pix*>(reinterpret_cast<const char*>(w->data) + (size_t)y * w->rowbytes) + x;
    rgb[0] = p->red / (double)MAXV;
    rgb[1] = p->green / (double)MAXV;
    rgb[2] = p->blue / (double)MAXV;
    *a = p->alpha / (float)MAXV;
}

PF_Err SmartRender(PF_InData* in_data, PF_OutData* out_data, PF_SmartRenderExtra* extra) {
    PF_Err err = PF_Err_NONE, err2 = PF_Err_NONE;
    PF_EffectWorld *input = nullptr, *output = nullptr;
    camsim::Timer tm;
    if (tm.on) camsim::log("CameraSim : image %lld", in_data->time_step ? (long long)(in_data->current_time / in_data->time_step) : 0LL);
    ERR(extra->cb->checkout_layer_pixels(in_data->effect_ref, P_INPUT, &input));
    tm.lap("calque d'entree (After Effects)");
    ERR(extra->cb->checkout_output(in_data->effect_ref, &output));
    double V[P_COUNT];
    std::memset(V, 0, sizeof(V));
    ERR(ReadParams(in_data, V));
    if (err || !input || !output) return err;

    const PF_WorldSuite2* ws = nullptr;
    ERR(in_data->pica_basicP->AcquireSuite(kPFWorldSuite, kPFWorldSuiteVersion2, (const void**)&ws));
    PF_PixelFormat fmt = PF_PixelFormat_INVALID;
    if (!err) ERR(ws->PF_GetPixelFormat(input, &fmt));
    if (ws) in_data->pica_basicP->ReleaseSuite(kPFWorldSuite, kPFWorldSuiteVersion2);
    if (err) return err;

    const int w = std::min(input->width, output->width), h = std::min(input->height, output->height);
    const int space = (int)V[P_SPACE];
    camsim::FrameSettings fs = Settings(V);
    const long long frame = in_data->time_step ? (long long)in_data->current_time / in_data->time_step : 0;
    fs.fps = (in_data->time_step > 0 && in_data->time_scale > 0) ? (double)in_data->time_scale / in_data->time_step : 25.0;
    const bool neighbors = NeedsNeighbors(V) && in_data->time_step != 0;
    PF_EffectWorld *wprev = nullptr, *wnext = nullptr;
    if (neighbors) {
        ERR(extra->cb->checkout_layer_pixels(in_data->effect_ref, ID_PREV, &wprev));
        ERR(extra->cb->checkout_layer_pixels(in_data->effect_ref, ID_NEXT, &wnext));
        tm.lap("images voisines (After Effects)");
    }

    try {
        camsim::ThreadShare share;              // part des coeurs de cette image (rendu multi-images)
        std::vector<float> rgb, out((size_t)w * h * 3), alpha, prev, next;
        aeio::load(input, fmt, w, h, space, rgb, &alpha);
        tm.lap("conversion d'entree");
        // Une image voisine entierement transparente (avant ou apres le calque) est ignoree.
        const bool has_prev = !err && neighbors && aeio::load(wprev, fmt, w, h, space, prev, nullptr);
        const bool has_next = !err && neighbors && aeio::load(wnext, fmt, w, h, space, next, nullptr);
        if (neighbors) tm.lap("conversion des images voisines");
        camsim::process_frame(rgb.data(), out.data(), w, h, frame, fs, has_prev ? prev.data() : nullptr,
                              has_next ? next.data() : nullptr);
        tm.lap("calcul (total)");
        aeio::store(output, fmt, w, h, space, out, alpha);
        tm.lap("conversion de sortie");
        if (tm.on) camsim::log("CameraSim : %d x %d, %s, total %.0f ms", w, h, fmt == PF_PixelFormat_ARGB128 ? "32 bits" :
                               (fmt == PF_PixelFormat_ARGB64 ? "16 bits" : "8 bits"), tm.total_ms());
    } catch (const std::bad_alloc&) {
        err = PF_Err_OUT_OF_MEMORY;
    }
    ERR2(extra->cb->checkin_layer_pixels(in_data->effect_ref, P_INPUT));
    if (neighbors) {
        ERR2(extra->cb->checkin_layer_pixels(in_data->effect_ref, ID_PREV));
        ERR2(extra->cb->checkin_layer_pixels(in_data->effect_ref, ID_NEXT));
    }
    return err;
}

}  // namespace

extern "C" DllExport PF_Err PluginDataEntryFunction2(PF_PluginDataPtr inPtr, PF_PluginDataCB2 inPluginDataCallBackPtr,
                                                     SPBasicSuite* inSPBasicSuitePtr, const char* inHostName,
                                                     const char* inHostVersion) {
    PF_Err result = PF_Err_INVALID_CALLBACK;
    result = PF_REGISTER_EFFECT_EXT2(inPtr, inPluginDataCallBackPtr, "CameraSim", "CameraSim Camera", "CameraSim",
                                     AE_RESERVED_INFO, "EffectMain", "https://example.invalid");
    return result;
}

extern "C" DllExport PF_Err EffectMain(PF_Cmd cmd, PF_InData* in_data, PF_OutData* out_data, PF_ParamDef* params[],
                                       PF_LayerDef* output, void* extra) {
    PF_Err err = PF_Err_NONE;
    try {
        switch (cmd) {
            case PF_Cmd_ABOUT: err = About(in_data, out_data, params, output); break;
            case PF_Cmd_GLOBAL_SETUP: err = GlobalSetup(in_data, out_data, params, output); break;
            case PF_Cmd_PARAMS_SETUP: err = ParamsSetup(in_data, out_data, params, output); break;
            case PF_Cmd_SMART_PRE_RENDER: err = PreRender(in_data, out_data, (PF_PreRenderExtra*)extra); break;
            case PF_Cmd_USER_CHANGED_PARAM:
            case PF_Cmd_UPDATE_PARAMS_UI: err = UpdateParamsUI(in_data, params); break;
            case PF_Cmd_SMART_RENDER: err = SmartRender(in_data, out_data, (PF_SmartRenderExtra*)extra); break;
            default: break;
        }
    } catch (PF_Err& e) {
        err = e;
    } catch (...) {
        err = PF_Err_INTERNAL_STRUCT_DAMAGED;
    }
    return err;
}
