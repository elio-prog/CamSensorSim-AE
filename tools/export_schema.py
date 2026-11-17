"""Ecrit ae/settings_schema.json : la liste des reglages et des profils, lue par le panneau.

A relancer apres avoir ajoute un reglage ou un profil :
  python tools/export_schema.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))

from camsim.pipeline import PROFILE_DIR          # noqa: E402
from camsim.schema import build_schema           # noqa: E402

ORDER = {"cmos": 0, "ccd": 1, "cmyg": 2, "tube": 3}


def main():
    profiles = []
    for f in sorted(PROFILE_DIR.glob("*.json")):
        p = json.loads(f.read_text(encoding="utf-8"))
        kind = "cmyg" if p.get("cfa") == "CMYG" else p.get("sensor_type", "cmos")
        profiles.append({"key": f.stem, "name": p["name"], "kind": kind})
    # Le Sony A7S II en premier : c'est le profil le mieux documente, donc le choix par defaut.
    profiles.sort(key=lambda p: (ORDER.get(p["kind"], 9), p["key"] != "sonya7s2", p["name"]))
    schema = build_schema(profiles)
    dst = ROOT / "ae" / "settings_schema.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    # ASCII pur : le panneau lit ce fichier avec eval, sans souci d'encodage.
    dst.write_text(json.dumps(schema, indent=1, ensure_ascii=True), encoding="ascii")
    n = sum(len(g["items"]) for g in schema["groups"])
    print("ecrit %s : %d profils, %d reglages en %d groupes" % (dst.name, len(profiles), n, len(schema["groups"])))


if __name__ == "__main__":
    main()
