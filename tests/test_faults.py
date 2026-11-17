"""Tests des pannes : chaque panne doit produire exactement l'effet de son mecanisme.

Usage : python tests/test_faults.py   (depuis le dossier CameraSim_AE)
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))

from camsim import CameraSim                       # noqa: E402
from camsim import faults                          # noqa: E402

P = "sonya7s2"
# Capteur sans bruit, pour isoler chaque panne. Le convertisseur reste actif.
QUIET = dict(iso=800, shot_noise=False, read_noise_model="off", row_noise_gain=0.0, color_bias_gain=0.0,
             olpf_h_px=0.0, olpf_v_px=0.0)
results = []


def check(name, ok, detail):
    results.append(bool(ok))
    print(("OK    " if ok else "ECHEC ") + name + " : " + detail)


def flat(v, n=256):
    return np.full((n, n, 3), v, dtype=np.float64)


yy, xx = np.mgrid[0:256, 0:256] / 255.0
ramp = np.stack([0.02 + 0.6 * xx, 0.02 + 0.6 * xx, 0.02 + 0.6 * xx], axis=-1)
scene = np.stack([0.05 + 0.5 * xx, 0.05 + 0.5 * yy, 0.3 - 0.2 * xx * yy], axis=-1)
ref = CameraSim(P, **QUIET).raw(scene, 1)

# 1. Bruit de colonnes fixe ------------------------------------------------------
sim = CameraSim(P, column_fpn_dn=4.0, quantize=False, **QUIET)
a, b = sim.raw(flat(0.0, 1024), 1), sim.raw(flat(0.0, 1024), 2)
sd = a.mean(axis=0).std()
check("decalage des colonnes", np.array_equal(a, b) and abs(sd / 4.0 - 1) < 0.08 and a.std(axis=0).max() < 1e-9,
      "ecart-type entre colonnes %.2f DN pour 4.00 ; fige d'une image a l'autre" % sd)
sim = CameraSim(P, column_gain_sigma=0.02, quantize=False, **QUIET)
g = sim.raw(flat(0.2, 1024), 1)[0::2, 1::2] - sim.sensor.black
rel = (g.mean(axis=0) / g.mean()).std()
check("gain des colonnes", abs(rel / 0.02 - 1) < 0.1, "ecart relatif %.4f pour 0.0200" % rel)

# 2. Pixels, lignes et colonnes morts -----------------------------------------------
sim = CameraSim(P, dead_pixel_fraction=0.01, stuck_pixel_fraction=0.002, **QUIET)
d = sim.raw(flat(0.2, 1024), 1)
fb, fw = (d == sim.sensor.black).mean(), (d == sim.sensor.white).mean()
check("pixels morts et colles", abs(fb / 0.01 - 1) < 0.1 and abs(fw / 0.002 - 1) < 0.2,
      "%.3f %% noirs (1 %% demande), %.3f %% blancs (0,2 %% demande)" % (100 * fb, 100 * fw))
sim = CameraSim(P, dead_columns=5, dead_rows=3, **QUIET)
d = sim.raw(flat(0.2, 512), 1)
bad = (d == sim.sensor.black) | (d == sim.sensor.white)
check("lignes et colonnes mortes", bad.all(axis=0).sum() == 5 and bad.all(axis=1).sum() == 3,
      "%d colonnes et %d lignes entierement mortes" % (bad.all(axis=0).sum(), bad.all(axis=1).sum()))

# 3. Adressage ---------------------------------------------------------------------------
d = CameraSim(P, row_address_stuck="4:0", **QUIET).raw(scene, 1)
src = np.arange(256) & ~(1 << 4)
check("bit d'adresse de ligne colle", np.array_equal(d, ref[src, :]),
      "les lignes 16 a 31 repetent les lignes 0 a 15, et ainsi de suite")
d = CameraSim(P, col_address_stuck="6:1", **QUIET).raw(scene, 1)
src = np.arange(256) | (1 << 6)
check("bit d'adresse de colonne colle", np.array_equal(d, ref[:, src]), "les colonnes sont repetees par blocs de 64")
d = CameraSim(P, row_address_error_rate=0.1, **QUIET).raw(scene, 1)
wrong = (d != ref).any(axis=1).mean()
check("erreurs d'adressage aleatoires", 0.05 < wrong < 0.16, "%.1f %% des lignes lues a une mauvaise adresse (10 %% demande)" % (100 * wrong))

# 4. Convertisseur -----------------------------------------------------------------------
x = np.linspace(0, 16383, 200001)[None, :]
ideal = faults.adc_convert(x, 16383, 14, 14)
check("convertisseur ideal", np.abs(ideal - x).max() <= 0.5 + 1e-9, "erreur max %.3f LSB" % np.abs(ideal - x).max())
th = faults.adc_thresholds(np.random.default_rng(0), 16384, 0.3)
real = faults.adc_convert(x, 16383, 14, 14, th)
counts = np.bincount(real.astype(int).ravel(), minlength=16384)[100:-100].astype(float)
dnl = (counts / counts.mean()).std()
check("irregularite des marches (DNL)", abs(dnl / 0.3 - 1) < 0.1 and np.all(np.diff(real.ravel()) >= 0),
      "largeur des marches : ecart %.3f LSB pour 0.300 ; reponse toujours croissante" % dnl)
th = faults.adc_thresholds(np.random.default_rng(0), 16384, 1.0)
missing = (np.bincount(faults.adc_convert(x, 16383, 14, 14, th).astype(int).ravel(), minlength=16384) == 0).mean()
check("codes manquants", 0.05 < missing < 0.4, "%.0f %% des codes ne sortent jamais avec une DNL de 1 LSB" % (100 * missing))
bow = faults.adc_convert(x, 16383, 14, 14, None, inl_lsb=20.0) - x
check("courbure (INL)", abs(bow.max() - 20) < 1.0 and abs(bow[0, 0]) <= 0.5 and abs(bow[0, -1]) <= 0.5,
      "ecart max %.1f LSB au milieu, nul aux deux bouts" % bow.max())
d = CameraSim(P, adc_bits=6, **QUIET).raw(ramp, 1)
check("profondeur reduite a 6 bits", len(np.unique(d)) <= 64 and np.all(d % 256 == 0), "%d niveaux distincts" % len(np.unique(d)))
d = CameraSim(P, adc_stuck_bits="9:1,3:0", **QUIET).raw(ramp, 1).astype(int)
check("bits colles", np.all(d & (1 << 9)) and not np.any(d & (1 << 3)), "bit 9 toujours a 1, bit 3 toujours a 0")
d = CameraSim(P, adc_sparkle_rate=0.01, **QUIET).raw(scene, 1)
diff = (d != ref)
x_or = (d.astype(int) ^ ref.astype(int))[diff]
check("erreurs de bits aleatoires", abs(diff.mean() / 0.01 - 1) < 0.15 and np.all((x_or & (x_or - 1)) == 0),
      "%.2f %% des pixels touches (1 %% demande), un seul bit inverse a chaque fois" % (100 * diff.mean()))

# 5. Liaison serie -------------------------------------------------------------------------
d = CameraSim(P, bitslip=3, **QUIET).raw(scene, 1).astype(np.int64)
r = ref.astype(np.int64)
nxt = np.zeros_like(r)
nxt[:, :-1] = r[:, 1:]
check("glissement de 3 bits", np.array_equal(d, ((r << 3) & 16383) | (nxt >> 11)),
      "chaque mot = fin du pixel courant + debut du pixel suivant")
check("glissement de 14 bits = aucun", np.array_equal(CameraSim(P, bitslip=14, **QUIET).raw(scene, 1), ref), "identique")
d = CameraSim(P, line_shift_rate=0.2, line_shift_max_px=6, **QUIET).raw(scene, 1)
moved = (d != ref).any(axis=1)
ok_rows = all(any(np.array_equal(np.roll(ref[y], k)[8:-8], d[y][8:-8]) for k in range(-6, 7)) for y in np.nonzero(moved)[0])
check("glissement de lignes isolees", 0.1 < moved.mean() < 0.3 and ok_rows,
      "%.0f %% des lignes decalees, chacune d'un nombre entier de pixels" % (100 * moved.mean()))
d = CameraSim(P, line_shift_rate=0.2, line_shift_max_px=6, line_shift_persist=True, **QUIET).raw(scene, 1)
offs = [next(k for k in range(256) if np.array_equal(np.roll(ref[y], k), d[y])) for y in range(256)]
steps = np.diff(np.array(offs))
steps = np.where(steps > 128, steps - 256, np.where(steps < -128, steps + 256, steps))
check("perte de synchro (glissements cumules)", np.abs(steps).max() <= 6 and (steps != 0).mean() > 0.08,
      "le decalage derive de ligne en ligne, par pas de 6 px au plus")

# 6. Image dechiree ----------------------------------------------------------------------------
sim = CameraSim(P, frame_tear_rate=1.0, **QUIET)
f1 = sim.raw(scene, 1)
f2 = sim.raw(scene[::-1].copy(), 2)
clean2 = CameraSim(P, **QUIET).raw(scene[::-1].copy(), 2)
top = (f2 == clean2).all(axis=1)
cut = int(np.argmin(top))
check("image dechiree", 0 < cut < 256 and top[:cut].all() and np.array_equal(f2[cut:], f1[cut:]),
      "au-dessus de la ligne %d : image courante ; en dessous : image precedente" % cut)

# 7. Parasite periodique --------------------------------------------------------------------------
sim = CameraSim(P, interference_dn=12.0, interference_period_px=40.0, quantize=False, **QUIET)
d = sim.raw(flat(0.0, 400), 1) - sim.sensor.black
spec = np.abs(np.fft.rfft(d[5])) / 200.0
check("parasite de l'alimentation", abs(spec.max() - 12) < 0.1 and np.argmax(spec) == 10,
      "amplitude %.2f DN (12 demande), periode %.0f px (40 demande)" % (spec.max(), 400 / np.argmax(spec)))

# 8. Erreurs du traitement ----------------------------------------------------------------------------
grey = flat(0.2, 64)
ok0 = CameraSim(P, **QUIET).process(grey)
bad = CameraSim(P, isp_bayer_dx=1, **QUIET).process(grey)
c0, c1 = ok0.mean(axis=(0, 1)), bad.mean(axis=(0, 1))
check("erreur de motif de Bayer", np.ptp(c0) < 1e-3 and np.ptp(c1) > 0.03,
      "un gris neutre %s devient %s" % (np.round(c0, 3), np.round(c1, 3)))
lift = CameraSim(P, isp_black_offset_dn=-200.0, **QUIET).process(flat(0.0, 64)).mean(axis=(0, 1))
check("niveau noir mal regle", lift.min() > 0.005 and np.ptp(lift) > 1e-3,
      "le noir remonte a %s : voile colore" % np.round(lift, 4))

print("\n%d / %d tests reussis" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
