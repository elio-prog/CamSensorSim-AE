"""Filtre passe-bas optique colle sur le capteur (filtre anti-moire).

Un filtre passe-bas classique est fait de lames birefringentes qui dedoublent
chaque rayon : une fois horizontalement, une fois verticalement. Chaque point de
l'image devient donc quatre points, ecartes de d pixels. Sa reponse en frequence est

    MTF(fx, fy) = cos(pi * d_h * fx) * cos(pi * d_v * fy)

Avec d = 1 pixel, la reponse s'annule a la frequence de Nyquist (0,5 cycle par
pixel) : c'est le reglage "theorique" qui supprime le moire. d = 0 : pas de filtre.

On applique cette reponse dans le domaine de Fourier, ce qui est exact pour un
ecart quelconque, y compris non entier.
"""
import numpy as np


def optical_low_pass(img, d_h, d_v, pad=16):
    """img : (H, W, C) en lumiere lineaire. d_h, d_v : ecart des points, en pixels."""
    if d_h <= 0 and d_v <= 0:
        return img
    h, w = img.shape[:2]
    x = np.pad(img, ((pad, pad), (pad, pad), (0, 0)), mode="reflect")
    H, W = x.shape[:2]
    fy = np.fft.fftfreq(H)[:, None]
    fx = np.fft.rfftfreq(W)[None, :]
    mtf = np.cos(np.pi * max(d_h, 0.0) * fx) * np.cos(np.pi * max(d_v, 0.0) * fy)
    spec = np.fft.rfft2(x, axes=(0, 1)) * mtf[..., None]
    out = np.fft.irfft2(spec, s=(H, W), axes=(0, 1))[pad:pad + h, pad:pad + w]
    return np.maximum(out, 0.0)      # la lumiere ne peut pas etre negative


def subpixel_shift(plane, dx, dy, pad=16):
    """Decale un plan (H, W) de (dx, dy) pixels, valeurs non entieres comprises (phase de Fourier)."""
    if dx == 0 and dy == 0:
        return plane
    h, w = plane.shape
    x = np.pad(plane, ((pad, pad), (pad, pad)), mode="reflect")
    H, W = x.shape
    fy = np.fft.fftfreq(H)[:, None]
    fx = np.fft.rfftfreq(W)[None, :]
    spec = np.fft.rfft2(x) * np.exp(-2j * np.pi * (fx * dx + fy * dy))
    return np.maximum(np.fft.irfft2(spec, s=(H, W))[pad:pad + h, pad:pad + w], 0.0)
