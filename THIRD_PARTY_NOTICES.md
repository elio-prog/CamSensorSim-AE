# Third-party notices

## Included data

### rawtoaces-data (Academy Software Foundation) – Apache License 2.0

Files in `references/third_party/rawtoaces-data/` (camera spectral sensitivities, CIE 1931 colour matching
functions, training spectra) come from the `rawtoaces` project of the Academy Software Foundation and are
distributed under the Apache License 2.0. The full license text is in
`references/third_party/rawtoaces-data/LICENSE`. The files are unmodified. Values derived from them are also
compiled into `native/core/profiles_data.h`.

### ELD camera noise parameters – MIT License

The noise model parameters in the camera profiles (`engine/camsim/profiles/*.json`, and
`native/core/profiles_data.h`) were derived from the calibrated camera parameters released by the authors of
*A Physics-based Noise Formation Model for Extreme Low-light Raw Denoising* (Kaixuan Wei, Ying Fu, Jiaolong
Yang, Hua Huang, CVPR 2020) in the ELD project (https://github.com/Vandermode/ELD), distributed under the MIT
License.

## Not included (downloaded or cited only)

- **CIE standard illuminant D65** (CIE 2019, International Commission on Illumination, Vienna,
  DOI: 10.25039/CIE.DS.hjfjmt59). Downloaded from the CIE by `tools/fetch_cie_d65.py`; values derived from it
  are compiled into `native/core/profiles_data.h`.
- **Camera colour matrices**: Adobe DNG matrices as published in LibRaw (`colordata.cpp`); only the numeric
  matrices for the profiled cameras are used, in the profile files.
- **Manufacturer datasheets** (Sony ICX409AK and others): cited, not redistributed.
- **Adobe After Effects SDK**: required to build the plugins, not included. Download it from Adobe.
- **NVIDIA CUDA Toolkit** (NVRTC): used at build time only. The plugins load the NVIDIA driver at run time and
  do not include any NVIDIA library.
