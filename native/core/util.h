// Outils communs du coeur natif : transformee de Fourier de toute longueur, filtres le long des
// lignes, redimensionnements identiques a ceux d'OpenCV (utilises par le moteur Python).
#pragma once
#include <complex>
#include <cstddef>
#include <functional>
#include <vector>

namespace camsim {

using cplx = std::complex<double>;

// Transformee de Fourier discrete exacte de longueur n quelconque (Bluestein sur une puissance de 2).
class FFT {
public:
    explicit FFT(int n);
    int size() const { return n_; }
    void forward(std::vector<cplx>& a) const;   // X[k] = somme x[j] exp(-2 i pi jk/n)
    void inverse(std::vector<cplx>& a) const;   // divise par n
private:
    int n_, m_;
    bool pow2_;
    std::vector<cplx> w_, bfft_;               // chirp et sa transformee
    void radix2(std::vector<cplx>& a, bool inv) const;
    void run(std::vector<cplx>& a, bool inv) const;
};

// Filtre reel H(f) (f en Hz, fs frequence d'echantillonnage) applique a chaque ligne de x (rows x n),
// comme numpy.fft.irfft(rfft(x) * H) : convolution circulaire le long de la ligne.
void filter_rows(std::vector<double>& x, int rows, int n, double fs, const std::function<double(double)>& H);

// Redimensionnements d'une image entrelacee (h x w x c), comme cv2.resize.
std::vector<double> resize_area(const std::vector<double>& src, int w, int h, int c, int nw, int nh);
std::vector<double> resize_linear(const std::vector<double>& src, int w, int h, int c, int nw, int nh);
std::vector<double> resize_lanczos(const std::vector<double>& src, int w, int h, int c, int nw, int nh);
std::vector<double> resize_nearest(const std::vector<double>& src, int w, int h, int c, int nw, int nh);
// Choix d'OpenCV pour INTER_AREA : moyenne par surface en reduction, bilineaire en agrandissement.
std::vector<double> resize_cv_area(const std::vector<double>& src, int w, int h, int c, int nw, int nh);

// Coefficients d'un redimensionnement sur un axe (comme cv::resize), en table compacte : la sortie d prend
// les entrees src[start[d] .. start[d + 1]) avec les poids w (dans cet ordre). Pour la carte graphique.
struct Taps1D {
    std::vector<int> start, src;
    std::vector<double> w;
};
Taps1D taps_area(int ssize, int dsize);       // INTER_AREA en reduction
Taps1D taps_linear(int ssize, int dsize);     // INTER_LINEAR
// Tables d'un redimensionnement d'image : 0 Lanczos, 1 bilineaire, 2 plus proche, 3 INTER_AREA (resize_cv_area,
// qui choisit selon les deux axes : tx pour la largeur, ty pour la hauteur).
void resize_taps(int method, int w, int h, int nw, int nh, Taps1D& tx, Taps1D& ty);

}  // namespace camsim
