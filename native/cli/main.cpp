// Outil de comparaison : applique le coeur natif a une image brute (float32, L x H x 3, ACEScg lineaire).
//   camsim_cli entree.raw sortie.raw largeur hauteur image [cle=valeur ...]
// Cles : profile iso ev exposure_time_s temperature_c seed shot_noise read_model read_noise_gain
//        row_noise_gain color_bias_gain prnu_sigma dsnu_rel_sigma dark_current olpf_h_px olpf_v_px
//        clip_full_well quantize adc_bits demosaic wb_red wb_blue clip_highlights spectral x0 y0
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "../core/camsim.h"
#include "../core/frame.h"
#include "../core/post.h"
#include "../core/shutter.h"
#include "../gpu/gpu.h"

int main(int argc, char** argv) {
    if (argc < 6) {
        std::fprintf(stderr, "usage : camsim_cli entree.raw sortie.raw largeur hauteur image [cle=valeur ...]\n");
        for (int i = 0; i < camsim::profile_count(); ++i) std::fprintf(stderr, "  profil %d : %s\n", i, camsim::profile_key(i));
        return 2;
    }
    const int w = std::atoi(argv[3]), h = std::atoi(argv[4]);
    const long long frame = std::atoll(argv[5]);
    camsim::Settings s;
    camsim::DspParams dsp;
    camsim::AnalogParams an;
    bool camera = true, interlaced = false;
    int resolution = 0, upscale = 0, video_mode = 0, video_readout = -1, dsp_mode = 2;   // etage video coupe par defaut
    double gain = 1.0;
    bool dsp_enabled_given = false;
    int x0 = 0, y0 = 0;
    std::string prev_path, next_path;
    bool flow_only = false;
    int warmup = 0;
    double fps = 25.0, readout = 0.0, flicker_hz = 0.0, flicker_depth = 0.0, flicker_phase = 0.0;
    bool bottom_to_top = false, fast_motion = false;
    for (int a = 6; a < argc; ++a) {
        std::string kv = argv[a];
        size_t eq = kv.find('=');
        if (eq == std::string::npos) continue;
        std::string k = kv.substr(0, eq);
        double v = std::atof(kv.c_str() + eq + 1);
        bool known = true;
        if (k == "prev") prev_path = kv.substr(eq + 1);
        else if (k == "next") next_path = kv.substr(eq + 1);
        else if (k == "flow_only") flow_only = v != 0;          // sortie : flux vers "next" (x, y, 0)
        else if (k == "flow_grad_iters") camsim::g_flow.grad_iters = (int)v;
        else if (k == "flow_refine_iters") camsim::g_flow.refine_iters = (int)v;
        else if (k == "flow_refine_norm") camsim::g_flow.refine_norm = v;
        else if (k == "flow_alpha") camsim::g_flow.alpha = v;
        else if (k == "fps") fps = v;
        else if (k == "gpu") s.use_gpu = v != 0;
        else if (k == "warmup") warmup = (int)v;   // calculs prealables non chronometres (premier lancement)
        else if (k == "fast_motion") fast_motion = v != 0;
        else if (k == "readout_time_s") readout = v;
        else if (k == "bottom_to_top") bottom_to_top = v != 0;
        else if (k == "flicker_hz") flicker_hz = v;
        else if (k == "flicker_depth") flicker_depth = v;
        else if (k == "flicker_phase") flicker_phase = v;
        else if (k == "profile") s.profile = (int)v;
        else if (k == "iso") s.iso = v;
        else if (k == "ev") s.ev = v;
        else if (k == "exposure_time_s") s.exposure_time_s = v;
        else if (k == "temperature_c") s.temperature_c = v;
        else if (k == "seed") s.seed = (uint32_t)v;
        else if (k == "shot_noise") s.shot_noise = v != 0;
        else if (k == "read_model") s.read_model = (camsim::ReadModel)(int)v;
        else if (k == "read_noise_gain") s.read_noise_gain = v;
        else if (k == "row_noise_gain") s.row_noise_gain = v;
        else if (k == "color_bias_gain") s.color_bias_gain = v;
        else if (k == "prnu_sigma") s.prnu_sigma = v;
        else if (k == "dsnu_rel_sigma") s.dsnu_rel_sigma = v;
        else if (k == "dark_current") s.dark_current = v;
        else if (k == "olpf_h_px") s.olpf_h_px = v;
        else if (k == "olpf_v_px") s.olpf_v_px = v;
        else if (k == "clip_full_well") s.clip_full_well = v != 0;
        else if (k == "quantize") s.quantize = v != 0;
        else if (k == "adc_bits") s.adc_bits = (int)v;
        else if (k == "demosaic") s.demosaic = (camsim::Demosaic)(int)v;
        else if (k == "wb_red") s.wb_red = v;
        else if (k == "wb_blue") s.wb_blue = v;
        else if (k == "clip_highlights") s.clip_highlights = v != 0;
        else if (k == "spectral") s.spectral = v != 0;
        else known = false;
        if (known) continue;
        if (k == "hot_pixel_fraction") s.hot_pixel_fraction = v;
        else if (k == "hot_pixel_gain") s.hot_pixel_gain = v;
        else if (k == "column_fpn_dn") s.column_fpn_dn = v;
        else if (k == "column_gain_sigma") s.column_gain_sigma = v;
        else if (k == "dead_pixel_fraction") s.dead_pixel_fraction = v;
        else if (k == "stuck_pixel_fraction") s.stuck_pixel_fraction = v;
        else if (k == "dead_columns") s.dead_columns = (int)v;
        else if (k == "dead_rows") s.dead_rows = (int)v;
        else if (k == "row_address_bit") s.row_address_bit = (int)v;
        else if (k == "row_address_value") s.row_address_value = (int)v;
        else if (k == "col_address_bit") s.col_address_bit = (int)v;
        else if (k == "col_address_value") s.col_address_value = (int)v;
        else if (k == "row_address_error_rate") s.row_address_error_rate = v;
        else if (k == "col_address_error_rate") s.col_address_error_rate = v;
        else if (k == "interference_dn") s.interference_dn = v;
        else if (k == "interference_period_px") s.interference_period_px = v;
        else if (k == "adc_dnl_lsb") s.adc_dnl_lsb = v;
        else if (k == "adc_inl_lsb") s.adc_inl_lsb = v;
        else if (k == "adc_stuck_bit") s.adc_stuck_bit = (int)v;
        else if (k == "adc_stuck_value") s.adc_stuck_value = (int)v;
        else if (k == "adc_sparkle_rate") s.adc_sparkle_rate = v;
        else if (k == "bitslip") s.bitslip = (int)v;
        else if (k == "line_shift_rate") s.line_shift_rate = v;
        else if (k == "line_shift_max_px") s.line_shift_max_px = (int)v;
        else if (k == "line_shift_persist") s.line_shift_persist = v != 0;
        else if (k == "sensor_shading") s.sensor_shading = v;
        else if (k == "isp_black_offset_dn") s.isp_black_offset_dn = v;
        else if (k == "ccd_smear_gain") s.ccd_smear_gain = v;
        else if (k == "ccd_antiblooming") s.ccd_antiblooming = v;
        else if (k == "ccd_cti_v") s.ccd_cti_v = v;
        else if (k == "ccd_cti_h") s.ccd_cti_h = v;
        else if (k == "ccd_blocked_columns") s.ccd_blocked_columns = (int)v;
        else if (k == "interlace") s.interlace = (int)v;
        else if (k == "registration_r_dx") s.registration_r_dx = v;
        else if (k == "registration_r_dy") s.registration_r_dy = v;
        else if (k == "registration_b_dx") s.registration_b_dx = v;
        else if (k == "registration_b_dy") s.registration_b_dy = v;
        else if (k == "cmyg_chroma_bw_mhz") s.cmyg_chroma_bw_mhz = v;
        else if (k == "cmyg_vertical_2h") s.cmyg_vertical_2h = v != 0;
        else if (k == "cmyg_chroma_gain") s.cmyg_chroma_gain = v;
        else if (k == "cmyg_field_flicker") s.cmyg_field_flicker = v;
        else if (k == "cmyg_line_crawl") s.cmyg_line_crawl = v;
        else if (k == "resolution") resolution = (int)v;
        else if (k == "upscale") upscale = (int)v;
        else if (k == "video_mode") video_mode = (int)v;
        else if (k == "video_readout") video_readout = (int)v;
        else if (k == "dsp_mode") dsp_mode = (int)v;
        else if (k == "camera") camera = v != 0;
        else if (k == "interlaced") interlaced = v != 0;
        else if (k == "dsp_gain") gain = v;
        else if (k == "dsp_enabled") { dsp.enabled = v != 0; dsp_enabled_given = true; }
        else if (k == "dsp_knee_point") dsp.knee_point = v;
        else if (k == "dsp_knee_slope") dsp.knee_slope = v;
        else if (k == "dsp_white_clip") dsp.white_clip = v;
        else if (k == "dsp_detail_h") dsp.detail_h = v;
        else if (k == "dsp_detail_v") dsp.detail_v = v;
        else if (k == "dsp_detail_coring") dsp.coring = v;
        else if (k == "dsp_detail_limit") dsp.limit = v;
        else if (k == "dsp_highlight_chroma") dsp.highlight_chroma = v;
        else if (k == "analog_standard") an.standard = (camsim::Standard)(int)v;
        else if (k == "analog_tape") an.tape = (camsim::Tape)(int)v;
        else if (k == "analog_composite") an.composite = v != 0;
        else if (k == "analog_comb") an.comb = v != 0;
        else if (k == "analog_link_snr_db") an.link_snr_db = v;
        else if (k == "analog_hue_deg") an.hue_deg = v;
        else if (k == "analog_luma_bw_mhz") an.luma_bw_mhz = v;
        else if (k == "analog_chroma_bw_mhz") an.chroma_bw_mhz = v;
        else if (k == "analog_snr_db") an.snr_db = v;
        else if (k == "analog_chroma_snr_db") an.chroma_snr_db = v;
        else if (k == "analog_chroma_delay_us") an.chroma_delay_us = v;
        else if (k == "analog_jitter_us") an.jitter_us = v;
        else if (k == "analog_skew_us") an.skew_us = v;
        else if (k == "analog_head_switch_lines") an.head_switch_lines = v;
        else if (k == "analog_head_switch_us") an.head_switch_us = v;
        else if (k == "analog_dropouts") an.dropouts = v;
        else if (k == "analog_dropout_comp") an.dropout_comp = v != 0;
        else if (k == "analog_tracking") an.tracking = v;
        else if (k == "analog_generations") an.generations = (int)v;
        else if (k == "analog_sharpness") an.sharpness = v;
        else if (k == "analog_anamorphic") an.anamorphic = v != 0;
        else if (k == "prnu_mult" || k == "dark_mult") {   // comme l'effet AE : multiple de la valeur du profil
            double prnu, dsnu, dark;
            camsim::profile_defaults(s.profile, &prnu, &dsnu, &dark);
            if (k == "prnu_mult") s.prnu_sigma = prnu * v; else s.dark_current = dark * v;
        }
        else if (k == "x0") x0 = (int)v;
        else if (k == "y0") y0 = (int)v;
        else { std::fprintf(stderr, "cle inconnue : %s\n", k.c_str()); return 2; }
    }
    std::vector<float> in((size_t)w * h * 3), out((size_t)w * h * 3);
    FILE* f = std::fopen(argv[1], "rb");
    if (!f || std::fread(in.data(), sizeof(float), in.size(), f) != in.size()) { std::fprintf(stderr, "lecture impossible\n"); return 1; }
    std::fclose(f);
    auto load = [&](const std::string& path, std::vector<float>& dst) {
        if (path.empty()) return true;
        dst.resize(in.size());
        FILE* g = std::fopen(path.c_str(), "rb");
        bool good = g && std::fread(dst.data(), sizeof(float), dst.size(), g) == dst.size();
        if (g) std::fclose(g);
        return good;
    };
    std::vector<float> pv, nx;
    if (!load(prev_path, pv) || !load(next_path, nx)) { std::fprintf(stderr, "lecture des voisines impossible\n"); return 1; }
    if (s.use_gpu) std::fprintf(stderr, "carte graphique : %s\n", camsim::gpu::describe().c_str());
    auto work = [&]() {
    if (flow_only) {
        std::vector<float> fl = camsim::estimate_flow(in.data(), nx.data(), w, h, fast_motion ? 2 : 1, s.use_gpu);
        for (size_t i = 0; i < (size_t)w * h; ++i) { out[i * 3] = fl[i * 2]; out[i * 3 + 1] = fl[i * 2 + 1]; out[i * 3 + 2] = 0; }
    } else if (camera) {
        // Chaine complete d'une image (grille, mode video, camera, etage video, analogique).
        camsim::FrameSettings fs;
        fs.cam = s;
        fs.resolution = resolution;
        fs.upscale = upscale;
        fs.video_mode = video_mode;
        fs.video_readout = video_readout;
        fs.dsp_mode = dsp_enabled_given ? (dsp.enabled ? 1 : 2) : dsp_mode;
        fs.dsp = dsp;
        fs.analog = an;
        fs.fps = fps;
        fs.readout_time_s = readout;
        fs.bottom_to_top = bottom_to_top;
        fs.flicker_hz = flicker_hz;
        fs.flicker_depth = flicker_depth;
        fs.flicker_phase = flicker_phase;
        fs.fast_motion = fast_motion;
        if (!camsim::process_frame(in.data(), out.data(), w, h, frame, fs, pv.empty() ? nullptr : pv.data(),
                                   nx.empty() ? nullptr : nx.data()))
            std::fprintf(stderr, "mode video absent\n");
    } else {
        out = in;
        camsim::dsp_apply(out, w, h, dsp, interlaced, gain);
        an.seed = s.seed;
        an.use_gpu = s.use_gpu;
        camsim::analog_apply(out, w, h, frame, an);
    }
    };
    for (int i = 0; i < warmup; ++i) work();
    auto t0 = std::chrono::steady_clock::now();
    work();
    double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
    f = std::fopen(argv[2], "wb");
    if (!f || std::fwrite(out.data(), sizeof(float), out.size(), f) != out.size()) { std::fprintf(stderr, "ecriture impossible\n"); return 1; }
    std::fclose(f);
    std::printf("%.1f ms\n", ms);
    return 0;
}
