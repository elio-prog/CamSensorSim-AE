"""Couleur spectrale : les rendus en RVB sont convertis en spectres plausibles, puis vus par les
vraies sensibilites spectrales mesurees d'un capteur.

1. Reconstruction d'un spectre pour chaque couleur (Jakob et Hanika, "A Low-Dimensional Function
   Space for Efficient Spectral Upsampling", Eurographics 2019) : un spectre lisse de la forme
       S(l) = sigmoide(c0 x^2 + c1 x + c2),   sigmoide(z) = 1/2 + z / (2 racine(1 + z^2)),
   x = longueur d'onde ramenee a [0, 1] sur 380-780 nm. Les trois coefficients sont ajustes pour
   que S, eclaire par D65 et vu par l'observateur CIE 1931, redonne la couleur voulue. Une couleur
   tres saturee donne un spectre etroit ; un gris, un spectre plat.
   La forme ne depend que de la chromaticite : on la calcule une fois pour une grille de couleurs
   (table par voie la plus forte), et on garde directement la reponse de la camera a ce spectre.
   A l'usage, c'est donc une simple table interpolee (rapide, et utilisable sur carte graphique).
2. Reponse de la camera : integrale du spectre x D65 x sensibilites mesurees (R, V, B).
3. Matrice de la camera : ajustee comme le font les fabricants, par moindres carres sur 190 couleurs
   de reference mesurees eclairees par D65, avec le blanc conserve exactement.

Donnees (references/third_party) : rawtoaces-data (Academy Software Foundation, Apache-2.0) pour
l'observateur CIE 1931, les 190 couleurs et les sensibilites (mesures Weta Digital, 2022) ; CIE pour
l'illuminant D65 (ISO/CIE 11664-2:2022, tableau B.1).
"""
import json
from pathlib import Path

import numpy as np

from .color import AP1_TO_XYZ_D65, XYZ_D65_TO_AP1

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "references" / "third_party"
WL = np.arange(380, 781, 5, dtype=np.float64)          # 81 longueurs d'onde
X = (WL - 380.0) / 400.0
Z0 = 0.3                                                # luminosite de reference des reflectances ajustees
GRID = 48                                               # taille des tables (par voie la plus forte)


def _load_json_spectra(path):
    d = json.loads(Path(path).read_text(encoding="utf-8"))["spectral_data"]["data"]["main"]
    out = {int(float(k)): np.asarray(v, dtype=np.float64) for k, v in d.items()}
    return out


def _on_grid(table):
    keys = sorted(table)
    vals = np.stack([table[k] for k in keys])
    return np.stack([np.interp(WL, keys, vals[:, i], left=0.0, right=0.0) for i in range(vals.shape[1])], axis=1)


def load_cmf():
    return _on_grid(_load_json_spectra(DATA / "rawtoaces-data" / "cmf_1931.json"))


def load_d65():
    rows = np.loadtxt(DATA / "cie" / "CIE_std_illum_D65.csv", delimiter=",")
    return np.interp(WL, rows[:, 0], rows[:, 1])


def load_training():
    d = json.loads((DATA / "rawtoaces-data" / "training_spectral.json").read_text(encoding="utf-8"))["spectral_data"]["data"]["main"]
    keys = sorted(int(float(k)) for k in d)
    vals = np.stack([np.asarray(d[str(k)] if str(k) in d else d["%d.0" % k], dtype=np.float64) for k in keys])
    return np.stack([np.interp(WL, keys, vals[:, i]) for i in range(vals.shape[1])], axis=1)   # (81, 190)


def load_ssf(name):
    return _on_grid(_load_json_spectra(DATA / "rawtoaces-data" / name))


def sigmoid(z):
    return 0.5 + z / (2.0 * np.sqrt(1.0 + z * z))


def spectrum(coef):
    """coef (..., 3) -> spectre (..., 81)."""
    c = np.asarray(coef)[..., None, :]
    return sigmoid(c[..., 0] * X * X + c[..., 1] * X + c[..., 2])


class Spectral:
    """Reconstruction des spectres et reponse d'une camera. Les tables sont mises en cache sur disque."""

    _base = None

    def __init__(self, ssf_file):
        if Spectral._base is None:
            cmf, d65 = load_cmf(), load_d65()
            w = cmf * d65[:, None]
            w = w / w[:, 1].sum()                               # Y d'une reflectance de 1 = 1
            Spectral._base = {"cmf": cmf, "d65": d65, "w_xyz": w, "coef": None}
        b = Spectral._base
        self.d65 = b["d65"]
        if b["coef"] is None:
            b["coef"], b["fit_err"] = self._coef_tables()
        self.coef, self.fit_err = b["coef"], b["fit_err"]
        ssf = load_ssf(ssf_file)
        self.ssf = ssf / ssf.max()
        # Reponse de la camera aux spectres de la table, pour un blanc normalise.
        resp = self._camera_response(spectrum(self.coef))       # (3, G, G, 3)
        white = (self.d65[:, None] * self.ssf).sum(axis=0)
        self.white_raw = white / white.max()                    # reponse a un gris, voie la plus forte = 1
        self.resp_lut = resp / (white.max() * Z0)
        self.xyz_to_cam = self._fit_matrix()

    # -------- ajustement des spectres (Jakob et Hanika 2019)
    def _coef_tables(self):
        cache = DATA / "rawtoaces-data" / ("_coef_jakob2019_%d.npy" % GRID)
        w = Spectral._base["w_xyz"]
        if cache.exists():
            coef = np.load(cache, allow_pickle=False)
        else:
            coef = np.zeros((3, GRID, GRID, 3))
            g = np.linspace(0.0, 1.0, GRID)
            for k in range(3):
                others = [i for i in range(3) if i != k]
                t = np.zeros((GRID, GRID, 3))
                t[..., k] = Z0
                t[..., others[0]] = Z0 * g[:, None]
                t[..., others[1]] = Z0 * g[None, :]
                coef[k] = self._fit(t.reshape(-1, 3), w).reshape(GRID, GRID, 3)
            np.save(cache, coef, allow_pickle=False)
        # erreur d'ajustement (en ACEScg, relative a la voie la plus forte)
        err = np.abs(self._ap1_of(spectrum(coef), w) - self._targets()).max(axis=-1) / Z0
        return coef, err

    @staticmethod
    def _targets():
        g = np.linspace(0.0, 1.0, GRID)
        t = np.zeros((3, GRID, GRID, 3))
        for k in range(3):
            others = [i for i in range(3) if i != k]
            t[k, ..., k] = Z0
            t[k, ..., others[0]] = Z0 * g[:, None]
            t[k, ..., others[1]] = Z0 * g[None, :]
        return t

    @staticmethod
    def _ap1_of(spec, w):
        return (spec @ w) @ XYZ_D65_TO_AP1.T

    def _fit(self, targets, w, iters=60):
        """Levenberg-Marquardt sur les 3 coefficients, pour toutes les couleurs a la fois."""
        n = len(targets)
        c = np.zeros((n, 3))
        lam = np.full(n, 1e-2)
        res = self._ap1_of(spectrum(c), w) - targets
        cost = (res ** 2).sum(-1)
        for _ in range(iters):
            J = np.zeros((n, 3, 3))
            for j in range(3):
                d = np.zeros(3)
                d[j] = 1e-4
                J[:, :, j] = (self._ap1_of(spectrum(c + d), w) - targets - res) / 1e-4
            A = np.einsum("nij,nik->njk", J, J)
            gvec = np.einsum("nij,ni->nj", J, res)
            A = A + lam[:, None, None] * np.eye(3)[None] * (np.einsum("nii->ni", A)[:, :, None] + 1e-9)
            step = -np.linalg.solve(A, gvec[..., None])[..., 0]
            c_new = c + step
            res_new = self._ap1_of(spectrum(c_new), w) - targets
            cost_new = (res_new ** 2).sum(-1)
            ok = cost_new < cost
            c[ok], res[ok], cost[ok] = c_new[ok], res_new[ok], cost_new[ok]
            lam = np.where(ok, lam * 0.3, lam * 10.0)
        return c

    # -------- camera
    def _camera_response(self, spec):
        return (spec * self.d65) @ self.ssf

    def _fit_matrix(self):
        """Matrice XYZ (D65) -> camera, ajustee sur 190 couleurs, blanc exact (comme une matrice DNG)."""
        refl = load_training()                                   # (81, 190)
        w = Spectral._base["w_xyz"]
        xyz = refl.T @ w                                         # (190, 3)
        cam = (refl.T * self.d65) @ self.ssf / ((self.d65[:, None] * self.ssf).sum(0).max())
        white_xyz = np.ones(81) @ w
        # cam ~ xyz @ Mt, avec la contrainte M @ white_xyz = white_raw (multiplicateurs de Lagrange).
        A = xyz.T @ xyz
        rows = []
        for k in range(3):
            K = np.block([[2 * A, white_xyz[:, None]], [white_xyz[None, :], np.zeros((1, 1))]])
            rhs = np.concatenate([2 * xyz.T @ cam[:, k], [self.white_raw[k]]])
            rows.append(np.linalg.solve(K, rhs)[:3])
        return np.stack(rows)                                    # (3, 3) : cam = M @ xyz

    def scene_to_cam(self, ap1):
        """Image ACEScg lineaire -> reponse brute de la camera (meme echelle que CameraColor.scene_to_cam)."""
        x = np.maximum(np.asarray(ap1, dtype=np.float64), 0.0)
        m = x.max(axis=-1)
        k = x.argmax(axis=-1)
        safe = np.where(m > 0, m, 1.0)
        out = np.zeros(x.shape)
        g = GRID - 1
        for ch in range(3):
            sel = k == ch
            if not sel.any():
                continue
            others = [i for i in range(3) if i != ch]
            u = x[sel][:, others[0]] / safe[sel] * g
            v = x[sel][:, others[1]] / safe[sel] * g
            i0, j0 = np.clip(np.floor(u).astype(int), 0, g - 1), np.clip(np.floor(v).astype(int), 0, g - 1)
            fu, fv = u - i0, v - j0
            L = self.resp_lut[ch]
            r = (L[i0, j0] * ((1 - fu) * (1 - fv))[:, None] + L[i0 + 1, j0] * (fu * (1 - fv))[:, None]
                 + L[i0, j0 + 1] * ((1 - fu) * fv)[:, None] + L[i0 + 1, j0 + 1] * (fu * fv)[:, None])
            out[sel] = r * m[sel][:, None]
        return out
