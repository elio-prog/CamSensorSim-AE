"""Lecture et ecriture des EXR (RVB ou RVBA, demi-flottants)."""
import numpy as np
import OpenEXR


def read_exr(path):
    """Renvoie (rgb float64 [H,W,3], alpha float32 [H,W] ou None, en-tete)."""
    with OpenEXR.File(str(path)) as f:
        header = dict(f.header())
        ch = f.channels()
        if "RGBA" in ch:
            px = ch["RGBA"].pixels
            return px[..., :3].astype(np.float64), px[..., 3].astype(np.float32), header
        if "RGB" in ch:
            return ch["RGB"].pixels.astype(np.float64), None, header
        rgb = np.stack([ch[c].pixels for c in ("R", "G", "B")], axis=-1).astype(np.float64)
        alpha = ch["A"].pixels.astype(np.float32) if "A" in ch else None
        return rgb, alpha, header


def write_exr(path, rgb, alpha=None, color_interop_id="lin_ap1_scene"):
    """Ecrit un EXR demi-flottant compresse en ZIP, etiquete ACEScg par defaut."""
    rgb = np.clip(rgb, -65000.0, 65000.0).astype(np.float16)
    if alpha is not None:
        data = np.concatenate([rgb, alpha.astype(np.float16)[..., None]], axis=-1)
        channels = {"RGBA": data}
    else:
        channels = {"RGB": rgb}
    header = {"compression": OpenEXR.ZIP_COMPRESSION, "type": OpenEXR.scanlineimage}
    if color_interop_id:
        header["colorInteropID"] = color_interop_id
    with OpenEXR.File(header, channels) as out:
        out.write(str(path))
