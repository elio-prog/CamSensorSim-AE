"""Automatismes d'une camera : exposition, gain et balance des blancs automatiques.

Une camera mesure l'image qu'elle vient de produire et corrige la suivante : il y a donc
toujours au moins une image de retard. La correction n'est pas instantanee (moteur du
diaphragme, filtrage de la mesure) et, selon le reglage de la boucle, elle peut depasser
sa cible et revenir : c'est le "pompage" des camescopes et des telephones.

Exposition : boucle du second ordre sur l'exposition totale x (en diaphragmes) :
    erreur  e = log2(cible / mesure)        (nulle dans une zone morte autour de la cible)
    a       = w^2 e - 2 z w v               (w = 1 / temps de reaction, z = amortissement)
    v      += a dt   (vitesse bornee)        x += v dt
Amortissement 1 : pas de depassement ; en dessous de 0,7 : depassement ; vers 0,2 : pompage.
L'exposition totale est d'abord prise par le diaphragme (plus ou moins de lumiere, donc plus
ou moins d'electrons), puis, quand le diaphragme est en butee, par le gain (AGC : le signal
est amplifie, le bruit avec lui). C'est l'ordre habituel des programmes d'exposition de
camescope.

Balance des blancs : estimation de la couleur de l'eclairage sur l'image (moyenne des
couleurs, "monde gris", ou moyenne des points les plus clairs, "point blanc"), puis
correction progressive des gains rouge et bleu (premier ordre), partielle si on le veut
(beaucoup de cameras laissent volontairement un peu de chaleur sous une lumiere chaude).

Les valeurs par defaut sont des valeurs courantes, pas des mesures d'une camera precise.
"""
import math

import numpy as np

from .color import AP1_TO_XYZ_D65
from .sources import AP1_TO_REC709

Y_AP1 = AP1_TO_XYZ_D65[1]          # luminance d'une couleur ACEScg


def metering_weights(h, w, mode):
    yy = (np.arange(h) - (h - 1) / 2.0) / (h / 2.0)
    xx = (np.arange(w) - (w - 1) / 2.0) / (w / 2.0)
    r2 = yy[:, None] ** 2 + xx[None, :] ** 2
    if mode == "average":
        return np.ones((h, w))
    if mode == "center":
        return np.exp(-r2 / (2 * 0.45 ** 2))          # mesure ponderee centrale
    if mode == "spot":
        return (r2 < 0.12 ** 2).astype(float) + 1e-9  # mesure spot : 12 % du centre
    if mode == "peak":
        return np.ones((h, w))
    raise ValueError("ae_metering inconnu : %r" % mode)


class Automatics:
    def __init__(self, settings):
        s = settings
        self.s = s
        self.ae = bool(s["auto_exposure"])
        self.awb = bool(s["auto_wb"])
        self.x = 0.0          # exposition ajoutee par l'automatisme (diaphragmes)
        self.v = 0.0
        self.lr = 0.0         # gains rouge et bleu ajoutes (en log2)
        self.lb = 0.0
        self._w = None
        self.history = []

    @property
    def active(self):
        return self.ae or self.awb

    def split(self):
        """Exposition totale -> (diaphragme, gain), en diaphragmes. Le gain prend le relais en butee."""
        if not self.ae:
            return 0.0, 0.0
        s = self.s
        iris = min(max(self.x, float(s["ae_iris_min_ev"])), float(s["ae_iris_max_ev"]))
        rest = self.x - iris
        gain_max = float(s["ae_gain_max_db"]) / 6.0206 if s["auto_gain"] else 0.0
        gain = min(max(rest, 0.0), gain_max)
        return iris, gain

    def measure(self, img):
        """Mesure de la luminosite (lineaire) sur l'image produite, sous-echantillonnee."""
        step = max(1, min(img.shape[0], img.shape[1]) // 160)
        sub = img[::step, ::step]
        y = np.maximum(sub @ Y_AP1, 0.0)
        mode = self.s["ae_metering"]
        if self._w is None or self._w.shape != y.shape:
            self._w = metering_weights(y.shape[0], y.shape[1], mode)
        if mode == "peak":
            return float(np.percentile(y, 99.0)), sub
        return float((y * self._w).sum() / self._w.sum()), sub

    def update(self, img, fps):
        """img : image produite par la camera (au niveau de son signal). Prepare l'image suivante."""
        dt = 1.0 / max(float(fps), 1e-6)
        s = self.s
        meas, sub = self.measure(img)
        rec = {"x": self.x, "mesure": meas}
        if self.ae:
            target = 1.0 if s["ae_metering"] == "peak" else float(s["ae_target"])
            e = math.log2(target / max(meas, 1e-6))
            dead = float(s["ae_deadband_ev"])
            e = 0.0 if abs(e) < dead else e - math.copysign(dead, e)
            w = 1.0 / max(float(s["ae_speed_s"]), 1e-3)
            z = float(s["ae_damping"])
            # Integration exacte sur dt avec de petits pas (stable meme pour une reaction rapide).
            n = max(1, int(math.ceil(dt * w * 8)))
            h = dt / n
            vmax = float(s["ae_max_rate_ev_s"])
            for _ in range(n):
                a = w * w * e - 2.0 * z * w * self.v
                self.v = min(max(self.v + a * h, -vmax), vmax)
                self.x += self.v * h
                e -= self.v * h                     # la correction appliquee reduit l'erreur restante
            lo = float(s["ae_iris_min_ev"])
            hi = float(s["ae_iris_max_ev"]) + (float(s["ae_gain_max_db"]) / 6.0206 if s["auto_gain"] else 0.0)
            if self.x < lo or self.x > hi:
                self.x = min(max(self.x, lo), hi)
                self.v = 0.0
        if self.awb:
            rgb = sub.reshape(-1, 3) @ AP1_TO_REC709.T
            y = rgb @ np.array([0.2126, 0.7152, 0.0722])
            # On ecarte les points trop sombres (bruit) et le demi-pourcent le plus clair (souvent ecrete).
            top = rgb.max(axis=1)
            ok = (y > 0.02 * max(meas, 1e-6)) & (top <= np.percentile(top, 99.5))
            if ok.sum() > 16:
                sel = rgb[ok]
                if s["awb_method"] == "whitepatch":
                    yy = y[ok]
                    sel = sel[yy >= np.percentile(yy, 97.0)]
                elif s["awb_method"] != "grayworld":
                    raise ValueError("awb_method inconnu : %r" % s["awb_method"])
                m = np.maximum(sel.mean(axis=0), 1e-9)
                # Couleur de l'eclairage avant la correction en cours, puis correction voulue.
                raw_r, raw_b = m[0] / 2.0 ** self.lr, m[2] / 2.0 ** self.lb
                k = float(s["awb_strength"])
                tr, tb = k * math.log2(m[1] / raw_r), k * math.log2(m[1] / raw_b)
                f = 1.0 - math.exp(-dt / max(float(s["awb_speed_s"]), 1e-3))
                dead = float(s["awb_deadband"])
                if abs(tr - self.lr) > dead:
                    self.lr += (tr - self.lr) * f
                if abs(tb - self.lb) > dead:
                    self.lb += (tb - self.lb) * f
        rec.update({"x_suivant": self.x, "rouge": self.lr, "bleu": self.lb})
        self.history.append(rec)
