"""Couleur : ACEScg <-> XYZ (D65) <-> RVB natif de la camera.

Les matrices sont calculees a partir des primaires, pas recopiees, pour eviter
une erreur de transcription. Limite connue : c'est un modele par matrice 3x3.
Une vraie camera repond selon ses sensibilites spectrales ; ce sera un module
ulterieur (base camspec, Jiang 2013).
"""
import numpy as np

# Primaires et blanc d'ACEScg (AP1), specification ACES S-2014-004.
AP1_PRIMS = ((0.713, 0.293), (0.165, 0.830), (0.128, 0.044))
AP1_WHITE = (0.32168, 0.33767)
D65_WHITE = (0.3127, 0.3290)
SRGB_PRIMS = ((0.64, 0.33), (0.30, 0.60), (0.15, 0.06))

BRADFORD = np.array([[0.8951, 0.2664, -0.1614],
                     [-0.7502, 1.7135, 0.0367],
                     [0.0389, -0.0685, 1.0296]])


def xy_to_XYZ(xy):
    x, y = xy
    return np.array([x / y, 1.0, (1.0 - x - y) / y])


def rgb_to_xyz_matrix(prims, white):
    """Matrice RVB -> XYZ d'un espace defini par ses primaires et son blanc."""
    P = np.stack([xy_to_XYZ(p) for p in prims], axis=1)
    S = np.linalg.solve(P, xy_to_XYZ(white))
    return P * S[None, :]


def bradford_cat(white_src, white_dst):
    """Adaptation chromatique de Bradford entre deux blancs."""
    s = BRADFORD @ xy_to_XYZ(white_src)
    d = BRADFORD @ xy_to_XYZ(white_dst)
    return np.linalg.inv(BRADFORD) @ np.diag(d / s) @ BRADFORD


AP1_TO_XYZ = rgb_to_xyz_matrix(AP1_PRIMS, AP1_WHITE)
AP1_TO_XYZ_D65 = bradford_cat(AP1_WHITE, D65_WHITE) @ AP1_TO_XYZ
XYZ_D65_TO_AP1 = np.linalg.inv(AP1_TO_XYZ_D65)
SRGB_TO_XYZ_D65 = rgb_to_xyz_matrix(SRGB_PRIMS, D65_WHITE)
AP1_TO_SRGB = np.linalg.inv(SRGB_TO_XYZ_D65) @ AP1_TO_XYZ_D65


def apply_matrix(img, M):
    """Applique une matrice 3x3 a une image (..., 3)."""
    return img @ np.asarray(M).T


class CameraColor:
    """Passage entre ACEScg et le RVB natif d'une camera.

    xyz_to_cam est la matrice Adobe/LibRaw (XYZ D65 -> camera). On la normalise
    pour qu'un blanc ACEScg (1,1,1) donne 1.0 sur le canal le plus sensible
    (le vert, en pratique), comme le fait dcraw.
    """

    def __init__(self, xyz_d65_to_cam):
        m = np.asarray(xyz_d65_to_cam, dtype=np.float64) @ AP1_TO_XYZ_D65
        white = m @ np.ones(3)
        self.ap1_to_cam = m / white.max()
        self.cam_white = white / white.max()          # reponse brute a un blanc neutre
        self.wb_gains = 1.0 / self.cam_white          # gains qui rendent ce blanc neutre
        self.cam_to_ap1_wb = np.linalg.inv(self.ap1_to_cam @ np.eye(3)) @ np.diag(self.cam_white)
        # cam_to_ap1_wb s'applique a une image deja equilibree en blanc.

    def scene_to_cam(self, ap1):
        return np.maximum(apply_matrix(ap1, self.ap1_to_cam), 0.0)

    def cam_wb_to_scene(self, cam_wb):
        return apply_matrix(cam_wb, self.cam_to_ap1_wb)


def preview_srgb(ap1, exposure=1.0):
    """Apercu 8 bits simple (matrice vers sRGB + courbe sRGB). Pour controle visuel seulement."""
    rgb = np.clip(apply_matrix(ap1 * exposure, AP1_TO_SRGB), 0.0, 1.0)
    out = np.where(rgb <= 0.0031308, 12.92 * rgb, 1.055 * np.power(rgb, 1 / 2.4) - 0.055)
    return (out * 255.0 + 0.5).astype(np.uint8)
