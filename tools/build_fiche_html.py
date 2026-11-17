"""Assemble la fiche HTML des reglages (out/fiche/CameraSim_fiche.html) a partir de out/fiche/cards.json.

Les textes du generateur (tools/make_fiche.py) sont ecrits sans accents ; ils sont accentues ici pour la lecture.
Les noms des reglages restent tels qu'ils apparaissent dans After Effects (sans accents).

Usage : python tools/build_fiche_html.py
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out" / "fiche"

ACC = dict(
    accentuation="accentuation", accentue="accentué", accentues="accentués", additionnees="additionnées",
    agrandie="agrandie", aligne="aligné", alternees="alternées", amplifie="amplifie", apres="après",
    arrachees="arrachées", arrete="arrête", arriere="arrière", assombrit="assombrit", baveuses="baveuses",
    bilineaire="bilinéaire", bloque="bloqué", bloques="bloqués", bruitees="bruitées", bruites="bruités",
    brutales="brutales", cable="câble", camera="caméra", cameras="caméras", camescope="caméscope",
    colorees="colorées", colores="colorés", compressees="compressées", connue="connue", cote="côté",
    crees="créés", crenelage="crénelage", deborde="déborde", debordement="débordement", debordent="débordent",
    decalage="décalage", decalees="décalées", dechire="déchire", decoche="décoché", decodeur="décodeur",
    defaut="défaut", defauts="défauts", definies="définies", definition="définition", degrades="dégradés",
    dela="delà", dematricage="dématriçage", desaturees="désaturées", desaturent="désaturent", details="détails",
    developpe="développé", deviennent="deviennent", difference="différence", differemment="différemment",
    differente="différente", discrete="discrète", divisees="divisées", duree="durée", eblouissement="éblouissement",
    eclaircit="éclaircit", ecrases="écrasés", ecretes="écrêtés", electronique="électronique", electrons="électrons",
    entieres="entières", entrelacee="entrelacée", epoque="époque", eviter="éviter", enormes="énormes",
    etage="étage", etirent="étirent", exposees="exposées", extremes="extrêmes", facon="façon", fige="figé",
    frequence="fréquence", gardees="gardées", general="général", granuleux="granuleux", horizontalement="horizontalement",
    idem="idem", irradie="irradié", irreguliere="irrégulière", irregulieres="irrégulières", isolees="isolées",
    legere="légère", legerement="légèrement", lumiere="lumière", lumieres="lumières", luminosite="luminosité",
    magnetoscope="magnétoscope", malvar="Malvar", masques="masqués", mesure="mesuré", mesuree="mesurée",
    mesurees="mesurées", mesures="mesures", methode="méthode", meme="même", memes="mêmes", mise="mise",
    mosaique="mosaïque", moyennees="moyennées", multipliees="multipliées", negatif="négatif", nettete="netteté",
    numerique="numérique", obscurite="obscurité", ondules="ondulés", perturbee="perturbée", placee="placée",
    pres="près", protege="protégé", qualite="qualité", ramenee="ramenée", recoit="reçoit", reel="réel",
    reelle="réelle", reglage="réglage", reglages="réglages", reglee="réglée", regler="régler", remplacee="remplacée",
    repete="répète", repetees="répétées", repondent="répondent", reponse="réponse", resolution="résolution",
    saturees="saturées", satures="saturés", separee="séparée", sensibilite="sensibilité", simule="simulé",
    solarise="solarisé", soulignes="soulignés", tassees="tassées", teintees="teintées",
    temperature="température", tetes="têtes", trainees="traînées", tres="très", video="vidéo", vieilli="vieilli",
    voila="voilà", desordre="désordre", controle="contrôle", ecretage="écrêtage", separes="séparés",
)
ACC.update({"coche": "coché", "deja": "déjà", "eclaire": "éclairé", "ecart": "écart", "orientees": "orientées"})


def accents(t):
    if not t:
        return t
    t = re.sub(r"[A-Za-z]+", lambda m: _word(m.group(0)), t)
    # « a » preposition (a 14 bits, a chaud, a la place...) ; le verbe (a ses, a une...) reste.
    t = re.sub(r"\ba (?!(ses|son|sa|une|un|des|leur|leurs)\b)", "à ", t)
    t = t.replace("A 0", "À 0").replace("au-delà", "au-delà").replace(" ou ", " ou ")
    return t


def _word(w):
    low = w.lower()
    if low in ACC:
        r = ACC[low]
        return r[0].upper() + r[1:] if w[0].isupper() else r
    return w


def main():
    data = json.loads((OUT / "cards.json").read_text(encoding="utf-8"))
    for c in data["cards"]:
        for k in ("text", "cond", "shown", "group"):
            c[k] = accents(c[k])
        c["camera"] = accents(c["camera"])
    tpl = (ROOT / "tools" / "fiche_template.html").read_text(encoding="utf-8")
    html = tpl.replace("/*DATA*/null", json.dumps(data, ensure_ascii=False))
    (OUT / "CameraSim_fiche.html").write_text(html, encoding="utf-8")
    print("ecrit :", OUT / "CameraSim_fiche.html", "%.1f Mo" % (len(html.encode()) / 1e6))


if __name__ == "__main__":
    main()
