#include "frame.h"

#include <omp.h>

#include <algorithm>
#include <atomic>
#include <cmath>

#include "grid.h"
#include "log.h"
#include "shutter.h"
#include "../gpu/gpu_flow.h"
#include "util.h"

namespace camsim {

namespace {

// (regroupement vertical, regroupement horizontal, saut vertical) : video.READOUTS
const int READOUT[4][3] = {{1, 1, 1}, {1, 3, 3}, {3, 3, 1}, {2, 1, 1}};

std::vector<double> to_image(const std::vector<double>& img, int w, int h, int nw, int nh, int method) {
    if (w == nw && h == nh) return img;
    if (method == 1) return resize_linear(img, w, h, 3, nw, nh);
    if (method == 2) return resize_nearest(img, w, h, 3, nw, nh);
    return resize_lanczos(img, w, h, 3, nw, nh);
}

// Retour a la taille de l'image (0 Lanczos, 1 bilineaire, 2 plus proche), sur la carte graphique si possible.
std::vector<float> to_image_f(const std::vector<float>& img, int w, int h, int nw, int nh, int method, bool use_gpu) {
    if (w == nw && h == nh) return img;
    std::vector<float> out;
    if (use_gpu && gpu::resize_rgb(img.data(), w, h, nw, nh, method, out)) return out;
    const std::vector<double> d = to_image(std::vector<double>(img.begin(), img.end()), w, h, nw, nh, method);
    return std::vector<float>(d.begin(), d.end());
}

std::vector<float> to_grid(const float* img, int w, int h, int ws, int hs, bool use_gpu = false) {
    std::vector<float> out;
    if (use_gpu && gpu::resize_rgb(img, w, h, ws, hs, 3, out)) return out;
    std::vector<double> d(img, img + (size_t)w * h * 3);
    d = resize_cv_area(d, w, h, 3, ws, hs);
    return std::vector<float>(d.begin(), d.end());
}

bool is_interlaced(const Profile& P, const Settings& s) {
    return P.kind == KIND_CMYG || (s.interlace < 0 ? P.interlace : s.interlace == 1);
}

// Obturateur deroulant puis scintillement (pipeline.CameraSim.shutter).
std::vector<float> shutter(const float* cur, const float* prev, const float* next, int w, int h, int64_t frame,
                           const FrameSettings& fs, std::vector<float>* flow_next = nullptr) {
    std::vector<float> x = rolling_shutter(cur, prev, next, w, h, fs.fps, fs.readout_time_s, fs.bottom_to_top, flow_next,
                                           fs.fast_motion ? 2 : 1, fs.cam.use_gpu);
    const std::vector<double> g = flicker_gain(h, frame, fs.fps, fs.readout_time_s, fs.cam.exposure_time_s, fs.flicker_hz,
                                               fs.flicker_depth, fs.flicker_phase, fs.bottom_to_top);
    if (!g.empty())
        for (int y = 0; y < h; ++y)
            for (size_t i = (size_t)y * w * 3; i < (size_t)(y + 1) * w * 3; ++i) x[i] = (float)(x[i] * g[y]);
    return x;
}

// After Effects calcule plusieurs images a la fois (rendu multi-images). Chaque image ne prend qu'une part des
// coeurs du processeur, sinon les calculs se genent (trop de fils pour trop peu de coeurs).
std::atomic<int> g_active{0};

thread_local int t_depth = 0;

}  // namespace

ThreadShare::ThreadShare() {
    if (t_depth++ > 0) return;                 // deja compte pour ce fil
    counted_ = true;
    const int a = ++g_active;
    omp_set_num_threads(std::max(1, omp_get_num_procs() / std::max(a, 1)));
}

ThreadShare::~ThreadShare() {
    --t_depth;
    if (counted_) --g_active;
}

bool profile_video_mode(int profile, int mode) {
    const Profile& P = get_profile(profile);
    return mode >= 1 && mode <= 2 && P.kind == KIND_BAYER && P.video_has[mode - 1];
}

bool frame_needs_neighbors(const FrameSettings& fs) {
    const Profile& P = get_profile(fs.cam.profile);
    const bool video = fs.video_mode && profile_video_mode(fs.cam.profile, fs.video_mode);
    return fs.readout_time_s > 0 || (!video && P.kind != KIND_BAYER && is_interlaced(P, fs.cam));
}

bool process_frame(const float* rgb_in, float* rgb_out, int w, int h, int64_t frame, const FrameSettings& fs,
                   const float* prev, const float* next) {
    ThreadShare share;
    Timer tm;
    const Profile& P = get_profile(fs.cam.profile);
    bool ok = true;
    // Etage video : du profil, ou force.
    DspParams dsp = fs.dsp;
    dsp.enabled = fs.dsp_mode == 1 || (fs.dsp_mode == 0 && P.dsp_enabled);
    const double gain = std::pow(2.0, fs.cam.ev);
    const bool interlaced = is_interlaced(P, fs.cam);

    std::vector<float> res;
    int vmode = fs.video_mode;
    if (vmode && !profile_video_mode(fs.cam.profile, vmode)) {
        ok = false;
        vmode = 0;
    }
    if (vmode) {
        // Obturateur a la taille de l'image, lecture video de l'appareil photo (video.video_frame),
        // taille de la video, etage video, retour.
        const std::vector<float> x = shutter(rgb_in, prev, next, w, h, frame, fs);
        const int m = vmode - 1;
        const int ro = fs.video_readout >= 0 ? std::min(fs.video_readout, 3) : P.video_readout[m];
        VideoSpec vs = {P.video_window[m][0], P.video_window[m][1], P.video_size[m][0], P.video_size[m][1],
                        READOUT[ro][0], READOUT[ro][1], READOUT[ro][2]};
        int hs, ws;
        sensor_grid(vs.window_w, vs.window_h, P.pitch_h, P.pitch_v, h, w, &hs, &ws);
        int ow, oh;
        std::vector<float> r = process_impl(x.data(), w, h, frame, fs.cam, 0, 0, &vs, &ow, &oh);
        const int th = std::abs(hs - vs.window_h) <= 2 ? vs.size_h : std::max(2, (int)std::nearbyint((double)hs * vs.size_h / vs.window_h));
        const int tw = std::abs(ws - vs.window_w) <= 2 ? vs.size_w : std::max(2, (int)std::nearbyint((double)ws * vs.size_w / vs.window_w));
        std::vector<double> v(r.begin(), r.end());
        v = (tw < ow) ? resize_cv_area(v, ow, oh, 3, tw, th) : resize_linear(v, ow, oh, 3, tw, th);
        std::vector<float> f(v.begin(), v.end());
        dsp_apply(f, tw, th, dsp, false, gain);
        const std::vector<double> o = to_image(std::vector<double>(f.begin(), f.end()), tw, th, w, h, fs.upscale);
        res.assign(o.begin(), o.end());
    } else {
        // Vraie grille du capteur (resolution.py) ou un pixel par photosite. Les images voisines suivent
        // la meme grille ; le mouvement y est estime.
        const bool grid = fs.resolution == 1 && P.sensor_w > 0;
        int hs = h, ws = w;
        if (grid) sensor_grid(P.sensor_w, P.sensor_h, P.pitch_h, P.pitch_v, h, w, &hs, &ws);
        std::vector<float> cg, pg, ng;
        const float *c = rgb_in, *pv = prev, *nx = next;
        if (hs != h || ws != w) {
            const bool g = fs.cam.use_gpu;
            cg = to_grid(rgb_in, w, h, ws, hs, g);
            c = cg.data();
            if (prev) { pg = to_grid(prev, w, h, ws, hs, g); pv = pg.data(); }
            if (next) { ng = to_grid(next, w, h, ws, hs, g); nx = ng.data(); }
        }
        std::vector<float> flow;
        if (hs != h || ws != w) tm.lap("vraie grille");
        const std::vector<float> x = shutter(c, pv, nx, ws, hs, frame, fs, &flow);
        tm.lap("obturateur et scintillement");
        // Seconde trame : la scene une demi-image plus tard (shutter.half_frame_later). Le mouvement vers
        // l'image suivante, s'il a deja ete mesure pour l'obturateur, est reutilise.
        std::vector<float> field2;
        if (P.kind != KIND_BAYER && interlaced && nx) {
            if (flow.empty()) flow = estimate_flow(x.data(), nx, ws, hs, fs.fast_motion ? 2 : 1, fs.cam.use_gpu);
            field2 = warp_rows(x, ws, hs, 3, flow, std::vector<double>(hs, 0.5));
        }
        if (!field2.empty()) tm.lap("seconde trame");
        std::vector<float> o((size_t)ws * hs * 3);
        process(x.data(), o.data(), ws, hs, frame, fs.cam, 0, 0, field2.empty() ? nullptr : field2.data());
        tm.lap("camera");
        dsp_apply(o, ws, hs, dsp, interlaced, gain);
        if (dsp.enabled) tm.lap("etage video");
        if (ws == w && hs == h) res.swap(o);
        else res = to_image_f(o, ws, hs, w, h, fs.upscale, fs.cam.use_gpu);
    }
    AnalogParams an = fs.analog;
    if (an.standard != Standard::Off) {
        an.seed = fs.cam.seed;
        an.use_gpu = fs.cam.use_gpu;
        analog_apply(res, w, h, frame, an);
    }
    std::copy(res.begin(), res.end(), rgb_out);
    tm.lap("fin");
    return ok;
}

}  // namespace camsim
