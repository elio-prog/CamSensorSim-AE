# CamSensorSim-AE

**A physically based camera simulator for Adobe After Effects.** Feed it a clean render (CG, comp, stock
footage) and it runs the image through a simulated camera: photons, sensor, electronics, analog-to-digital
converter, image processing, video stage, and optionally an analog PAL/NTSC signal and tape. Defects are the
point: noise, fixed-pattern noise, hot/dead pixels, CCD smear and blooming, rolling shutter, interlacing,
broken address lines, ADC faults, VHS dropouts, tracking errors, composite artifacts, and many more.

[Version française](README.fr.md)

> [!WARNING]
> **This project was vibe coded.** Almost all of the code was written by an AI assistant (Claude, by
> Anthropic) in conversation with the author, who directed the work, tested it in After Effects and made
> the decisions. The models are based on published research and measured data (sources below), and the
> native plugin is checked against the Python reference engine by an automated test suite, but the code has
> **not been audited by an experienced human developer**. Use it at your own risk, and keep backups of your
> projects.

## What is inside

| Part | What it is |
|---|---|
| `plugin/` | Ready-to-use After Effects plugins (Windows) and a one-click installer |
| `native/` | C++ source of the plugins: CPU core, NVIDIA GPU kernels (CUDA), After Effects glue |
| `engine/` | Python reference engine (NumPy): the "ground truth" the plugin is tested against |
| `ae/` | Older ScriptUI panel that drives the Python engine (renders sequences, video codecs) |
| `tests/` | Unit tests of the Python engine and comparisons native vs. Python, GPU vs. CPU |
| `tools/` | Profile import, previews, settings guide generator, export of profiles to C++ |
| `docs/` | Design document (in French): physics, sources, decisions, measurements |

Two effects are installed in After Effects, under **Effect > CameraSim**:

- **CameraSim** – the camera: 7 profiles (Sony a7S II, Nikon D850, Canon 5D Mark IV, 70D, 700D, a 3-CCD
  broadcast camera, a single-CCD consumer camcorder with a CMYG complementary mosaic), measured noise
  (ELD calibration), spectral color from measured sensor sensitivities, sensor faults, CCD effects, true sensor
  grid, DSLR video readout (line skipping, binning), rolling shutter and light flicker with motion estimated
  from neighbouring frames (DIS optical flow), interlaced second field, video stage (knee, detail, clipping).
- **CameraSim Analogique** – the analog signal after the camera: PAL / NTSC, composite or separate, notch or
  comb decoder, VHS / VHS LP / S-VHS / Video8 / Hi8 tape (bandwidth, FM noise, chroma noise and delay, time-base
  jitter, skew, head switching, dropouts, tracking, generation loss).

Every setting can be keyframed. Settings that do nothing with the chosen camera are greyed out.

## Requirements

- Windows 10 or 11, 64-bit.
- Adobe After Effects (developed and tested with **After Effects 2025**).
- Optional: an **NVIDIA graphics card**, GeForce GTX 900 series or newer (Maxwell to Blackwell), with an NVIDIA
  driver 528 or newer. The plugin then computes on the GPU (4 to 6 times faster). Without an NVIDIA card it
  computes on the CPU and gives the same image.

## Install the plugin (easy way)

1. Download this repository: green **Code** button > **Download ZIP**, then unzip it anywhere.
2. Close After Effects.
3. Open the `plugin` folder and double-click **`Install.bat`**. Accept the Windows administrator prompt
   (the plugins go into `C:\Program Files\Adobe\...`).
4. Start After Effects. The effects are under **Effect > CameraSim**.

The installer finds every After Effects version installed on the machine and copies both plugins into its
`Support Files\Plug-ins\CameraSim` folder. To remove them, double-click **`Uninstall.bat`**.

Manual install: copy `plugin/CameraSim.aex` and `plugin/CameraSimAnalog.aex` into
`C:\Program Files\Adobe\Adobe After Effects <version>\Support Files\Plug-ins\`.

## Quick start

1. Apply **CameraSim** to your footage (or to an adjustment layer above it).
2. Set **Espace de travail** (working space) to match your project: ACEScg linear, sRGB, or Rec.709 linear.
   For realistic results, work in 32-bit.
3. Pick a **Camera**, then an **ISO** and an **Exposition**. Everything else starts from measured values.
4. Optionally apply **CameraSim Analogique** below CameraSim in the effect stack for a PAL/NTSC/VHS look.

The settings are in French (the author's language). A visual guide to every setting, with before / after /
difference examples, can be generated with `python tools/make_fiche.py` then `python tools/build_fiche_html.py`.

The plugins write a timing log to `%TEMP%\CameraSim_temps.txt` (time spent in each stage, and why the GPU was
not used if that happens).

## Build from source

Needed: Visual Studio 2022 or newer with C++ and CMake, and the **After Effects SDK** (free download from
Adobe, not included here). Optional: **CUDA Toolkit 12.9** for the GPU kernels (the kernels are compiled with
NVRTC, so the CUDA compiler does not need to support your Visual Studio version).

```
cmake -S native -B native/build/ae -A x64 -DAE_SDK="<AE SDK>/Examples" -DCUDA_TOOLKIT="C:/Program Files/NVIDIA GPU Computing Toolkit/CUDA/v12.9"
cmake --build native/build/ae --config Release
```

The plugins are built in `native/build/ae/Release/`. Without `CUDA_TOOLKIT`, the plugins compute on the CPU only.

## Python reference engine and tests

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python tools\fetch_cie_d65.py        # CIE illuminant D65 table (for spectral color)
.venv\Scripts\python tests\test_core.py
```

The `tests/compare_*.py` scripts compare the native core (`camsim_cli.exe`, built with the plugin) to the
Python engine, and the GPU to the CPU. Video encoding in the Python engine uses `ffmpeg` (on the PATH, or set
its path in `config.json`, see `config.example.json`).

## Not in the plugin yet

Tube cameras and image lag, auto exposure / auto white balance, frame tearing, and digital recording codecs
(DV, MJPEG, MPEG-2, H.264) exist in the Python engine but not yet in the plugin. Camcorder, 3-CCD, DSLR video
mode and the video stage still compute on the CPU.

## Sources

The physics and the data come from published work. The main ones:

- Noise: Wei et al., *A Physics-based Noise Formation Model for Extreme Low-light Raw Denoising* (CVPR 2020) and
  its calibrated camera parameters (ELD project, MIT license).
- Spectral sensitivities: Academy Software Foundation `rawtoaces` data (Apache-2.0); spectral upsampling:
  Jakob & Hanika, *A Low-Dimensional Function Space for Efficient Spectral Upsampling* (2019).
- Camera color matrices: Adobe DNG matrices as distributed by LibRaw.
- Inverse processing of sRGB images: Brooks et al., *Unprocessing Images for Learned Raw Denoising* (CVPR 2019).
- Optical flow: Kroeger et al., *Fast Optical Flow using Dense Inverse Search* (ECCV 2016), as in OpenCV.
- CCD and camcorder: manufacturer datasheets (Sony ICX409AK and others, not redistributed), Janesick,
  *Photon Transfer* (SPIE).

See `docs/CAHIER_DES_CHARGES.md` for the full list and the reasoning behind each model, and
`THIRD_PARTY_NOTICES.md` for the licenses of included data.

## License

MIT, see [LICENSE](LICENSE). Included third-party data keep their own licenses (see `THIRD_PARTY_NOTICES.md`).
After Effects is a trademark of Adobe; this project is not affiliated with or endorsed by Adobe, Sony, Canon,
Nikon or NVIDIA. Camera names only identify which published measurements a profile uses.
