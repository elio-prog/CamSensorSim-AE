"""Telecharge le tableau de l'illuminant normalise CIE D65 (pas de 1 nm) depuis la CIE.

Necessaire au moteur Python pour la couleur spectrale (engine/camsim/spectral.py). Le module After Effects
n'en a pas besoin : les tables qui en derivent sont deja dans native/core/profiles_data.h.
Source : CIE 2019, CIE standard illuminant D65, International Commission on Illumination (CIE), Vienna,
DOI : 10.25039/CIE.DS.hjfjmt59.

Usage : python tools/fetch_cie_d65.py
"""
import urllib.request
from pathlib import Path

URL = "https://files.cie.co.at/CIE_std_illum_D65.csv"
DST = Path(__file__).resolve().parent.parent / "references" / "third_party" / "cie" / "CIE_std_illum_D65.csv"


def main():
    if DST.exists():
        print("deja present :", DST)
        return
    DST.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(URL, headers={"User-Agent": "CamSensorSim-AE"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    rows = [l for l in data.decode("utf-8").splitlines() if l.strip()]
    if len(rows) < 400 or not rows[0].startswith("300,"):
        raise SystemExit("contenu inattendu : telecharger le fichier a la main depuis "
                         "https://cie.co.at/datatable/cie-standard-illuminant-d65 et le placer dans %s" % DST)
    DST.write_bytes(data)
    print("ecrit :", DST, "(%d lignes)" % len(rows))


if __name__ == "__main__":
    main()
