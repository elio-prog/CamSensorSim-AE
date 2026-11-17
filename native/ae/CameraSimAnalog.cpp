// CameraSim Analogique, effet After Effects (SmartFX, 8 / 16 / 32 bits, rendu multi-images).
// Le signal video analogique apres la camera : liaison PAL / NTSC (composite ou separee) et enregistrement
// sur bande. Marche sur n'importe quelle image, avec ou sans l'effet CameraSim devant ; se place apres lui
// (meme ordre que dans le moteur Python : camera, etage video, analogique). Coeur : native/core/post.h.
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
#include <vector>

#include "post.h"
#include "frame.h"
#include "ae_io.h"
#include "log.h"

namespace {

enum Kind { T_TOPIC, T_END, T_FLOAT, T_CHECK, T_POPUP };

struct Param {
    Kind kind;
    const char* name;
    double vmin, vmax, smin, smax, dflt;
    int precision;
    const char* choices;
};

// L'ordre fixe les identifiants : ne pas le changer sans changer la version de l'effet.
enum Id {
    P_INPUT = 0,
    P_SPACE, P_STANDARD, P_FRAMING, P_COMPOSITE, P_COMB, P_LINK_SNR, P_HUE, P_SEED,
    G_TAPE, P_TAPE, P_LUMA_BW, P_TAPE_CHROMA_BW, P_TAPE_SNR, P_TAPE_CSNR, P_CDELAY, P_JITTER, P_SKEW, P_HEAD_LINES,
    P_HEAD_US, P_DROPOUTS, P_DOC, P_TRACKING, P_GENERATIONS, P_SHARPNESS, G_TAPE_END,
    P_DEVICE,                                    // 1.1 : ajoute a la fin (identifiant neuf)
    P_COUNT
};

const Param PARAMS[P_COUNT] = {
    {T_TOPIC, ""},
    {T_POPUP, "Espace de travail", 0, 0, 0, 0, 1, 0, "ACEScg lineaire|sRGB (non lineaire)|Rec.709 lineaire"},
    {T_POPUP, "Signal", 0, 0, 0, 0, 2, 0, "Aucun|PAL|NTSC"},
    {T_POPUP, "Cadrage dans l'image 4:3", 0, 0, 0, 0, 1, 0, "Bandes noires|Anamorphose (remplit l'image)"},
    {T_CHECK, "Liaison composite (sous-porteuse)", 0, 0, 0, 0, 1},
    {T_CHECK, "Decodeur en peigne", 0, 0, 0, 0, 0},
    {T_FLOAT, "Bruit de la liaison (dB)", 10, 300, 25, 60, 55, 1},
    {T_FLOAT, "Erreur de teinte (NTSC, degres)", -180, 180, -30, 30, 0, 1},
    {T_FLOAT, "Graine du hasard", 1, 99999, 1, 999, 1, 0},
    {T_TOPIC, "Bande"},
    {T_POPUP, "Format de bande", 0, 0, 0, 0, 2, 0, "Aucune|VHS|VHS (LP)|S-VHS|Video8|Hi8"},
    {T_FLOAT, "Bande passante luminance (MHz, -1 = bande)", -1, 10, -1, 5, -1, 2},
    {T_FLOAT, "Bande passante couleur (MHz, -1 = bande)", -1, 5, -1, 0.6, -1, 3},
    {T_FLOAT, "Bruit de la bande (dB, -1 = bande)", -1, 300, -1, 46, -1, 1},
    {T_FLOAT, "Bruit de la couleur (dB, -1 = bande)", -1, 300, -1, 40, -1, 1},
    {T_FLOAT, "Retard de la couleur (us, -1 = bande)", -1, 10, -1, 1, -1, 3},
    {T_FLOAT, "Instabilite des lignes (us, -1 = bande)", -1, 10, -1, 0.5, -1, 3},
    {T_FLOAT, "Haut de l'image qui penche (us, -1 = bande)", -1, 20, -1, 3, -1, 2},
    {T_FLOAT, "Lignes de commutation des tetes (-1 = bande)", -1, 100, -1, 12, -1, 0},
    {T_FLOAT, "Decalage de commutation (us, -1 = bande)", -1, 50, -1, 8, -1, 2},
    {T_FLOAT, "Pertes de signal par image", 0, 1000, 0, 20, 0, 1},
    {T_CHECK, "Compensateur de pertes", 0, 0, 0, 0, 1},
    {T_FLOAT, "Bande mal calee", 0, 1, 0, 1, 0, 2},
    {T_FLOAT, "Copies successives", 1, 10, 1, 5, 1, 0},
    {T_FLOAT, "Accentuation du magnetoscope", 0, 5, 0, 1.5, 0, 2},
    {T_END, ""},
    {T_POPUP, "Calcul", 0, 0, 0, 0, 1, 0, "Carte graphique NVIDIA (si disponible)|Processeur"},
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

int count_choices(const char* s) {
    int n = 1;
    for (; *s; ++s) n += (*s == '|');
    return n;
}

PF_Err About(PF_InData* in_data, PF_OutData* out_data, PF_ParamDef* params[], PF_LayerDef* output) {
    PF_SPRINTF(out_data->return_msg, "CameraSim Analogique 1.0\rSignal video PAL / NTSC et enregistrement sur bande. "
                                     "A placer apres l'effet CameraSim.");
    return PF_Err_NONE;
}

PF_Err GlobalSetup(PF_InData* in_data, PF_OutData* out_data, PF_ParamDef* params[], PF_LayerDef* output) {
    out_data->my_version = PF_VERSION(1, 1, 0, PF_Stage_DEVELOP, 1);
    char tmp[MAX_PATH] = {0};
    if (GetTempPathA(MAX_PATH, tmp)) camsim::set_log_file((std::string(tmp) + "CameraSim_temps.txt").c_str());
    out_data->out_flags = PF_OutFlag_DEEP_COLOR_AWARE | PF_OutFlag_NON_PARAM_VARY | PF_OutFlag_SEND_UPDATE_PARAMS_UI;
    out_data->out_flags2 = PF_OutFlag2_SUPPORTS_SMART_RENDER | PF_OutFlag2_FLOAT_COLOR_AWARE |
                           PF_OutFlag2_SUPPORTS_THREADED_RENDERING;
    return PF_Err_NONE;
}

PF_Err ParamsSetup(PF_InData* in_data, PF_OutData* out_data, PF_ParamDef* params[], PF_LayerDef* output) {
    PF_Err err = PF_Err_NONE;
    PF_ParamDef def;
    for (int id = 1; id < P_COUNT && !err; ++id) {
        const Param& p = PARAMS[id];
        AEFX_CLR_STRUCT(def);
        switch (p.kind) {
            case T_TOPIC: PF_ADD_TOPIC(p.name, id); break;
            case T_END: PF_END_TOPIC(id); break;
            case T_FLOAT:
                PF_ADD_FLOAT_SLIDERX(p.name, p.vmin, p.vmax, p.smin, p.smax, p.dflt, p.precision, PF_ValueDisplayFlag_NONE,
                                     PF_ParamFlag_SUPERVISE, id);
                break;
            case T_CHECK: PF_ADD_CHECKBOXX(p.name, p.dflt != 0 ? 1 : 0, PF_ParamFlag_SUPERVISE, id); break;
            case T_POPUP: PF_ADD_POPUPX(p.name, count_choices(p.choices), (int)p.dflt, p.choices, PF_ParamFlag_SUPERVISE, id); break;
        }
    }
    out_data->num_params = P_COUNT;
    return err;
}

double ui_value(const PF_ParamDef* p) {
    switch (p->param_type) {
        case PF_Param_FLOAT_SLIDER: return p->u.fs_d.value;
        case PF_Param_CHECKBOX: return p->u.bd.value ? 1.0 : 0.0;
        case PF_Param_POPUP: return p->u.pd.value;
        default: return 0.0;
    }
}

// Un reglage qui n'agit pas avec les choix actuels est grise (il garde sa valeur).
void Applicable(const double* v, bool* on) {
    const bool sig = v[P_STANDARD] > 1, tape = sig && v[P_TAPE] > 1, compo = sig && v[P_COMPOSITE] != 0;
    for (int i = 0; i < P_COUNT; ++i) on[i] = sig;
    on[P_SPACE] = on[P_STANDARD] = on[P_DEVICE] = true;
    on[P_COMB] = compo;
    on[P_HUE] = compo && v[P_STANDARD] == 3;
    for (int i = P_LUMA_BW; i <= P_SHARPNESS; ++i) on[i] = tape;
    on[P_DOC] = tape && v[P_DROPOUTS] > 0;
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

PF_Err PreRender(PF_InData* in_data, PF_OutData* out_data, PF_PreRenderExtra* extra) {
    PF_Err err = PF_Err_NONE;
    PF_RenderRequest req = extra->input->output_request;
    PF_CheckoutResult in_result;
    // Le signal est calcule sur l'image entiere (trame, lignes) : on demande toujours le calque entier.
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
    return err;
}

PF_Err ReadParams(PF_InData* in_data, double* v) {
    PF_Err err = PF_Err_NONE, err2 = PF_Err_NONE;
    for (int i = 1; i < P_COUNT && !err; ++i) {
        if (PARAMS[i].kind == T_TOPIC || PARAMS[i].kind == T_END) continue;
        PF_ParamDef p;
        AEFX_CLR_STRUCT(p);
        ERR(PF_CHECKOUT_PARAM(in_data, i, in_data->current_time, in_data->time_step, in_data->time_scale, &p));
        if (!err) v[i] = ui_value(&p);
        ERR2(PF_CHECKIN_PARAM(in_data, &p));
    }
    return err;
}

camsim::AnalogParams Settings(const double* v) {
    camsim::AnalogParams a;
    a.standard = (camsim::Standard)std::clamp((int)v[P_STANDARD] - 1, 0, 2);
    a.anamorphic = v[P_FRAMING] == 2;
    a.composite = v[P_COMPOSITE] != 0;
    a.comb = v[P_COMB] != 0;
    a.link_snr_db = v[P_LINK_SNR];
    a.hue_deg = v[P_HUE];
    a.seed = (uint32_t)std::max(1.0, v[P_SEED]);
    a.tape = (camsim::Tape)std::clamp((int)v[P_TAPE] - 1, 0, 5);
    a.luma_bw_mhz = v[P_LUMA_BW];
    a.chroma_bw_mhz = v[P_TAPE_CHROMA_BW];
    a.snr_db = v[P_TAPE_SNR];
    a.chroma_snr_db = v[P_TAPE_CSNR];
    a.chroma_delay_us = v[P_CDELAY];
    a.jitter_us = v[P_JITTER];
    a.skew_us = v[P_SKEW];
    a.head_switch_lines = v[P_HEAD_LINES];
    a.head_switch_us = v[P_HEAD_US];
    a.dropouts = v[P_DROPOUTS];
    a.dropout_comp = v[P_DOC] != 0;
    a.tracking = v[P_TRACKING];
    a.generations = std::max(1, (int)v[P_GENERATIONS]);
    a.sharpness = v[P_SHARPNESS];
    a.use_gpu = v[P_DEVICE] == 1;
    return a;
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
    ERR(extra->cb->checkout_layer_pixels(in_data->effect_ref, P_INPUT, &input));
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
    const camsim::AnalogParams ap = Settings(V);
    const long long frame = in_data->time_step ? (long long)in_data->current_time / in_data->time_step : 0;

    try {
        camsim::ThreadShare share;              // part des coeurs de cette image (rendu multi-images)
        camsim::Timer tm;
        if (tm.on) camsim::log("CameraSim Analogique : image %lld, %d x %d", frame, w, h);
        std::vector<float> rgb, alpha;
        aeio::load(input, fmt, w, h, space, rgb, &alpha);
        tm.lap("conversion d'entree");
        camsim::analog_apply(rgb, w, h, frame, ap);
        tm.lap("signal analogique");
        aeio::store(output, fmt, w, h, space, rgb, alpha);
        tm.lap("conversion de sortie");
    } catch (const std::bad_alloc&) {
        err = PF_Err_OUT_OF_MEMORY;
    }
    ERR2(extra->cb->checkin_layer_pixels(in_data->effect_ref, P_INPUT));
    return err;
}

}  // namespace

extern "C" DllExport PF_Err PluginDataEntryFunction2(PF_PluginDataPtr inPtr, PF_PluginDataCB2 inPluginDataCallBackPtr,
                                                     SPBasicSuite* inSPBasicSuitePtr, const char* inHostName,
                                                     const char* inHostVersion) {
    PF_Err result = PF_Err_INVALID_CALLBACK;
    result = PF_REGISTER_EFFECT_EXT2(inPtr, inPluginDataCallBackPtr, "CameraSim Analogique", "CameraSim Analog",
                                     "CameraSim", AE_RESERVED_INFO, "EffectMain", "https://example.invalid");
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
            case PF_Cmd_USER_CHANGED_PARAM:
            case PF_Cmd_UPDATE_PARAMS_UI: err = UpdateParamsUI(in_data, params); break;
            case PF_Cmd_SMART_PRE_RENDER: err = PreRender(in_data, out_data, (PF_PreRenderExtra*)extra); break;
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
