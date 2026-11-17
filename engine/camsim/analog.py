"""Signal analogique : bande magnetique (VHS, S-VHS, Video8, Hi8) et liaison composite PAL / NTSC.

L'image est convertie en signal video ligne par ligne, echantillonne a 4 fois la sous-porteuse
de couleur, et chaque traitement agit la ou il agit vraiment : le long des lignes (le temps),
en megahertz et en microsecondes.

1. Signal video : courbe BT.709, Y'UV (PAL) ou Y'IQ (NTSC), une ligne d'echantillons par ligne
   de balayage (576 lignes utiles en PAL, 486 en NTSC ; 52 us utiles par ligne).
2. Bande ("color-under", tous les formats grand public) :
   - luminance enregistree en modulation de frequence : bande passante limitee ; bruit de
     demodulation FM dont l'amplitude croit avec la frequence (spectre triangulaire) ;
   - couleur abaissee sous la luminance (vers 627 kHz en PAL) : bande tres etroite (0,4 MHz,
     environ 30 lignes), bruit, retard sur la luminance ;
   - defauts mecaniques : instabilite de la base de temps (lignes qui ondulent), lignes du haut
     qui penchent (reprise de la synchro), commutation des tetes en bas de l'image, pertes de
     signal (dropouts) eventuellement masquees par le compensateur, bande mal calee (tracking) ;
   - accentuation des contours du magnetoscope ; copies successives (generations).
3. Liaison composite : la couleur est modulee sur la sous-porteuse et ajoutee a la luminance
   (PAL : phase de V inversee une ligne sur deux ; NTSC : 227,5 periodes par ligne), un peu de
   bruit, puis le televiseur ou la carte d'acquisition separe luminance et couleur (filtre
   coupe-bande ou filtre en peigne) et demodule. D'ou le chenillement de points et les irisations
   sur les details fins.
Le traitement depend du numero d'image (graine), pas des images voisines : il peut se faire
image par image (compatible avec un futur effet en direct dans After Effects).

Chiffres : sous-porteuses et bandes passantes PAL B/G et NTSC M (ITU-R BT.470) ; ligne utile
de 52 us ; environ 78 lignes TV par MHz (2 x f x 52 us x 3/4) ; VHS environ 3 MHz (240 lignes),
S-VHS et Hi8 environ 5 MHz (400 lignes), couleur de tous ces formats limitee vers 0,4 MHz
(30 lignes). Rapports signal sur bruit, retards, instabilites : valeurs courantes, libres.
"""
import math

import cv2
import numpy as np

from .color import apply_matrix
from .resolution import sensor_grid
from .sources import AP1_TO_REC709, REC709_TO_AP1, bt709_decode, bt709_encode

STANDARDS = {
    "pal": dict(fsc=4.43361875e6, lines=576, active_us=52.0, luma_bw=5.0e6, c_bw=(1.3e6, 1.3e6),
                cycles_per_line=283.7516, comb_step=4, total_lines=625),
    "ntsc": dict(fsc=3.579545e6, lines=486, active_us=52.66, luma_bw=4.2e6, c_bw=(1.3e6, 0.4e6),
                 cycles_per_line=227.5, comb_step=2, total_lines=525),
}

# Formats de bande. luma/chroma en Hz ; snr en dB (signal noir-blanc / bruit efficace).
TAPES = {
    "vhs": dict(label="VHS (SP)", luma=3.0e6, chroma=0.4e6, snr=43.0, csnr=38.0, cdelay=0.15, jitter=0.04,
                skew=0.6, head=5, head_shift=3.0),
    "vhs_lp": dict(label="VHS (LP)", luma=2.6e6, chroma=0.35e6, snr=40.0, csnr=35.0, cdelay=0.2, jitter=0.07,
                   skew=0.8, head=6, head_shift=4.0),
    "svhs": dict(label="S-VHS", luma=5.0e6, chroma=0.4e6, snr=45.0, csnr=39.0, cdelay=0.12, jitter=0.03,
                 skew=0.5, head=5, head_shift=3.0),
    "video8": dict(label="Video8", luma=3.0e6, chroma=0.4e6, snr=43.0, csnr=38.0, cdelay=0.15, jitter=0.03,
                   skew=0.4, head=4, head_shift=2.5),
    "hi8": dict(label="Hi8", luma=5.0e6, chroma=0.4e6, snr=45.0, csnr=39.0, cdelay=0.12, jitter=0.03,
                skew=0.4, head=4, head_shift=2.5),
}


def lines_per_mhz(active_us=52.0):
    """Lignes TV (par hauteur d'image) pour 1 MHz : 2 x f x duree utile x 3/4."""
    return 2.0 * 1e6 * active_us * 1e-6 * 0.75


# ---------------------------------------------------------------- filtres le long des lignes
def _freqs(n, fs):
    return np.fft.rfftfreq(n, 1.0 / fs)


def lowpass(x, fc, fs, order=4):
    """Passe-bas de type Butterworth (module), applique a chaque ligne."""
    if fc <= 0 or fc >= fs / 2:
        return x
    f = _freqs(x.shape[-1], fs)
    h = 1.0 / np.sqrt(1.0 + (f / fc) ** (2 * order))
    return np.fft.irfft(np.fft.rfft(x, axis=-1) * h, n=x.shape[-1], axis=-1)


def bandpass_resp(n, fs, f0, bw):
    f = _freqs(n, fs)
    return np.exp(-0.5 * ((f - f0) / max(bw, 1.0)) ** 2)


def shift_lines(x, shift):
    """Decalage horizontal de chaque ligne (en echantillons, fractionnaire), bords repetes."""
    n = x.shape[-1]
    idx = np.arange(n)[None, :] - shift[:, None]
    i0 = np.floor(idx).astype(int)
    f = idx - i0
    a = np.take_along_axis(x, np.clip(i0, 0, n - 1), axis=-1)
    b = np.take_along_axis(x, np.clip(i0 + 1, 0, n - 1), axis=-1)
    return a * (1 - f) + b * f


# ---------------------------------------------------------------- reglages
def params(settings):
    std = settings.get("analog_standard", "off") or "off"
    if std == "off":
        return None
    if std not in STANDARDS:
        raise ValueError("analog_standard inconnu : %r" % std)
    p = dict(STANDARDS[std])
    p["std"] = std
    p["fs"] = 4.0 * p["fsc"]
    p["samples"] = int(round(p["active_us"] * 1e-6 * p["fs"]))
    tape = settings.get("analog_tape", "none") or "none"
    if tape != "none" and tape not in TAPES:
        raise ValueError("analog_tape inconnu : %r" % tape)
    t = dict(TAPES.get(tape, {}))
    p["tape"] = tape

    def get(key, default):
        v = settings.get(key)
        return float(v) if v not in (None, "") else default

    if tape != "none":
        p["luma_bw_tape"] = get("analog_luma_bw_mhz", t["luma"] / 1e6) * 1e6
        p["chroma_bw_tape"] = get("analog_chroma_bw_mhz", t["chroma"] / 1e6) * 1e6
        p["snr"] = get("analog_snr_db", t["snr"])
        p["csnr"] = get("analog_chroma_snr_db", t["csnr"])
        p["cdelay"] = get("analog_chroma_delay_us", t["cdelay"])
        p["jitter"] = get("analog_jitter_us", t["jitter"])
        p["skew"] = get("analog_skew_us", t["skew"])
        p["head"] = int(get("analog_head_switch_lines", t["head"]))
        p["head_shift"] = get("analog_head_switch_us", t["head_shift"])
    p["dropouts"] = get("analog_dropouts", 0.0)
    p["doc"] = bool(settings.get("analog_dropout_comp", True))
    p["tracking"] = get("analog_tracking", 0.0)
    p["generations"] = max(1, int(get("analog_generations", 1)))
    p["sharpness"] = get("analog_sharpness", 0.0)
    p["composite"] = bool(settings.get("analog_composite", True))
    p["decoder"] = settings.get("analog_decoder", "notch") or "notch"
    if p["decoder"] not in ("notch", "comb"):
        raise ValueError("analog_decoder inconnu : %r" % p["decoder"])
    p["link_snr"] = get("analog_link_snr_db", 55.0)
    p["hue"] = get("analog_hue_deg", 0.0)
    p["seed"] = int(settings.get("seed", 1))
    # Cadrage dans la trame 4:3 : bandes noires (format d'origine) ou anamorphose (l'image remplit toute la
    # trame, comme sur les cameras 16:9 a capteur 4:3 : elle est comprimee a l'enregistrement, etiree a la lecture).
    p["framing"] = settings.get("analog_framing", "letterbox") or "letterbox"
    if p["framing"] not in ("letterbox", "anamorphic"):
        raise ValueError("analog_framing inconnu : %r" % p["framing"])
    return p


# ---------------------------------------------------------------- couleur
# Y'UV (BT.470 / BT.601) ; en NTSC, I et Q sont U et V tournes de 33 degres.
YUV = np.array([[0.299, 0.587, 0.114], [-0.14713, -0.28886, 0.436], [0.615, -0.51499, -0.10001]])
YUV_INV = np.linalg.inv(YUV)


def _rot(a_deg):
    a = math.radians(a_deg)
    return np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])


# ---------------------------------------------------------------- bande
def tape_pass(Y, U, V, p, rng, gen):
    fs = p["fs"]
    L, N = Y.shape
    us = fs * 1e-6                                   # echantillons par microseconde
    # Accentuation du magnetoscope (avant l'enregistrement) puis bande passante FM.
    if p["sharpness"] > 0:
        Y = Y + p["sharpness"] * (Y - lowpass(Y, 1.5e6, fs, order=2))
    Y = lowpass(Y, p["luma_bw_tape"], fs)
    # Bruit FM : spectre triangulaire (amplitude proportionnelle a la frequence), limite a la bande.
    if p["snr"] < 200:
        n = rng.standard_normal(Y.shape)
        f = _freqs(N, fs)
        spec = np.fft.rfft(n, axis=-1) * (f / max(p["luma_bw_tape"], 1.0))[None, :]
        n = lowpass(np.fft.irfft(spec, n=N, axis=-1), p["luma_bw_tape"], fs)
        n *= 10 ** (-p["snr"] / 20.0) / max(n.std(), 1e-12)
        Y = Y + n
    # Couleur abaissee : bande etroite, bruit (basse frequence : trainees colorees), retard.
    U = lowpass(U, p["chroma_bw_tape"], fs, order=2)
    V = lowpass(V, p["chroma_bw_tape"], fs, order=2)
    if p["csnr"] < 200:
        for C in (U, V):
            n = lowpass(rng.standard_normal(C.shape), p["chroma_bw_tape"], fs, order=2)
            C += n * (10 ** (-p["csnr"] / 20.0) / max(n.std(), 1e-12))
    if p["cdelay"]:
        d = np.full(L, p["cdelay"] * us)
        U, V = shift_lines(U, d), shift_lines(V, d)
    # Lecture PAL ou NTSC : la couleur est moyennee sur deux lignes de la meme trame (ligne a retard).
    U = 0.5 * (U + np.concatenate([U[:2], U[:-2]], axis=0))
    V = 0.5 * (V + np.concatenate([V[:2], V[:-2]], axis=0))
    # Base de temps : marche au hasard lissee + tremblement, par trame ; haut d'image qui penche.
    off = np.zeros(L)
    for field in (0, 1):
        k = np.arange(field, L, 2)
        walk = np.cumsum(rng.standard_normal(len(k))) * 0.25 + rng.standard_normal(len(k))
        walk = cv2.GaussianBlur(walk.reshape(-1, 1), (1, 0), 2.0).ravel() if len(k) > 4 else walk
        off[k] = p["jitter"] * walk / max(np.std(walk), 1e-9)
        top = np.exp(-np.arange(len(k)) / 12.0)
        off[k] += p["skew"] * top * (0.6 + 0.4 * rng.random())
    # Commutation des tetes : les dernieres lignes de chaque trame sont decalees et bruitees.
    if p["head"] > 0:
        for field in (0, 1):
            k = np.arange(field, L, 2)[-p["head"]:]
            ramp = np.linspace(0.3, 1.0, len(k))
            off[k] += p["head_shift"] * ramp * (0.8 + 0.4 * rng.random())
            Y[k] += rng.standard_normal((len(k), N)) * 0.05 * ramp[:, None]
    sh = off * us
    Y, U, V = shift_lines(Y, sh), shift_lines(U, sh), shift_lines(V, sh)
    # Pertes de signal (dropouts) : morceaux de ligne sans signal.
    if p["dropouts"] > 0:
        n_do = rng.poisson(p["dropouts"])
        for _ in range(n_do):
            line = int(rng.integers(0, L))
            length = int(min(N, max(2, rng.exponential(12.0) * us)))
            x0 = int(rng.integers(0, max(1, N - length)))
            if p["doc"] and line >= 2:              # compensateur : on recopie la ligne d'avant (meme trame)
                for C in (Y, U, V):
                    C[line, x0:x0 + length] = C[line - 2, x0:x0 + length]
            else:
                Y[line, x0:x0 + length] = 0.6 + 0.4 * rng.random(length)
                U[line, x0:x0 + length] = 0.0
                V[line, x0:x0 + length] = 0.0
    # Bande mal calee : une bande de lignes bruitees et decalees qui monte dans l'image.
    if p["tracking"] > 0:
        c = (rng.random() * L) % L
        width = 8 + 40 * p["tracking"]
        for line in range(L):
            dist = min(abs(line - c), L - abs(line - c))
            if dist < width:
                w = 1.0 - dist / width
                if rng.random() < 0.6 * w * p["tracking"]:
                    x0 = int(rng.integers(0, N))
                    ln = int(rng.integers(N // 20, N // 2))
                    Y[line, x0:x0 + ln] = rng.random() * 0.8 + 0.4 * rng.standard_normal(min(ln, N - x0))
                Y[line] = np.roll(Y[line], int(w * p["tracking"] * 6 * us * rng.standard_normal()))
    return Y, U, V


# ---------------------------------------------------------------- composite
def composite_pass(Y, U, V, p, rng, frame):
    fs, fsc = p["fs"], p["fsc"]
    L, N = Y.shape
    t = np.arange(N) / fs
    # Phase de la sous-porteuse au debut de chaque ligne, dans l'ordre reel du balayage :
    # ligne d'image r = ligne r // 2 de la trame r % 2 ; une trame dure total_lines / 2 lignes.
    r = np.arange(L)
    scan = (r % 2) * (p["total_lines"] / 2.0) + r // 2 + frame * p["total_lines"]
    phase0 = 2 * np.pi * ((scan * p["cycles_per_line"]) % 1.0)
    w = 2 * np.pi * fsc * t[None, :] + phase0[:, None]
    if p["std"] == "pal":
        sw = np.where(np.arange(L) % 4 < 2, 1.0, -1.0)[:, None]      # inversion de V (trame par trame : 2 lignes)
        C = U * np.sin(w) + sw * V * np.cos(w)
    else:
        I, Q = np.tensordot(np.stack([U, V], -1), _rot(33.0).T, axes=1).transpose(2, 0, 1)
        C = Q * np.sin(w) + I * np.cos(w)
    comp = Y + C
    if p["link_snr"] < 200:
        comp = comp + rng.standard_normal(comp.shape) * 10 ** (-p["link_snr"] / 20.0)
    comp = lowpass(comp, p["luma_bw"], fs, order=10)      # canal video : plat jusqu'a la sous-porteuse
    # Separation de la luminance et de la couleur.
    spec = np.fft.rfft(comp, axis=-1)
    band = bandpass_resp(N, fs, fsc, max(p["c_bw"]) * 0.9)
    if p["decoder"] == "comb":
        k = p["comb_step"]                                  # ligne de meme phase opposee (meme trame)
        prev = np.concatenate([comp[:k], comp[:-k]], axis=0)
        C_sep = 0.5 * (comp - prev)
        C_sep = np.fft.irfft(np.fft.rfft(C_sep, axis=-1) * band, n=N, axis=-1)
        Y_sep = comp - C_sep
    else:
        C_sep = np.fft.irfft(spec * band, n=N, axis=-1)
        Y_sep = np.fft.irfft(spec * (1.0 - band), n=N, axis=-1)
    # Demodulation (le recepteur se cale sur la salve de reference : erreur de teinte possible en NTSC).
    hue = math.radians(p["hue"]) if p["std"] == "ntsc" else 0.0
    a = lowpass(2 * C_sep * np.sin(w + hue), p["c_bw"][0], fs, order=2)
    b = lowpass(2 * C_sep * np.cos(w + hue), p["c_bw"][0], fs, order=2)
    if p["std"] == "pal":
        U2, V2 = a, b * sw
        # Decodeur PAL a ligne a retard : moyenne de deux lignes, qui annule les erreurs de phase.
        U2 = 0.5 * (U2 + np.concatenate([U2[:2], U2[:-2]], axis=0))
        V2 = 0.5 * (V2 + np.concatenate([V2[:2], V2[:-2]], axis=0))
    else:
        Q2 = a
        I2 = lowpass(b, p["c_bw"][0], fs, order=2)
        Q2 = lowpass(Q2, p["c_bw"][1], fs, order=2)
        UV = np.tensordot(np.stack([I2, Q2], -1), _rot(33.0), axes=1)     # retour de IQ vers UV
        U2, V2 = UV[..., 0], UV[..., 1]
    return Y_sep, U2, V2


# ---------------------------------------------------------------- image complete
def region(p, h, w):
    """Zone de la trame qui recoit l'image : (lignes, echantillons, y0, x0)."""
    L, N = p["lines"], p["samples"]
    if p.get("framing") == "anamorphic":
        return L, N, 0, 0
    par = (4.0 / 3.0) / (N / L)                      # forme d'un echantillon pour une image 4:3
    hr, wr = sensor_grid({"sensor_pixels": [N, L], "pixel_pitch_um": [par, 1.0], "name": p["std"]}, h, w)
    return hr, wr, (L - hr) // 2, (N - wr) // 2


def apply(ap1, p, frame):
    """Image ACEScg lineaire -> meme image apres le passage analogique."""
    if p is None:
        return ap1
    h, w = ap1.shape[:2]
    L, N = p["lines"], p["samples"]
    hr, wr, y0, x0 = region(p, h, w)
    rgb = bt709_encode(np.clip(apply_matrix(ap1, AP1_TO_REC709), 0.0, 1.0))
    rgb = cv2.resize(np.ascontiguousarray(rgb), (wr, hr), interpolation=cv2.INTER_AREA)
    raster = np.zeros((L, N, 3))
    raster[y0:y0 + hr, x0:x0 + wr] = rgb
    yuv = raster @ YUV.T
    Y, U, V = yuv[..., 0], yuv[..., 1], yuv[..., 2]
    rng = np.random.default_rng([p["seed"], 31, int(frame)])
    if p["tape"] != "none":
        for g in range(p["generations"]):
            Y, U, V = tape_pass(Y, U, V, p, rng, g)
    if p["composite"]:
        Y, U, V = composite_pass(Y, U, V, p, rng, frame)
    out = np.stack([Y, U, V], -1) @ YUV_INV.T
    out = out[y0:y0 + hr, x0:x0 + wr]
    out = cv2.resize(np.ascontiguousarray(out), (w, h), interpolation=cv2.INTER_LINEAR)
    lin = bt709_decode(np.clip(out, 0.0, 1.2))
    return apply_matrix(lin, REC709_TO_AP1)
