"""Obturateur deroulant (rolling shutter) et scintillement de l'eclairage.

Un capteur CMOS courant ne lit pas toutes ses lignes au meme instant : il les lit
l'une apres l'autre, du haut vers le bas, pendant le "temps de lecture". La ligne y
voit donc la scene a l'instant

    t(y) = t_image + temps_de_lecture * (y / H - 0,5)

Ce qui bouge pendant ce temps est deforme (verticales penchees, objets etires).

Pour connaitre la scene a un instant situe entre deux images du rendu, on deplace
les pixels le long du mouvement apparent (flux optique) entre images voisines.
C'est le principe utilise en recherche pour fabriquer des images a obturateur
deroulant a partir d'images a obturateur global. Le flux est soit fourni (vecteurs
de mouvement du rendu), soit estime par OpenCV (algorithme DIS).

Limite : la ou un objet en cache un autre, le deplacement est approximatif.

Scintillement : un eclairage sur secteur varie a deux fois la frequence du reseau
(100 Hz en Europe). Chaque ligne integre la lumiere pendant le temps de pose, a un
instant different : on obtient des bandes horizontales. Pour une lumiere
L(t) = 1 + m cos(2 pi f t), le gain de la ligne est exactement

    1 + m * sinc(f * T_pose) * cos(2 pi f (t(y) + T_pose / 2))
"""
import numpy as np


def row_times(h, frame, fps, readout_time_s, direction="top_to_bottom"):
    """Instant de lecture de chaque ligne, en secondes."""
    pos = (np.arange(h) + 0.5) / h - 0.5
    if direction == "bottom_to_top":
        pos = -pos
    return frame / fps + readout_time_s * pos


def row_offsets_in_frames(h, fps, readout_time_s, direction="top_to_bottom"):
    """Decalage temporel de chaque ligne par rapport au centre de l'image, en images."""
    return row_times(h, 0, fps, readout_time_s, direction) * fps


def _to_gray8(rgb):
    lum = np.maximum(rgb.mean(axis=-1), 0.0)
    scale = np.percentile(lum, 99.5) or 1.0
    g = np.sqrt(np.clip(lum / max(scale, 1e-6), 0.0, 1.0))
    return (g * 255.0 + 0.5).astype(np.uint8)


def estimate_flow(a, b):
    """Flux optique de l'image a vers l'image b, en pixels (H, W, 2). Algorithme DIS d'OpenCV."""
    import cv2
    dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
    return dis.calc(_to_gray8(a), _to_gray8(b), None).astype(np.float64)


def warp_rows(img, flow, tau):
    """Deplace chaque ligne de tau[y] fois le flux : image vue a l'instant de chaque ligne."""
    import cv2
    h, w = img.shape[:2]
    xx, yy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    t = tau.astype(np.float32)[:, None]
    map_x = xx - t * flow[..., 0].astype(np.float32)
    map_y = yy - t * flow[..., 1].astype(np.float32)
    out = cv2.remap(img.astype(np.float32), map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return out.astype(np.float64)


def rolling_shutter(cur, prev=None, nxt=None, *, fps, readout_time_s, direction="top_to_bottom",
                    flow_next=None, flow_prev=None):
    """Applique l'obturateur deroulant a l'image cur (canaux quelconques, alpha compris).

    prev / nxt : images voisines (meme forme), utilisees pour estimer le mouvement.
    flow_next / flow_prev : flux deja connus (vecteurs de mouvement du rendu), en pixels par image.
    """
    if readout_time_s <= 0 or (prev is None and nxt is None and flow_next is None and flow_prev is None):
        return cur
    h = cur.shape[0]
    tau = row_offsets_in_frames(h, fps, readout_time_s, direction)     # en images, de -r/2 a +r/2
    rgb = cur[..., :3]
    if flow_next is None and nxt is not None:
        flow_next = estimate_flow(rgb, nxt[..., :3])
    if flow_prev is None and prev is not None:
        flow_prev = estimate_flow(rgb, prev[..., :3])
    # S'il manque un voisin (debut ou fin de plan), on suppose un mouvement regulier.
    if flow_next is None:
        flow_next = -flow_prev
    if flow_prev is None:
        flow_prev = -flow_next
    later = warp_rows(cur, flow_next, np.maximum(tau, 0.0))            # lignes lues apres le centre
    earlier = warp_rows(cur, flow_prev, np.maximum(-tau, 0.0))         # lignes lues avant le centre
    return np.where((tau >= 0)[:, None, None], later, earlier)


def flicker_gain(h, frame, *, fps, readout_time_s, exposure_time_s, flicker_hz, depth, phase=0.0,
                 direction="top_to_bottom"):
    """Gain par ligne du a un eclairage qui scintille. Renvoie un tableau (H,)."""
    if flicker_hz <= 0 or depth <= 0:
        return None
    t = row_times(h, frame, fps, readout_time_s, direction)
    att = np.sinc(flicker_hz * exposure_time_s)                        # np.sinc(x) = sin(pi x) / (pi x)
    return 1.0 + depth * att * np.cos(2.0 * np.pi * flicker_hz * (t + 0.5 * exposure_time_s) + phase)


def half_frame_later(cur, nxt=None, flow_next=None):
    """La scene une demi-image plus tard : seconde trame d'une camera entrelacee (1/50 s apres la premiere)."""
    if flow_next is None:
        if nxt is None:
            return cur
        flow_next = estimate_flow(cur[..., :3], nxt[..., :3])
    return warp_rows(cur, flow_next, np.full(cur.shape[0], 0.5))
