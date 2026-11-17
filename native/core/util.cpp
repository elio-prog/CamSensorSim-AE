#define _USE_MATH_DEFINES
#include "util.h"

#include <algorithm>
#include <cmath>
#include <map>
#include <memory>
#include <mutex>

namespace camsim {

// ------------------------------------------------------------------ FFT
FFT::FFT(int n) : n_(n), m_(1), pow2_((n & (n - 1)) == 0) {
    if (pow2_) {
        m_ = n;
        return;
    }
    while (m_ < 2 * n - 1) m_ <<= 1;
    w_.resize(n);
    for (int k = 0; k < n; ++k) {
        double a = M_PI * ((double)((long long)k * k % (2LL * n))) / n;
        w_[k] = cplx(std::cos(a), -std::sin(a));           // exp(-i pi k^2 / n)
    }
    bfft_.assign(m_, cplx(0, 0));
    bfft_[0] = std::conj(w_[0]);
    for (int k = 1; k < n; ++k) bfft_[k] = bfft_[m_ - k] = std::conj(w_[k]);
    radix2(bfft_, false);
}

void FFT::radix2(std::vector<cplx>& a, bool inv) const {
    const int n = (int)a.size();
    for (int i = 1, j = 0; i < n; ++i) {
        int bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) std::swap(a[i], a[j]);
    }
    for (int len = 2; len <= n; len <<= 1) {
        double ang = 2 * M_PI / len * (inv ? 1 : -1);
        cplx wl(std::cos(ang), std::sin(ang));
        for (int i = 0; i < n; i += len) {
            cplx w(1, 0);
            for (int j = 0; j < len / 2; ++j) {
                cplx u = a[i + j], v = a[i + j + len / 2] * w;
                a[i + j] = u + v;
                a[i + j + len / 2] = u - v;
                w *= wl;
            }
        }
    }
}

void FFT::run(std::vector<cplx>& a, bool inv) const {
    if (pow2_) {
        radix2(a, inv);
        return;
    }
    // Bluestein : la DFT devient une convolution par un chirp, calculee sur une puissance de 2.
    std::vector<cplx> A(m_, cplx(0, 0));
    for (int k = 0; k < n_; ++k) A[k] = a[k] * (inv ? std::conj(w_[k]) : w_[k]);
    radix2(A, false);
    if (inv) {
        // chirp conjugue pour l'inverse : conj(B) dans le domaine temporel -> on conjugue la transformee
        std::vector<cplx> B(m_, cplx(0, 0));
        B[0] = w_[0];
        for (int k = 1; k < n_; ++k) B[k] = B[m_ - k] = w_[k];
        radix2(B, false);
        for (int i = 0; i < m_; ++i) A[i] *= B[i];
    } else {
        for (int i = 0; i < m_; ++i) A[i] *= bfft_[i];
    }
    radix2(A, true);
    for (int k = 0; k < n_; ++k) a[k] = A[k] / (double)m_ * (inv ? std::conj(w_[k]) : w_[k]);
}

void FFT::forward(std::vector<cplx>& a) const { run(a, false); }

void FFT::inverse(std::vector<cplx>& a) const {
    run(a, true);
    for (auto& v : a) v /= (double)n_;
}

namespace {
std::mutex g_fft_mutex;
std::map<int, std::shared_ptr<FFT>> g_ffts;
std::shared_ptr<FFT> get_fft(int n) {
    std::lock_guard<std::mutex> lock(g_fft_mutex);
    auto& p = g_ffts[n];
    if (!p) p = std::make_shared<FFT>(n);
    return p;
}
}  // namespace

void filter_rows(std::vector<double>& x, int rows, int n, double fs, const std::function<double(double)>& H) {
    auto fft = get_fft(n);
    std::vector<double> h(n);
    for (int k = 0; k < n; ++k) {
        int kk = (k <= n / 2) ? k : n - k;                  // frequence positive associee
        h[k] = H(kk * fs / n);
    }
    // Le filtre est reel et symetrique (H(f) = H(-f)) : deux lignes reelles passent ensemble dans une seule
    // transformee (l'une en partie reelle, l'autre en partie imaginaire) et ressortent separees.
    const int pairs = (rows + 1) / 2;
#pragma omp parallel for
    for (int pr = 0; pr < pairs; ++pr) {
        const int r0 = 2 * pr, r1 = 2 * pr + 1;
        std::vector<cplx> a(n);
        for (int j = 0; j < n; ++j) a[j] = cplx(x[(size_t)r0 * n + j], r1 < rows ? x[(size_t)r1 * n + j] : 0.0);
        fft->forward(a);
        for (int k = 0; k < n; ++k) a[k] *= h[k];
        fft->inverse(a);
        for (int j = 0; j < n; ++j) x[(size_t)r0 * n + j] = a[j].real();
        if (r1 < rows)
            for (int j = 0; j < n; ++j) x[(size_t)r1 * n + j] = a[j].imag();
    }
}

// ------------------------------------------------------------------ redimensionnements (cv2.resize)
namespace {
struct Tap {
    int src;
    double w;
};

// Table INTER_AREA en reduction (computeResizeAreaTab d'OpenCV).
std::vector<std::vector<Tap>> area_tab(int ssize, int dsize) {
    const double scale = 1.0 / ((double)dsize / ssize);         // comme cv::resize
    std::vector<std::vector<Tap>> tab(dsize);
    for (int dx = 0; dx < dsize; ++dx) {
        double fsx1 = dx * scale, fsx2 = fsx1 + scale;
        double cell = std::min(scale, ssize - fsx1);
        int sx1 = (int)std::ceil(fsx1), sx2 = (int)std::floor(fsx2);
        sx2 = std::min(sx2, ssize - 1);
        sx1 = std::min(sx1, sx2);
        if (sx1 - fsx1 > 1e-3) tab[dx].push_back({sx1 - 1, (sx1 - fsx1) / cell});
        for (int sx = sx1; sx < sx2; ++sx) tab[dx].push_back({sx, 1.0 / cell});
        if (fsx2 - sx2 > 1e-3) tab[dx].push_back({sx2, std::min(std::min(fsx2 - sx2, 1.0), cell) / cell});
    }
    return tab;
}

// Coefficients bilineaires (INTER_LINEAR) ou "area" en agrandissement, comme resize() d'OpenCV.
std::vector<std::vector<Tap>> linear_tab(int ssize, int dsize, bool area_mode) {
    const double inv = (double)dsize / ssize, scale = 1.0 / inv;          // comme cv::resize
    std::vector<std::vector<Tap>> tab(dsize);
    for (int dx = 0; dx < dsize; ++dx) {
        double fx;
        int sx;
        // Memes arrondis qu'OpenCV : position en simple precision.
        if (!area_mode) {
            float f = (float)((dx + 0.5) * scale - 0.5);
            sx = (int)std::floor(f);
            fx = (double)(f - (float)sx);
        } else {
            sx = (int)std::floor(dx * scale);
            float f = (float)((dx + 1) - (sx + 1) * inv);
            fx = f <= 0 ? 0.0 : (double)(f - std::floor(f));
        }
        if (sx < 0) { fx = 0; sx = 0; }
        if (sx >= ssize - 1) { fx = 0; sx = ssize - 1; }
        tab[dx].push_back({sx, 1.0 - fx});
        if (fx > 0) tab[dx].push_back({std::min(sx + 1, ssize - 1), fx});
    }
    return tab;
}

std::vector<double> separable(const std::vector<double>& src, int w, int h, int c, int nw, int nh,
                              const std::vector<std::vector<Tap>>& tx, const std::vector<std::vector<Tap>>& ty) {
    std::vector<double> tmp((size_t)h * nw * c, 0.0), out((size_t)nh * nw * c, 0.0);
#pragma omp parallel for
    for (int y = 0; y < h; ++y)
        for (int x = 0; x < nw; ++x)
            for (const Tap& t : tx[x])
                for (int k = 0; k < c; ++k) tmp[((size_t)y * nw + x) * c + k] += t.w * src[((size_t)y * w + t.src) * c + k];
#pragma omp parallel for
    for (int y = 0; y < nh; ++y)
        for (const Tap& t : ty[y])
            for (int x = 0; x < nw; ++x)
                for (int k = 0; k < c; ++k) out[((size_t)y * nw + x) * c + k] += t.w * tmp[((size_t)t.src * nw + x) * c + k];
    return out;
}
}  // namespace

std::vector<double> resize_area(const std::vector<double>& src, int w, int h, int c, int nw, int nh) {
    return separable(src, w, h, c, nw, nh, area_tab(w, nw), area_tab(h, nh));
}

std::vector<double> resize_linear(const std::vector<double>& src, int w, int h, int c, int nw, int nh) {
    if (nw == w && nh == h) return src;
    return separable(src, w, h, c, nw, nh, linear_tab(w, nw, false), linear_tab(h, nh, false));
}

namespace {
// Coefficients de Lanczos d'ordre 4 (interpolateLanczos4 d'OpenCV), 8 points, bords repetes.
std::vector<std::vector<Tap>> lanczos_tab(int ssize, int dsize) {
    static const double s45 = 0.70710678118654752440084436210485;
    static const double cs[8][2] = {{1, 0}, {-s45, -s45}, {0, 1}, {s45, -s45}, {-1, 0}, {s45, s45}, {0, -1}, {-s45, s45}};
    const double scale = 1.0 / ((double)dsize / ssize);         // comme cv::resize
    std::vector<std::vector<Tap>> tab(dsize);
    for (int dx = 0; dx < dsize; ++dx) {
        float ff = (float)((dx + 0.5) * scale - 0.5);
        int sx = (int)std::floor(ff);
        double fx = (double)(ff - (float)sx);
        double co[8];
        if (fx < 1.1920929e-7) {
            for (int i = 0; i < 8; ++i) co[i] = 0;
            co[3] = 1;
        } else {
            double sum = 0, y0 = -(fx + 3) * M_PI * 0.25, s0 = std::sin(y0), c0 = std::cos(y0);
            for (int i = 0; i < 8; ++i) {
                double y = -(fx + 3 - i) * M_PI * 0.25;
                co[i] = (float)((cs[i][0] * s0 + cs[i][1] * c0) / (y * y));
                sum += co[i];
            }
            for (int i = 0; i < 8; ++i) co[i] *= 1.0 / sum;
        }
        for (int i = 0; i < 8; ++i) tab[dx].push_back({std::clamp(sx - 3 + i, 0, ssize - 1), co[i]});
    }
    return tab;
}
std::vector<std::vector<Tap>> nearest_tab(int ssize, int dsize) {
    const double ifx = 1.0 / ((double)dsize / ssize);        // comme resizeNN d'OpenCV
    std::vector<std::vector<Tap>> tab(dsize);
    for (int dx = 0; dx < dsize; ++dx) tab[dx].push_back({std::min((int)std::floor(dx * ifx), ssize - 1), 1.0});
    return tab;
}
}  // namespace

std::vector<double> resize_lanczos(const std::vector<double>& src, int w, int h, int c, int nw, int nh) {
    if (nw == w && nh == h) return src;
    return separable(src, w, h, c, nw, nh, lanczos_tab(w, nw), lanczos_tab(h, nh));
}

std::vector<double> resize_nearest(const std::vector<double>& src, int w, int h, int c, int nw, int nh) {
    if (nw == w && nh == h) return src;
    return separable(src, w, h, c, nw, nh, nearest_tab(w, nw), nearest_tab(h, nh));
}

namespace {
Taps1D flatten(const std::vector<std::vector<Tap>>& tab) {
    Taps1D o;
    o.start.push_back(0);
    for (const auto& v : tab) {
        for (const Tap& t : v) { o.src.push_back(t.src); o.w.push_back(t.w); }
        o.start.push_back((int)o.src.size());
    }
    return o;
}
}  // namespace

Taps1D taps_area(int ssize, int dsize) { return flatten(area_tab(ssize, dsize)); }
Taps1D taps_linear(int ssize, int dsize) { return flatten(linear_tab(ssize, dsize, false)); }

void resize_taps(int method, int w, int h, int nw, int nh, Taps1D& tx, Taps1D& ty) {
    if (method == 1) { tx = flatten(linear_tab(w, nw, false)); ty = flatten(linear_tab(h, nh, false)); }
    else if (method == 2) { tx = flatten(nearest_tab(w, nw)); ty = flatten(nearest_tab(h, nh)); }
    else if (method == 3) {
        if (nw <= w && nh <= h) { tx = flatten(area_tab(w, nw)); ty = flatten(area_tab(h, nh)); }
        else { tx = flatten(linear_tab(w, nw, true)); ty = flatten(linear_tab(h, nh, true)); }
    } else { tx = flatten(lanczos_tab(w, nw)); ty = flatten(lanczos_tab(h, nh)); }
}

std::vector<double> resize_cv_area(const std::vector<double>& src, int w, int h, int c, int nw, int nh) {
    if (nw == w && nh == h) return src;
    if (nw <= w && nh <= h) return resize_area(src, w, h, c, nw, nh);
    return separable(src, w, h, c, nw, nh, linear_tab(w, nw, true), linear_tab(h, nh, true));
}

}  // namespace camsim
