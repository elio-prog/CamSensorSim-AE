# CameraSim_AE — cahier des charges

> Nom provisoire. Document de référence du projet : à relire avant toute décision de conception.
> Dernière mise à jour : 2026-11-16.

## 1. Objectifs (par ordre de priorité)

1. **RÉALISME PHYSIQUE — priorité absolue.** L'émulation doit reproduire ce qui se passe réellement dans une caméra, en s'appuyant sur de vraies recherches, de vrais projets et de vraies données mesurées. En cas de conflit avec un autre objectif, c'est celui-ci qui gagne.
2. **Utilisation dans After Effects** (AE 2025).
3. **Les défauts sont le sujet de l'outil.** Un maximum de réglages, pour des résultats variés et originaux. Chaque défaut doit pouvoir être poussé bien au-delà de ce qu'une vraie caméra ferait.
4. **Utilisable tous les jours** sur un PC courant. Sinon, on s'adapte à la config ci-dessous.

## 2. Config du PC (relevée le 2026-11-16)

| Élément | Valeur |
|---|---|
| Carte graphique | NVIDIA GeForce RTX 4070 SUPER (12 Go) |
| Processeur | Intel Core i5-14600KF (14 cœurs, 20 threads) |
| Mémoire | 32 Go |
| Système | Windows 10 Pro |

Conséquence : le calcul sur carte graphique est possible et doit être la voie principale. Une version processeur seule reste utile comme référence exacte et comme secours.

## 3. Règles de conception qui découlent du réalisme

Ces règles sont non négociables : ce sont elles qui font la différence entre un filtre « look caméra » et une simulation.

1. **Respecter l'ordre physique de la chaîne.** Lumière arrivant sur le capteur → capteur → électronique → traitement d'image → enregistrement. Un défaut est appliqué à l'endroit où il naît réellement. Exemple : le bruit est ajouté sur la mosaïque de Bayer avant le dématriçage, jamais sur l'image finale.
2. **Travailler en lumière linéaire et en unités physiques.** Le signal dans le capteur est compté en photons puis en électrons. Le bruit se règle en électrons, pas en pourcentage.
3. **Le triangle d'exposition pilote tout.** L'utilisateur donne la quantité de lumière qui arrive sur le capteur, le temps de pose et l'ISO. Le bruit et la saturation en découlent automatiquement, comme sur une vraie caméra.
4. **Des réglages qui sont de vraies grandeurs.** Taille de pixel en µm, capacité du puits en électrons, bruit de lecture en électrons, température en °C. Chaque réglage a une plage « réaliste » et une plage « hors limites » pour le créatif.
5. **Des profils de vraies caméras**, construits à partir de données mesurées (section 6).
6. **Cohérence dans le temps.** Trois familles à ne pas confondre :
   - ce qui est **figé** d'une image à l'autre : bruit à motif fixe, pixels chauds, poussières sur le capteur, défauts de colonnes ;
   - ce qui est **tiré au hasard à chaque image** : bruit de photons, bruit de lecture ;
   - ce qui a **de la mémoire** : rémanence, exposition automatique, balance des blancs automatique, réduction de bruit temporelle.
7. **Les pannes sont injectées dans la chaîne, pas par-dessus.** Le « circuit bending » se fait en cassant un étage réel (un bit du convertisseur, une horloge de ligne, la phase du Bayer), ce qui donne des défauts que la suite de la chaîne transforme de façon crédible.
8. **Chaque étage peut être désactivé, isolé et vérifié** contre une mesure ou une publication (section 8).

## 4. La chaîne simulée et ses effets

Les références entre crochets renvoient à la section 7.

### 4.0 Entrée : retrouver la lumière de la scène
- **Mode A, rendu 3D linéaire (EXR de Blender)** : le plus réaliste, la lumière de la scène est déjà connue.
- **Mode B, image déjà traitée (PNG, vidéo, compo AE)** : inversion du traitement (courbe, gamma, matrice couleur, balance des blancs) pour retrouver une image brute plausible [Brooks 2019]. Fait le 2026-11-17 (`sources.py`, voir section 12) ; les compositions AE doivent être rendues avant.
- Option : reconstruction spectrale de la couleur pour un vrai métamérisme [Jakob 2019].
### 4.1 Optique
**Hors périmètre** (décision du 2026-11-16). Rien de ce qui passe par l'objectif n'est émulé : diffraction, aberrations, aberration chromatique, distorsion, vignettage, flare, voile lumineux, défauts de lentille, flou de mise au point, flou de mouvement. L'utilisateur fait tout cela séparément. La simulation commence à la surface du capteur.

### 4.2 Capteur
- Filtres collés sur le capteur : filtre passe-bas (présent, absent, mal dimensionné) et filtre infrarouge (présent, retiré, fuyant).
- Poussières sur le capteur.
- Taille de pixel, taux de remplissage, microlentilles, diaphonie entre pixels voisins.
- Motif du filtre couleur : Bayer, RGBW, X-Trans, motifs inventés.
- Sensibilité spectrale de capteurs réels [Jiang 2013].
- **Bruit de photons** (loi de Poisson) [Foi 2008, Janesick 2007].
- **Bruit de lecture**, avec sa vraie distribution à queue longue [Wei 2020].
- **Bruit de lignes et de colonnes** (banding) [Wei 2020, Konnik 2014].
- **Bruit à motif fixe** : écart de niveau noir (DSNU) et écart de sensibilité (PRNU) entre pixels [EMVA 1288].
- **Courant d'obscurité** et pixels chauds, dépendants de la température et du temps de pose [Konnik 2014].
- Saturation du puits, éblouissement, traînées verticales des capteurs CCD (smear), soleil noir des vieux CMOS.
- Rémanence et traînées de comète (capteurs à tube, vieux CCD).
- **Obturateur** : global ou déroulant (déformation des mouvements, bandes sous éclairage 50 Hz, flash coupé en deux) [Liang 2008].
- Entrelacement.

### 4.3 Électronique
- Gain analogique (ISO), offset, niveau noir, non-linéarité de la réponse [Konnik 2014].
- Convertisseur analogique-numérique : nombre de bits, erreurs de quantification, bits collés ou manquants [Wei 2020].
- Parasites électriques : ronflement, interférences périodiques, diaphonie entre canaux.
- **Pannes** : erreurs d'adressage de lignes, perte d'horloge, colonnes mortes, données corrompues dans le brut, mauvaise phase du Bayer.

### 4.4 Traitement d'image dans la caméra
- Correction du niveau noir, masquage des pixels défectueux (réussi ou raté).
- Balance des blancs manuelle ou automatique (qui se trompe, qui dérive).
- Dématriçage : plus proche voisin, bilinéaire, [Malvar 2004], [Hirakawa 2005], avec leurs artefacts respectifs.
- Matrice couleur de caméras réelles, courbes de réponse réelles [Grossberg 2003].
- Réduction de bruit spatiale (aplats cireux) et temporelle (images fantômes).
- Accentuation des contours avec halos.
- Automatismes qui « pompent » : exposition, balance des blancs, mise au point.
- Ordre et contenu de la chaîne modifiables [Karaimer 2016].

### 4.5 Enregistrement et sortie
- Sous-échantillonnage de la chrominance, quantification, compression par de vrais codecs (MJPEG, DV, MPEG-2, H.264) à travers ffmpeg, avec pertes et erreurs de flux.
- Option : signal analogique et bande magnétique (voir ntsc-rs).
- Option : pellicule [Newson 2017].
- Sortie en EXR 32 bits ou ProRes pour AE. Export possible de l'image brute Bayer.

## 5. Propositions supplémentaires

1. **Calibrer une vraie caméra.** Avec quelques images noires (bouchon sur l'objectif) et quelques images d'une surface unie, on extrait le vrai motif de bruit fixe, les vrais pixels chauds et la vraie courbe de bruit d'un appareil [Janesick 2007]. On peut ainsi cloner la caméra de l'utilisateur.
2. **Bibliothèque de profils par époque** : tube des années 70, caméscope CCD des années 90, téléphone de 2005, webcam, caméra de surveillance, hybride moderne, caméra de cinéma.
3. **Bouton « mutation »** : part d'un profil réel et dérègle au hasard un nombre choisi de paramètres, avec une graine pour retrouver le résultat.
4. **Animation de tous les réglages par images clés AE** (température qui monte, capteur qui meurt au fil du plan).
5. **Aperçu rapide** : une image, en résolution réduite, calculée en moins d'une seconde avant de lancer le rendu complet.
6. **Vues de diagnostic** : afficher la sortie de n'importe quel étage (électrons, brut Bayer), utiles pour comprendre et comme rendu créatif.
7. **Presets enregistrables et partageables** (un fichier par caméra).

## 6. Données réelles à utiliser

État au 2026-11-16, après vérification en ligne (étape 1). « Trouvé » veut dire que la source existe et a été localisée ; les fichiers n'ont pas encore été ouverts ni téléchargés.

| Donnée | Source | État |
|---|---|---|
| Paramètres de bruit calibrés de 5 appareils (Canon 5D4, 70D, 700D, Nikon D850, Sony A7S II) et courbe de réponse du Sony A7S II | dépôt `Vandermode/ELD`, fichier `noise.py` et dossier `camera_params/release` (ajout de 2022) | Trouvé, contenu à ouvrir |
| Outil pour calibrer le bruit d'une caméra | dépôt `Srameo/LED` (code de calibration ajouté en décembre 2023) | Trouvé, à ouvrir |
| Sensibilités spectrales de 28 appareils | base « camspec » de [Jiang 2013], recopiée sur Zenodo et dans `butcherg/ssf-data` (fichiers .csv). L'adresse d'origine (RIT) date de 2013. | Trouvé. Licence CC BY-NC-SA : usage non commercial |
| Autres sensibilités spectrales mesurées | `rawtoaces` (dossier `data/camera`, fichiers JSON), `butcherg/ssf-data`, `ilia3101/SpectralMeasurements` | Trouvé, à ouvrir |
| 201 courbes de réponse de caméras et de films | base DoRF de [Grossberg 2003], site du laboratoire CAVE de Columbia | Référence confirmée, lien de téléchargement à tester |
| Bruit de lecture, capacité du puits, plage dynamique de nombreux appareils | photonstophotos.net, tableau « Sensor Characteristics » (Bill Claff) | Trouvé. Attention : ce sont des valeurs estimées par ajustement, pas des mesures directes, et elles sont parfois contestées |
| Fiches de capteurs industriels | fiches EMVA 1288 publiées par les fabricants | À chercher fabricant par fabricant |
| Images brutes bruitées réelles pour comparer | SIDD [Abdelhamed 2018] : environ 30 000 images, 5 smartphones | Trouvé |
| Matrices couleur de caméras | tables de LibRaw / Adobe DNG | Non vérifié à cette étape |
| Paramètres de bruit inscrits dans les fichiers DNG (balise NoiseProfile) | fichiers DNG de vraies caméras | Idée citée de mémoire, non vérifiée |

## 7. Références

Vérifiées en ligne le 2026-11-16, sauf mention contraire. La vérification porte sur l'existence, les auteurs, l'année et l'emplacement du code ; le texte complet des articles n'a pas été relu.

**Simulation de caméra complète**
- [Farrell 2012] Farrell, Catrysse, Wandell, « Digital camera simulation », Applied Optics 51(4), A80–A90, 2012. Code : `github.com/ISET/isetcam` (MATLAB).
- [Konnik 2014] Konnik, Welsh, « High-level numerical simulations of noise in CCD and CMOS photosensors: review and tutorial », arXiv:1412.4031, 2014. Le code MATLAB d'origine était sur Google Code (lien sans doute mort). **Portage Python : module `rystare` de `github.com/NelisW/pyradi`.** Couvre exactement notre liste : PRNU, bruit de photons, courant d'obscurité (fixe et aléatoire), offset fixe, bruit du suiveur de source, bruit de remise à zéro, quantification. Validé par les auteurs contre un vrai capteur CMOS.
- [Karaimer 2016] Karaimer, Brown, « A Software Platform for Manipulating the Camera Imaging Pipeline », ECCV 2016, LNCS 9905, p. 429–444. Page : `karaimer.github.io/camera-pipeline`.
- Nakamura (dir.), « Image Sensors and Signal Processing for Digital Still Cameras », 2005. Livre, non revérifié.

**Bruit du capteur**
- [Janesick 2007] Janesick, « Photon Transfer », SPIE Press, 2007. Livre, non revérifié.
- [EMVA 1288] Norme EMVA 1288, **version 4.0 en vigueur depuis juin 2021**, PDF gratuits sur emva.org (modules « Linear » et « General »). Un paquet Python `emva1288` existe sur PyPI.
- Healey, Kondepudy, « Radiometric CCD camera calibration and noise estimation », IEEE TPAMI 16(3), 267–276, 1994.
- [Foi 2008] Foi, Trimeche, Katkovnik, Egiazarian, « Practical Poissonian-Gaussian noise modeling and fitting for single-image raw-data », IEEE TIP 17(10), 1737–1754, 2008. Le modèle inclut l'écrêtage.
- [Brooks 2019] Brooks, Mildenhall, Xue, Chen, Sharlet, Barron, « Unprocessing Images for Learned Raw Denoising », CVPR 2019. **Code : `github.com/timothybrooks/unprocessing`** (et non dans l'organisation google-research, comme je l'avais dit).
- [Wei 2020] Wei, Fu, Yang, Huang, « A Physics-based Noise Formation Model for Extreme Low-light Raw Denoising », CVPR 2020 (oral). Version longue : « Physics-based Noise Modeling for Extreme Low-light Photography », TPAMI 2021, arXiv:2108.02158. **Attention :** à la sortie, les auteurs n'ont publié ni le modèle de bruit ni la calibration (licence commerciale). Une mise à jour de 2022 du dépôt `Vandermode/ELD` a ajouté des modèles de bruit de base et les paramètres calibrés des caméras. À ouvrir pour voir ce qui est réellement fourni (bruit de lignes, distribution à queue longue).
- Jin et al., « Lighting Every Darkness in Two Pairs », ICCV 2023. Code : `github.com/Srameo/LED`, avec un outil de calibration du modèle de bruit. Ajouté à cette étape.
- Zhang, Qin, Wang, Li, « Rethinking Noise Synthesis and Modeling in Raw Denoising », ICCV 2021, arXiv:2110.04756. Code annoncé à `github.com/zhangyi-3/noise-synthesis`, disponibilité non confirmée.
- [Abdelhamed 2018] Abdelhamed, Lin, Brown, « A High-Quality Denoising Dataset for Smartphone Cameras » (SIDD), CVPR 2018. Et Abdelhamed, Brubaker, Brown, « Noise Flow », ICCV 2019, code `github.com/BorealisAI/noise_flow` (licence non commerciale).

**Couleur et traitement**
- [Jiang 2013] Jiang, Liu, Gu, Süsstrunk, « What is the Space of Spectral Sensitivity Functions for Digital Color Cameras? », WACV 2013.
- [Grossberg 2003] Grossberg, Nayar, « What is the Space of Camera Response Functions? », CVPR 2003, p. 602–612. Version longue avec un modèle compact (EMoR) : IEEE TPAMI 26(10), 2004.
- [Jakob 2019] Jakob, Hanika, « A Low-Dimensional Function Space for Efficient Spectral Upsampling », Computer Graphics Forum 38(2), 2019. Code : `github.com/mitsuba-renderer/rgb2spec`. Très léger à évaluer, donc compatible avec la carte graphique.
- [Malvar 2004] Malvar, He, Cutler, « High-quality linear interpolation for demosaicing of Bayer-patterned color images », ICASSP 2004. Une implémentation de référence commentée existe dans la revue IPOL (2011).
- [Hirakawa 2005] Hirakawa, Parks, « Adaptive homogeneity-directed demosaicing algorithm », IEEE TIP 14(3), 360–369, 2005.

**Obturateur**
- [Liang 2008] Chia-Kai Liang, Li-Wen Chang, Homer H. Chen, « Analysis and Compensation of Rolling Shutter Effect », IEEE TIP 17(8), 1323–1330, 2008. L'article sert à corriger l'effet ; on utilisera son analyse dans l'autre sens.

**Capteurs CCD (traînées verticales)**
- Note technique de PCO sur le smear (2000), avec des formules simples selon le type de CCD (transfert de trame ou interligne). Formules à relire dans le PDF d'origine.
- Article d'Applied Optics de 2015, arXiv:1506.03706 : modèle du smear quand la lumière change pendant le transfert. Titre et auteurs à relever.
- Aucune équation de l'éblouissement (blooming) trouvée à cette étape.

**Caméras à tube**
- Yamamoto, Kiuchi, « Calculations of Image Lag in Vidicon-Type Camera Tubes. I. Beam Discharge Lag and Total Image Lag, Including Photoconductive Lag », Japanese Journal of Applied Physics 10, 1203, 1971. Donne la loi d'atterrissage du faisceau utilisée par le modèle. Résumé lu, article complet non relu.
- Manuel Mullard « Electronic tubes, Book 2 Part 2A : Plumbicon camera tubes and accessories », 1987. Texte extrait et lu (notes générales et fiche XQ1410).
- Manuel Philips T12 « Vidicon and Newvicon camera tubes », 1987. Texte extrait et lu (notes générales et fiche XQ1031).
- Le manuel Philips T10 des Plumbicon (1985) n'a pas pu être téléchargé (accès refusé).
- Projet voisin repéré, non examiné : `github.com/stoatworks-labs/plumbicon` (tube de caméra en effet pour Resolume).

**Capteurs CCD (fiche et simulateurs)**
- Fiche onsemi KAI-0373/D, rév. 4, mars 2015. Texte extrait et lu.
- Pyxel, simulateur de détecteurs open source de l'ESA (Python), avec les modèles de transfert imparfait CDM et ArCTIC. Repéré, non utilisé : notre modèle de transfert est plus simple (premier ordre).

**Pellicule (module éventuel)**
- [Newson 2017] Newson, Delon, Galerne, « A Stochastic Film Grain Model for Resolution-Independent Rendering », Computer Graphics Forum 36(8), 684–699, 2017. Code C++ avec et sans carte graphique, via l'article compagnon dans IPOL (2017).

**Projets open source**
- ISETCam, Unprocessing, pyradi (rystare), ELD, LED, openISP / fast-openISP, LibRaw, rawtoaces, rgb2spec, ntsc-rs, FFglitch, agx-emulsion.

**Licences à surveiller** : Noise Flow et la base camspec sont en usage non commercial. Unprocessing n'a pas de licence vérifiée à cette étape. À contrôler avant de réutiliser du code ou des données tels quels.

## 8. Comment prouver que c'est réaliste

- **Courbe de transfert de photons** : filmer virtuellement une surface unie à plusieurs expositions et vérifier que la relation bruit/signal suit la théorie [Janesick 2007, EMVA 1288].
- **Comparaison aux bases réelles** : statistiques du bruit simulé contre SIDD.
- **Comparaison côte à côte** avec des images d'une vraie caméra calibrée (proposition 5.1).
- **Mire de test** : moiré et artefacts de dématriçage mesurés sur une mire virtuelle, comparés aux résultats publiés.
- Chaque étage a son test, et aucun étage n'est déclaré « fait » sans ce test.

## 9. Architecture envisagée

À confirmer avec les réponses de la section 10.

- **Étape 1 : moteur Python sur carte graphique + panneau AE**, sur le modèle de SlitScan_AE (panneau ScriptUI, lancement par .vbs + `File.execute()`, jamais `system.callSystem`). Rapide à faire évoluer, idéal pour valider la physique étage par étage.
- **Étape 2 (éventuelle) : vrai effet natif AE** en C++ avec calcul sur carte graphique, une fois le modèle validé, pour avoir l'aperçu en direct dans la pile d'effets.
- Une version de référence lente mais exacte (NumPy) sert d'étalon à la version rapide.

## 10. Décisions de l'utilisateur (2026-11-16)

- **Intégration dans AE :** panneau + moteur Python sur carte graphique d'abord. L'effet natif est une étape ultérieure éventuelle.
- **Sources :** rendus 3D Blender en EXR, séquences PNG ou vidéos rendues, compositions AE de motion design. Pas de vidéo réellement filmée. Les deux modes d'entrée (4.0 A et B) sont donc nécessaires.
- **Familles de caméras :** les quatre. CMOS modernes, caméscopes CCD 1990-2000, caméras à tube 1960-1980, bas de gamme. Le moteur doit donc gérer dès le départ plusieurs types de capteurs (CMOS, CCD, tube) et l'entrelacement.
- **Périmètre :** capteur + électronique + traitement d'image d'abord. L'enregistrement (codecs, bande) et le signal analogique (4.5) viendront ensuite par modules.
- **Hors périmètre :** tout ce qui passe par l'objectif (voir 4.1), flou de mouvement compris. L'utilisateur le fait séparément. Ne pas l'émuler ni le proposer.
- **Séquence de test :** une séquence EXR 16 bits en 1920 × 1920. C'est le mode d'entrée A (lumière linéaire), le plus réaliste. Elle sert de base aux expériences.
- **Résolution (2026-11-17) :** la simulation à la vraie grille du capteur doit rester **un choix**, y compris dans l'interface finale : l'utilisateur doit pouvoir garder la résolution de l'image ou prendre celle du capteur.
- **Interface (2026-11-17) :** le panneau actuel (aperçu en image fixe, rendu à part) est provisoire. Il est peu agréable pour expérimenter. La cible est un **effet natif AE sur carte graphique** : résultat en direct, images clés sur tous les réglages, application à n'importe quel calque. L'utilisateur préfère d'abord **continuer le plan de base** (capteur, électronique, traitement) et faire l'interface plus tard. Le moteur Python reste la référence que l'effet natif devra reproduire.

## 12. Avancement

- **Étape 1 (vérification des sources) : faite le 2026-11-16.** Résultats dans les sections 6 et 7. Restent à faire : ouvrir les dépôts repérés, et trouver un modèle chiffré pour les caméras à tube.
- **Étape 2 (noyau capteur CMOS de référence) : première version faite le 2026-11-16.** Détail ci-dessous.

### Ce qui existe (version de référence, NumPy, processeur)

| Fichier | Rôle |
|---|---|
| `engine/camsim/color.py` | ACEScg ↔ XYZ ↔ RVB natif de la caméra (matrices calculées depuis les primaires) |
| `engine/camsim/sensor.py` | Capteur : électrons, bruits, numérisation |
| `engine/camsim/isp.py` | Traitement dans la caméra : noir, balance des blancs, dématriçage, matrice couleur |
| `engine/camsim/pipeline.py` | Chaîne complète et liste des réglages (`DEFAULTS`) |
| `engine/camsim/run.py` | Ligne de commande pour traiter une séquence EXR |
| `engine/camsim/profiles/*.json` | 5 caméras réelles : Sony A7S II, Nikon D850, Canon 5D IV, 70D, 700D |
| `tools/import_eld_profiles.py` | Fabrique les profils depuis les données ELD, sans exécuter de code contenu dans les fichiers |
| `tools/make_preview.py` | Planche de comparaison PNG |
| `tests/test_core.py` | 21 tests contre la théorie |
| `references/third_party/` | Sources téléchargées pour lecture (Unprocessing, ELD, pyradi, LibRaw). Jamais exécutées. |

**Modèle implémenté.** Le bruit suit le modèle d'ELD [Wei 2020], avec les paramètres calibrés publiés par les auteurs : `DN = K × Poisson(électrons) + lecture (loi de Tukey-lambda) + bruit de ligne + biais par canal + noir`, puis quantification. K est le gain du système en DN par électron. S'y ajoutent, d'après [Konnik 2014] et pyradi : non-uniformité de sensibilité (PRNU), courant d'obscurité avec sa loi de température, non-uniformité du courant d'obscurité (DSNU), pixels chauds, saturation du puits. L'exposition suit la définition ISO de la sensibilité par saturation : un gris à 18 % arrive à 12,8 % de la saturation du brut.

**Tests réussis (21 sur 21).** Aller-retour exact sans défaut ; variance de la loi de Tukey-lambda ; courbe de transfert de photons (le gain K mesuré retombe sur le gain demandé à moins de 2 %, à ISO 100, 800 et 3200) ; bruit de lecture mesuré dans le noir ; bruit de ligne ; défauts figés identiques d'une image à l'autre ; saturation ; courant d'obscurité (loi de Poisson, doublement tous les 8,3 °C près de 20 °C).

**Essai réel.** Images 1001 à 1003 de la sequence de test, Sony A7S II à ISO 6400 : environ 2 s par image en 1920 × 1920 sur le processeur. Planche : `out/apercu_1001.png`.

**Utilisation** (depuis le dossier `CameraSim_AE`, PowerShell) :

```
$env:PYTHONPATH="engine"; .\.venv\Scripts\python.exe -m camsim.run --in "DOSSIER_EXR" --out "out\essai" --profile sonya7s2 --set iso=6400
```

### Limites et hypothèses de cette version (à lever)

1. **Couleur par matrice 3 × 3**, pas par sensibilités spectrales : pas encore de métamérisme.
2. **Lien entre K et l'ISO supposé** : K proportionnel à l'ISO, avec K minimal = ISO 100. ELD ne publie pas la correspondance.
3. **Listes par réglage ISO** (forme de la loi de Tukey, biais par canal) interpolées régulièrement entre K minimal et maximal : correspondance exacte non publiée.
4. **Niveaux noir et blanc** : ceux du Sony A7S II sont cités de mémoire ; ceux des quatre autres caméras sont les valeurs génériques du code ELD.
5. **PRNU, courant d'obscurité, pixels chauds** : le modèle est là et testé, mais les valeurs ne sont mesurées pour aucune caméra. Elles sont à zéro par défaut.
6. **Un pixel du rendu = un photosite.** Le filtre passe-bas (ajouté ensuite, voir ci-dessous) règle les franges colorées, mais son écart réel sur le Sony A7S II n'est pas connu : la valeur par défaut de 1 pixel est la valeur théorique.
7. **Pas encore faits :** essai du panneau dans After Effects, caméscope à un seul CCD (mosaïque complémentaire), automatismes, mode d'entrée B (images déjà traitées), sensibilités spectrales, calcul sur carte graphique.

### Ajouts du 2026-11-16 (deuxième passe) : trois défauts

| Défaut | Fichier | Réglages | Base physique |
|---|---|---|---|
| Filtre passe-bas du capteur | `engine/camsim/filters.py` | `olpf_h_px`, `olpf_v_px` (0 = sans filtre, 1 = annule le moiré) | Lames biréfringentes qui dédoublent chaque point en quatre ; réponse `cos(π·d·f)` appliquée dans le domaine de Fourier |
| Obturateur déroulant | `engine/camsim/shutter.py` | `readout_time_s` (0 = obturateur global), `fps`, `shutter_direction` | Chaque ligne est lue à un instant différent ; la scène entre deux images est reconstruite en suivant le mouvement apparent, estimé par OpenCV (algorithme DIS) ou fourni |
| Scintillement de l'éclairage | `engine/camsim/shutter.py` | `flicker_hz`, `flicker_depth`, `flicker_phase`, avec `exposure_time_s` | Gain exact par ligne : `1 + m·sinc(f·T)·cos(2πf(t + T/2))` |

**Tests (13 sur 13, `tests/test_defects.py`).** Le contraste restant après le filtre suit la théorie au millionième près, et s'annule à la fréquence de Nyquist. Une barre qui avance de 40 px par image sort penchée de 19,95 px pour 19,95 attendus. Le mouvement estimé par OpenCV retombe sur le mouvement réel (6,00 px pour 6,00). Les bandes de scintillement ont l'amplitude prévue, et disparaissent à 1/50 s et 1/100 s de pose, comme sur une vraie caméra sous éclairage 50 Hz.

**Essai réel.** Planche `out/apercu_defauts_1010.png`. Le plan de test bouge très peu (0,09 px par image en médiane) : l'obturateur déroulant n'y produit aucune déformation visible, même en multipliant le mouvement par 8. Il faudra un plan avec du mouvement pour le juger à l'œil.

**Limites de ces ajouts.**
- Là où un objet en cache un autre, la reconstruction du mouvement est approximative. Pour un résultat exact, activer la passe « Vector » dans Blender : le moteur accepte déjà un mouvement fourni (`flow_next`, `flow_prev`), mais la lecture de cette passe dans l'EXR n'est pas encore codée.
- Le temps de lecture réel des caméras n'est dans aucun profil : à régler à la main (quelques millisecondes à quelques dizaines de millisecondes selon les capteurs, ordre de grandeur cité de mémoire).
- Le scintillement s'applique à toute l'image, comme si tout l'éclairage scintillait.
- OpenCV a été ajouté à l'environnement (`opencv-python-headless`).

### Ajouts du 2026-11-17 : pannes et bruit de colonnes

Fichier `engine/camsim/faults.py`, branché dans `sensor.py` et `isp.py`. Chaque panne est injectée à l'étage où elle naît, et la suite de la chaîne la transforme (règle 7 de la section 3).

| Étage réel | Panne | Réglages |
|---|---|---|
| Amplis de colonnes | Décalage et gain propres à chaque colonne, figés | `column_fpn_dn`, `column_gain_sigma` |
| Matrice de pixels | Pixels morts (noirs) ou collés (blancs), lignes et colonnes mortes | `dead_pixel_fraction`, `stuck_pixel_fraction`, `dead_columns`, `dead_rows` |
| Adressage | Un fil d'adresse collé : des blocs de lignes ou de colonnes se répètent | `row_address_stuck`, `col_address_stuck` (ex. `"6:0"`) |
| Adressage | Parasite sur un fil d'adresse : lignes ou colonnes lues au mauvais endroit | `row_address_error_rate`, `col_address_error_rate` |
| Alimentation | Parasite périodique ajouté au signal : bandes diagonales qui défilent | `interference_dn`, `interference_period_px` |
| Convertisseur | Marches irrégulières (DNL), codes manquants, courbure (INL) | `adc_dnl_lsb`, `adc_inl_lsb` |
| Convertisseur | Profondeur réduite, bits collés, bits inversés au hasard | `adc_bits`, `adc_stuck_bits` (ex. `"10:1,8:0"`), `adc_sparkle_rate` |
| Liaison série | Glissement de bits : chaque mot commence trop tard | `bitslip` |
| Liaison série | Glissement de l'horloge pixel, isolé ou cumulé (synchro perdue) | `line_shift_rate`, `line_shift_max_px`, `line_shift_persist` |
| Mémoire tampon | Image déchirée : le bas vient de l'image précédente | `frame_tear_rate` |
| Traitement | Mauvais motif de Bayer, niveau noir mal réglé | `isp_bayer_dx`, `isp_bayer_dy`, `isp_black_offset_dn` |

**Tests (22 sur 22, `tests/test_faults.py`).** Chaque panne est isolée sur un capteur sans bruit et comparée à l'effet exact de son mécanisme : par exemple, avec le bit 4 de l'adresse de ligne collé à 0, les lignes 16 à 31 sont bien la copie des lignes 0 à 15 ; un glissement de 3 bits donne bien « fin du pixel courant + début du pixel suivant ».

**Essai réel.** Planche `out/apercu_pannes_1010.png`, neuf pannes sur l'image 1010.

**Limites.**
- Ce sont des mécanismes réels, mais **aucun réglage n'est calibré sur une vraie caméra en panne**. Les valeurs sont libres.
- Les bits inversés au hasard touchent n'importe quel bit avec la même probabilité : c'est une simplification.
- L'image déchirée demande de traiter les images dans l'ordre (le moteur garde l'image précédente en mémoire).
- Sur la planche, la déchirure ne se voit pas : le plan bouge trop peu pour que l'image précédente diffère.

**Total des tests : 56 sur 56** (21 noyau, 13 défauts, 22 pannes).

### Ajouts du 2026-11-17 (suite) : capteurs CCD et caméras à tube

Trois nouveaux profils, tous construits sur des fiches techniques lues dans le texte :

| Profil | Source | Ce qui vient de la fiche |
|---|---|---|
| `kai0373_3ccd` | Fiche onsemi KAI-0373/D rév. 4 (2015), capteur interligne 768 × 484, 2/3 de pouce | Puits 55 000 e⁻, bruit 55 e⁻, CTE 0,99999, protection contre l'éblouissement 300×, traînée 0,01 % (tache sur 10 % de la hauteur, pose 33 ms), PRNU 2 % max |
| `plumbicon_xq1410` | Manuel Mullard « Plumbicon camera tubes and accessories » (1987), fiche XQ1410 | Gamma 0,95 ; blanc 150 nA, faisceau 300 nA ; contraste 55 % à 400 lignes TV ; rémanence 7 % à 60 ms et 2 % à 200 ms en basse lumière ; lumière de polarisation 3 nA |
| `vidicon_xq1031` | Manuel Philips T12 « Vidicon and Newvicon camera tubes » (1987), fiche XQ1031 | Gamma 0,74 ; signal 200 nA pour 20 nA d'obscurité ; rémanence 21 % à 60 ms ; résolution limite 600 lignes TV ; l'obscurité double tous les 7 à 8 °C |

**CCD (`engine/camsim/ccd.py`).** Les défauts viennent du transport des charges.

| Défaut | Réglage | Mécanisme |
|---|---|---|
| Éblouissement | `ccd_antiblooming` (1 = aucune protection) | Un photosite saturé déborde dans sa colonne, moitié vers le haut, moitié vers le bas |
| Traînée verticale | `ccd_smear_gain`, `exposure_time_s` | La lumière continue d'arriver pendant le transport : toute la colonne reçoit une part de sa propre moyenne. Plus la pose est courte, plus la traînée pèse |
| Transfert imparfait | `ccd_cti_v`, `ccd_cti_h` | Chaque transfert laisse une part de charge derrière lui : traînées vers le bas et vers la droite |
| Colonnes bloquées | `ccd_blocked_columns` | Un défaut arrête les charges : tout ce qui est derrière est perdu |
| Lecture entrelacée | `interlace="fields"` | Deux lignes voisines sont additionnées, avec un décalage d'une ligne entre les deux trames, prises à 1/50 s d'écart |
| Défaut d'alignement | `registration_r_dx`, `_r_dy`, `_b_dx`, `_b_dy` | Les trois capteurs ne sont pas parfaitement superposés : franges colorées |

**Tubes (`engine/camsim/tube.py`).** La cible est une mosaïque de condensateurs que la lumière décharge et qu'un faisceau d'électrons recharge à chaque trame. Le modèle part de la loi d'atterrissage du faisceau de Yamamoto et Kiuchi (1971), `i = I_b / (1 + exp(−a·v))`, dont j'ai tiré une forme fermée pour un passage du faisceau. Ce seul mécanisme donne la rémanence (plus forte en basse lumière), l'effet de la lumière de polarisation et les traînées de comète quand le faisceau ne suffit plus. S'y ajoutent le gamma de la couche, la rémanence photoconductrice, le courant d'obscurité, l'étalement des hautes lumières, la tache du faisceau, le bruit du préamplificateur (spectre qui monte avec la fréquence), l'ombrage et le marquage. Réglages : tous ceux qui commencent par `tube_`.

**Accord avec les mesures publiées.** `tools/calibrate_tube_lag.py` refait le calcul.
- Plumbicon, physique seule (capacité calculée depuis la géométrie, sans rien régler) : 16 à 28 % à 60 ms pour 7 % mesurés. Le bon ordre de grandeur, mais trop fort.
- Plumbicon, après réglage de trois paramètres sur les trois mesures : 7,6 % à 60 ms (mesure 7), 1,6 % à 200 ms (mesure 2), montée 98 % (mesure 98).
- Vidicon, après réglage de la rémanence photoconductrice : 21,3 % à 60 ms (mesure 21), 7,7 % à 200 ms (mesure 8).
- Sans réglage supplémentaire, le modèle retrouve deux faits décrits par Mullard : la lumière de polarisation réduit la rémanence (10,9 % → 6,4 %), et la rémanence est plus faible en pleine lumière (2,4 % au blanc).

**Tests (32 sur 32, `tests/test_ccd_tube.py`).** La traînée simulée dans les conditions de la fiche redonne la valeur de la fiche (0,0100 % pour 0,01 %). Le bruit mesuré du CCD est de 55,3 e⁻ pour 55. L'éblouissement conserve la charge. Le transfert imparfait suit la loi théorique.

**Essai réel.** Planche `out/apercu_ccd_tubes_1010.png`. Le plan bougeant trop peu, le script de démonstration ajoute un panoramique artificiel de 40 px par image et une petite source très lumineuse : ce sont des ajouts de démonstration, pas des effets de la caméra.

**Limites.**
- **La caméra 3 CCD est une construction** : le KAI-0373 est un capteur monochrome réel, mais aucune caméra couleur n'a été faite avec trois d'entre eux. Convertisseur 12 bits et niveau noir : choix libres.
- **Courant d'obscurité du CCD** : la fiche ne donne qu'un maximum. La valeur retenue est un dixième de ce maximum : c'est une estimation.
- **Caméscopes à un seul CCD avec mosaïque de couleurs complémentaires** (jaune, cyan, magenta, vert) : pas codés. C'est pourtant le cas le plus courant des caméscopes grand public.
- **Tubes** : la forme fermée du passage du faisceau est ma dérivation ; je n'ai pas pu relire les équations de l'article d'origine. Les paramètres de rémanence sont réglés sur les mesures, pas prédits.
- **Bruit du préamplificateur** (0,5 nA Plumbicon, 2 nA vidicon), **étalement des hautes lumières**, **ombrage** et **marquage** : valeurs libres, non mesurées. Mullard attribue l'éblouissement à la déviation du faisceau ; il est représenté ici par un étalement de la charge.
- **Couleurs** des trois profils : prisme idéal aux primaires Rec.709.
- **Vitesse** en 1920 × 1920 sur le processeur : environ 3 s par image pour le CCD, 5 s en entrelacé, 9 s pour le Plumbicon, 4 s pour le vidicon.
- Les tubes et le CCD entrelacé gardent une mémoire : il faut traiter les images dans l'ordre.

**Total des tests : 88 sur 88** (21 noyau, 13 défauts, 22 pannes, 32 CCD et tubes).

### Ajouts du 2026-11-17 (fin) : panneau After Effects

| Fichier | Rôle |
|---|---|
| `ae/CameraSim.jsx` | Le panneau |
| `ae/CameraSim_loader.jsx` | Petit fichier à copier dans le dossier « ScriptUI Panels » d'AE ; il charge le panneau depuis le dossier du projet |
| `ae/settings_schema.json` | Liste des réglages et des caméras, lue par le panneau. Fabriquée par `tools/export_schema.py` |
| `engine/camsim/schema.py` | Libellés, groupes et plages de mutation de chaque réglage |
| `engine/camsim/job.py` | Lanceur : exécute un travail (aperçu ou rendu) décrit dans un fichier, et écrit son avancement |

**Installation.** Copier `ae/CameraSim_loader.jsx` dans `C:\Program Files\Adobe\Adobe After Effects 2025\Support Files\Scripts\ScriptUI Panels\`, relancer AE, puis ouvrir le menu Fenêtre > CameraSim_loader.jsx. Sans installation : Fichier > Scripts > Exécuter le fichier de script, et choisir `ae/CameraSim.jsx`. Dans les préférences d'AE (Scripts et expressions), « Autoriser les scripts à écrire des fichiers et à accéder au réseau » doit être coché.

**Utilisation.**
1. Sélectionner dans la composition un calque qui est une séquence EXR en ACEScg.
2. Choisir la caméra, régler ce qu'on veut dans les neuf onglets. Un champ vide veut dire « valeur du profil ». Les réglages qui ne concernent pas la famille de la caméra choisie sont grisés.
3. **Aperçu** : calcule l'image sous la tête de lecture et ouvre une planche avant / après (environ 4 s pour un CMOS).
4. **Rendre la zone de travail** : écrit une séquence EXR ACEScg dans le dossier de sortie, l'importe et la pose au-dessus du calque source, calée dans le temps.
5. **Mutation** dérègle trois réglages au hasard. **Enregistrer** et **Charger** gardent une caméra et ses réglages dans un fichier (dossier `presets`).

Le calcul tourne à côté d'AE sans le bloquer (lancement par .vbs, jamais `system.callSystem`). Le dossier source n'est jamais modifié. Pour les tubes, quelques images sont traitées avant la première pour que la caméra soit dans son état normal.

**Ce qui a été vérifié, hors d'After Effects.**
- `tests/check_panel.js` (moteur JScript de Windows, même génération de langage que celui d'AE) : le panneau se compile, s'exécute en entier avec de faux objets AE, crée un contrôle par réglage (81 sur 81), et un clic sur Aperçu écrit le bon travail (bonne image, bonne caméra) et lance le moteur.
- `tests/check_launch.ps1` : la chaîne .bat → .vbs → moteur → fichier d'état → planche fonctionne sur l'image 1010, sans bloquer l'appelant (3,6 s).
- `tests/test_job.py` (8 sur 8) : aperçu, rendu avec trois familles de caméras, refus d'écrire dans le dossier source, réglage inconnu signalé, description des réglages à jour.

**Ce qui n'a PAS été vérifié.** Le panneau n'a jamais tourné dans After Effects. L'apparence, la taille des onglets, la fenêtre d'aperçu et l'import de la séquence dans la composition restent à essayer.

**Limites.**
- **Séquences EXR en ACEScg seulement.** Les PNG, les vidéos et les compositions AE (mode d'entrée B) ne sont pas encore lus.
- **L'étirement temporel du calque est pris en compte, pas le remappage temporel.**
- **AE doit interpréter la séquence importée en ACEScg** : le fichier est étiqueté, mais le réglage dépend de la gestion des couleurs du projet.
- **L'aperçu utilise une conversion simple vers sRGB**, pas la vue du projet.
- **Toujours sur le processeur** : 2 à 9 s par image en 1920 × 1920 selon la caméra.

**Total des tests : 96 sur 96 en Python**, plus 7 contrôles du panneau et 1 essai de la chaîne de lancement.

### Ajouts du 2026-11-17 (soir) : caméscope à un seul CCD

Note sur le panneau : les boutons étaient sous les onglets et sortaient de la partie visible dans AE. Ils sont maintenant en haut, sous la caméra.

| Fichier | Rôle |
|---|---|
| `engine/camsim/sensor.py` | Mosaïque complémentaire `CMYG_PATTERN` (Cy Ye / G Mg / Cy Ye / Mg G, fiche Sony), réponse au rouge, lecture par trames toujours active pour ce capteur |
| `engine/camsim/camcorder.py` | Séparation des couleurs du caméscope : luminance par somme de voisins, différence de couleur par démodulation, filtre de bande passante, retard 1H (ou moyenne 2H), matrice |
| `engine/camsim/profiles/icx409ak_camcorder.json` | Profil « Caméscope à un CCD (Sony ICX409AK) » |
| `tests/test_camcorder.py` | 15 tests |
| `tools/make_preview_camcorder.py` | Planche `out/apercu_camescope_1010.png` |

**Chaîne simulée.** Chaque photosite reçoit la somme de deux primaires (Ye = R+G, Cy = G+B, Mg = R+B) ou le vert seul. La trame A additionne les lignes (0+1), (2+3)… dans le registre vertical, la trame B les lignes (1+2), (3+4)…, prises 1/50 s plus tard. Chaque ligne de sortie alterne deux sommes : (Cy+Mg)(Ye+G) sur les lignes « bleues », (Cy+G)(Ye+Mg) sur les lignes « rouges ». Le caméscope en tire Y = 2R+3G+2B (somme de deux voisins), puis Cr = 2R−G ou Cb = 2B−G (différence de deux voisins) selon la ligne, filtre ces différences, reprend l'autre sur la ligne précédente de la même trame (retard 1H), et revient à R, V, B par la matrice. Nouveaux réglages, onglet « Caméscope » : bande passante des couleurs (MHz), couleur verticale 1H ou 2H, niveau de couleur. Les réglages CCD (traînée, éblouissement, transfert, colonnes bloquées) s'appliquent aussi.

**Sources.** Brevet Samsung US 5 136 370 (1992) : relations des filtres, sommes de lignes, formules Cr = 2R−G et Cb = 2B−G, retard 1H, matrice. Brevet Canon US 6 757 016 (2004), art antérieur : sommes de lignes (Cy+Mg),(Ye+G) puis (Cy+G),(Ye+Mg), qui fixent la disposition de la mosaïque. Brevet Sharp US 6 628 718 (2003) : même luminance 2R+3G+2B. Notes de l'université d'Oxford sur les capteurs Sony (novembre 2012, copie dans `references/datasheets/icx_cmyg/`) : ICX409AK, 752 × 582 photosites de 6,5 × 6,25 µm, lecture à 14,1875 MHz, filtre YCMG.

**Ce qui en découle sans être ajouté à la main** (vérifié par les tests) : les filtres complémentaires recueillent 1,75 fois plus de lumière qu'une mosaïque de Bayer, ce qui explique pourquoi les caméscopes les utilisaient ; la couleur est décalée d'une ligne à une limite horizontale ; des fausses couleurs apparaissent sur les contours verticaux, même dans une image grise ; les couleurs bavent quand la bande passante baisse ; la matrice amplifie le bruit de couleur.

**Mise à jour avec la fiche Sony ICX409AK** (fournie par l'utilisateur, `references/datasheets/icx_cmyg/ICX409AK.PDF`, réf. E00611B28) :
- **Disposition de la mosaïque** corrigée d'après le « Color Coding Diagram » : Cy Ye / G Mg / Cy Ye / Mg G ; ligne A1 = (G+Cy),(Mg+Ye), ligne A2 = (Mg+Cy),(G+Ye). Les formules de la fiche (Y = ½(2B+3G+2R), R−Y = 2R−G, −(B−Y) = −(2B−G)) sont exactement celles du code.
- **Bande passante des couleurs : 1 MHz** (filtre de chrominance du circuit de mesure Sony, −3 dB à 1 MHz), au lieu de 1,5 MHz libre.
- **Traînée : −110 dB** typique (méthode 1/10 V), soit 3,16·10⁻⁶ au lieu de 10⁻⁴ ; le test redonne −110,0 dB.
- **Courant d'obscurité** : 2 mV au plus à 60 °C pour une saturation d'au moins 1000 mV, sur une trame, soit 133,7 e⁻/s à 20 °C pour le puits retenu. C'est un maximum garanti.
- **ISO de base 1250 (gain 0 dB)**, calculé : la fiche donne 950 mV à 706 cd/m², F5,6, 1/250 s, et une saturation d'au moins 1000 mV ; avec H = 0,65·L·t/N², la saturation arrive à 0,0616 lux·s, soit ISO 1266 au sens de la norme ISO 12232. Un caméscope plafonne vers +18 dB, soit ISO 10 000.
- **Réponse au rouge 0,40.** Avec des filtres idéaux sous lumière halogène à 3200 K, Mg/G vaudrait 2,32 et Ye/Cy 2,14, alors que Sony mesure 0,93 à 1,35 et 1,15 à 1,48. Un seul facteur sur le rouge (silicium + filtre anti-infrarouge de la mesure) explique les deux : 0,40 donne Mg/G = 1,15 et Ye/Cy = 1,29. Chaque rapport, pris seul, donne entre 0,29 et 0,53 : les deux mesures sont cohérentes. La balance des blancs du caméscope compense, ce qui amplifie le bruit du rouge (visible sur la planche).
- **Niveau de couleur** appliqué après la balance des blancs, sur Y = (2R+3G+2B)/7, comme le gain de chrominance d'un caméscope.

**Limites.**
- **Non donnés par la fiche** (elle ne donne pas le gain de conversion en µV par électron) : capacité du puits (27,6 ke⁻, celle du KAI-0373 ramenée à la surface du photosite, estimation) et bruit de lecture (55 e⁻, repris du KAI-0373, sans doute trop fort : à +18 dB l'image est très bruitée). Protection contre l'éblouissement (« excellente », sans chiffre) et transfert de charge : repris du KAI-0373.
- **Non simulés, bien que chiffrés par la fiche** : ombrage (20 % max.), papillotement entre trames (2 % max.), écarts entre lignes (3 % max.), rémanence (0,5 % max.). Ce sont des maximums garantis, pas des valeurs typiques.
- **Filtres idéaux** en dehors de la réponse au rouge ; primaires Rec.709. Les courbes spectrales de la fiche sont des images : non lues.
- **Convertisseur 10 bits, niveau noir 32** : valeurs libres (circuit du caméscope, pas le capteur).
- **ISO par défaut du panneau** : 800, soit −4 dB pour ce caméscope (moins de bruit qu'au gain 0 dB). Mettre 1250 pour le gain nominal.
- **Résolution** : voir plus bas, « vraie grille du capteur » (au choix).
- **Pas encore faits** pour le caméscope : automatismes (exposition, balance des blancs), enregistrement sur bande. (Accentuation, coude, couleurs dans les blancs et défauts de la fiche : faits le 2026-11-18, voir plus bas.)

### Ajouts du 2026-11-17 (nuit) : vraie grille du capteur, au choix

Demande de l'utilisateur : pouvoir simuler à la vraie résolution du capteur, **en gardant le choix** de rester à la résolution de l'image. Trois réglages, onglet « Capteur » :

| Réglage | Valeurs | Effet |
|---|---|---|
| `sensor_resolution` | `image` (défaut) / `capteur` | `image` : un pixel = un photosite, comme avant (résultat identique bit à bit, vérifié). `capteur` : la lumière est recueillie par la vraie grille de photosites, toute la chaîne tourne à cette résolution |
| `sensor_output` | `image` (défaut) / `capteur` | Taille de la séquence rendue : celle du calque, ou celle de la grille |
| `sensor_upscale` | `lanczos` / `bilinear` / `nearest` | Agrandissement vers la taille du calque (affichage, pas de la physique) |

**Fichiers.** `engine/camsim/resolution.py` (grille, recueil de la lumière, flux optique, agrandissement), `CameraSim.grid()` et `process_frame()` dans `pipeline.py`, clés `sensor_pixels`, `pixel_pitch_um` et `sensor_grid_source` dans chaque profil, `tests/test_resolution.py` (12 tests), planche `out/apercu_resolution_1010.png` (`tools/make_preview_resolution.py`).

**Cadrage.** L'image est placée dans la zone utile du capteur sans être déformée, en respectant la forme réelle des photosites. Image carrée sur le caméscope : 560 × 582 photosites (582 lignes, 582 × 6,25 / 6,50 colonnes) ; image 16:9 : 752 × 440. Recueil de la lumière : moyenne sur la surface de chaque photosite (microlentilles couvrant toute la surface). Le filtre passe-bas, les mosaïques, l'entrelacement et tous les réglages en « px » s'appliquent alors à l'échelle des photosites. Le flux optique (obturateur déroulant, seconde trame) est ramené à la grille.

**Grilles.**
| Profil | Grille | Source |
|---|---|---|
| Sony A7S II | 4240 × 2832 | fiches techniques (comparatif apotelyt.com) |
| Nikon D850 | 8256 × 5504 | idem |
| Canon 5D Mark IV | 6720 × 4480 | idem |
| Canon 70D | 5472 × 3648 | apotelyt.com, specspro.net |
| Canon 700D | 5184 × 3456 | idem |
| KAI-0373 (3 CCD) | 768 × 484, photosites 11,6 × 13,6 µm | fiche onsemi rév. 4 |
| ICX409AK (caméscope) | 752 × 582, photosites 6,50 × 6,25 µm | fiche Sony |
| Plumbicon, vidicon | 576 lignes × 768 points | PAL 625 lignes (576 utiles, ITU-R BT.601), 4:3 |

**Correction du caméscope trouvée grâce à ce mode.** À la vraie résolution, les détails sombres du rendu prenaient une teinte bleue. Cause : la luminance pleine finesse était combinée à des différences de couleur filtrées et retardées, ce qui colorait tout détail, même gris. Un caméscope fait sa balance des blancs sur R−Y et B−Y : un gris donne zéro, et seul ce qui est vraiment coloré passe par le filtre et le retard 1H. C'est corrigé dans `camcorder.separate()` (paramètre `white`), et un test le vérifie (écart entre canaux 1·10⁻¹⁵). Les fausses couleurs qui restent sont physiques : contours horizontaux (différence entre voisins) et détails d'un seul photosite de haut (les deux lignes additionnées n'ont pas les mêmes filtres).

**Limites.**
- **Photos, pas vidéo** : les grilles des appareils photo sont celles de la photo. Le mode vidéo (lignes sautées, regroupement, recadrage) est un réglage à part, ajouté ensuite (voir plus bas).
- **Tubes** : le balayage horizontal est continu ; il est échantillonné au pas des lignes (768 points pour une image 4:3), un peu plus grossier que la résolution limite du vidicon (600 lignes TV).
- **Vitesse** : à la grille du capteur, un caméscope ou un 3 CCD calcule une image 1920 × 1920 en moins d'une seconde ; un appareil photo est plus lent qu'en mode image (2832 × 2832 pour l'A7S II, 5 s ; le D850 sur une image carrée travaille sur 5504 × 5504).

**Total des tests : 124 sur 124 en Python** (21 noyau, 13 défauts, 22 pannes, 32 CCD et tubes, 8 lanceur, 16 caméscope, 12 résolution), plus 7 contrôles du panneau.

### Ajouts du 2026-11-17 (fin de nuit) : PNG, TIFF, JPEG et vidéos (mode d'entrée B)

| Fichier | Rôle |
|---|---|
| `engine/camsim/sources.py` | Lecture des sources : séquences EXR ; séquences ou images fixes PNG, TIFF, JPEG ; vidéos (MOV, MP4, M4V, AVI, MKV, MXF, WEBM) par ffmpeg. Retour à la lumière de scène |
| `config.json` | Chemin de ffmpeg et ffprobe (ici `un dossier local`) ; sinon ils sont cherchés dans le PATH |
| `engine/camsim/job.py` | Passe par `sources.open_source` ; sortie EXR ACEScg ou PNG 16 bits ; image fixe (`still`) |
| `ae/CameraSim.jsx` | Accepte les calques image, séquence ou vidéo ; image fixe importée comme image fixe, sur la même durée que la source |
| `tests/test_sources.py` | 14 tests ; `tests/check_panel.js` : 3 contrôles de plus (vidéo, image fixe, format refusé) |
| `tools/make_preview_sources.py` | Planche `out/apercu_sources_1010.png` : la même image depuis l'EXR et depuis un PNG 8 bits |

**Méthode** (ordre de la fonction `unprocess` de Brooks et al. 2019, dépôt `timothybrooks/unprocessing`) :
1. Valeurs codées vers [0, 1], avec un tremblement d'un demi-pas de code (désactivable) pour effacer les marches 8 bits sans changer la moyenne (vérifié).
2. Courbe de tonalité inverse, au choix : aucune (défaut) ou l'inverse de la courbe « smoothstep » de Brooks, 0,5 − sin(asin(1 − 2y)/3).
3. Courbe de transfert inverse : sRGB (IEC 61966-2-1), BT.709 (inverse de la courbe de prise de vue, constantes exactes de BT.2020 pour que la courbe soit continue), gamma 2,2, gamma 2,4, linéaire. « auto » : sRGB pour les images ; pour les vidéos, d'après l'étiquette, BT.709 par défaut.
4. Primaires Rec.709 / sRGB vers ACEScg (matrices calculées, Bradford) : un blanc d'écran donne (1, 1, 1).
5. Exposition (`input_exposure_ev`) : 0 = le blanc de l'écran vaut 1,0 en lumière de scène.

Les vidéos sont décodées en RVB 16 bits : les 10 bits d'un ProRes sont gardés (vérifié sur un ProRes 422 HQ). Matrice YUV vers RVB d'après l'étiquette, BT.709 sans étiquette. Sortie au choix (`output_format`) : EXR ACEScg linéaire, ou PNG 16 bits dans le même codage que la source (aller-retour exact vérifié, écart 0 sur 65535 sans bruit de caméra).

**Nouveaux réglages, onglet « Entree »** : courbe de la source, courbe de tonalité à défaire, exposition de la source, effacer les marches 8 bits, format de sortie. Sans effet sur les EXR.

**Vérifié** : courbes contre leurs valeurs de norme, blanc et gris neutres, allers-retours (4 courbes × 2 tonalités), tremblement, séquence PNG 16 bits avec alpha, vidéo 8 bits (nombre d'images, accès au hasard, gris neutre), vidéo 10 bits, rendus complets PNG → PNG et vidéo → EXR, protection du dossier source, image fixe numérotée. Sur l'image 1010, le caméscope donne presque la même image depuis un PNG 8 bits que depuis l'EXR.

**Limites.**
- **Les hautes lumières écrêtées ne reviennent pas** : au-delà du blanc de l'écran, l'information est perdue. Un soleil ou une lampe dans un PNG n'aura jamais l'éblouissement ni la traînée qu'il aurait depuis un EXR. C'est une limite de toute méthode de ce type.
- **Couleurs** : seules les primaires Rec.709 / sRGB sont prévues pour les sources traitées (pas de P3, Rec.2020 ni log de caméra).
- **Compositions AE** : pas lues directement. Il faut d'abord les rendre (EXR de préférence, sinon PNG 16 bits). Une lecture directe demanderait de piloter la file de rendu d'AE depuis le panneau.
- **Vidéos** : la sortie est une séquence d'images, pas un fichier vidéo. Le nombre d'images vient d'un comptage par ffprobe (lent sur de très longues vidéos). L'accès au hasard relance ffmpeg à chaque saut.
- **ffmpeg** : celui de `un dossier local` est utilisé. S'il est déplacé, mettre à jour `config.json`.

**Total des tests : 138 sur 138 en Python** (21 noyau, 13 défauts, 22 pannes, 32 CCD et tubes, 8 lanceur, 16 caméscope, 12 résolution, 14 sources), plus 10 contrôles du panneau.

### Ajouts du 2026-11-18 : lecture vidéo des appareils photo

En vidéo, un appareil photo ne lit pas tous ses photosites. Deux réglages, onglet « Capteur » (appareils CMOS seulement) :

| Réglage | Valeurs | Effet |
|---|---|---|
| `video_mode` | `off` (défaut) / `1080p` / `4k` | `off` : comportement photo, inchangé (vérifié bit à bit). Sinon : mode vidéo du profil |
| `video_readout` | `auto` / `full` / `skip3` / `bin3x3` / `vbin2` | `auto` : la méthode de l'appareil. Les autres permettent de la changer (par exemple lire un 700D comme un 5D Mark III) |

**Méthodes.** `skip3` : moyenne de 3 photosites de même couleur sur une ligne (colonnes x−2, x, x+2) et une ligne lue sur trois. `bin3x3` : même regroupement dans les deux sens, aucun photosite perdu. `vbin2` : regroupement vertical par 2. `full` : tout est lu, puis l'image est réduite par la caméra. Le résultat reste une mosaïque de Bayer (la parité des photosites centraux est conservée), dématricée puis mise à la taille de la vidéo.

**Modes des profils.**
| Appareil | 1080p | 4K | Source |
|---|---|---|---|
| Canon 70D, 700D | `skip3` | — | Magic Lantern : regroupement horizontal et une ligne sur trois sur tous les Canon sauf le 5D Mark III ; 700D : registres CMOS 5 et 800C |
| Canon 5D Mark IV | `skip3` (**hypothèse**) | recadrage 4096 × 2160, tout lu | EOSHD et Frame & Focal pour la 4K ; pour le 1080p, EOSHD dit seulement « pixel-binned » |
| Nikon D850 | `skip3` | `vbin2` sur toute la largeur, réduit à 3840 × 2160 (**déduit** par l'auteur de l'analyse, non confirmé) | landingfield.wordpress.com (2020) : 1080p à 1/3 dans chaque sens (2784 × 1854), 4K FX lue sur 8352 × 2328 |
| Sony A7S II | `full` | `full` | Sony : lecture de tous les photosites sans regroupement, en 4K comme en 1080p |

**Physique.** Chaque photosite sature d'abord seul. Puis les charges sont regroupées : la somme de n lois de Poisson est une loi de Poisson de la somme, donc le bruit de photons est exact (le test redonne une division de l'écart-type par 1,72 pour 3 photosites et 2,92 pour 9, contre 1,73 et 3 en théorie). La lumière est recueillie photosite par photosite, en deux temps pour ne pas fabriquer toute la grille : lignes d'abord (filtre passe-bas vertical à l'échelle des photosites), puis seules les lignes lues.

**Fichiers.** `engine/camsim/video.py`, paramètre `binning` de `Sensor.expose`, `CameraSim._process_video`, clé `video_modes` des profils, `tests/test_video.py` (11 tests), planche `out/apercu_video_1010.png` (`tools/make_preview_video.py`, avec une mire de cercles ajoutée pour la démonstration). Sur la planche, la lecture d'une ligne sur trois fait apparaître des cercles fantômes colorés (bleu, orange) sur la mire : c'est le moiré typique des reflex en vidéo. Le regroupement 3 × 3 et la lecture complète l'évitent.

**Limites.**
- **Pas de lecture directe des pages Magic Lantern** : elles sont protégées par une vérification anti-robot. Les informations viennent des extraits cités par la recherche et de l'analyse du D850. La position exacte des photosites regroupés (x−2, x, x+2) est celle qui conserve la mosaïque de Bayer ; elle est cohérente avec « averaging pixels of the same color » mais n'a pas pu être relue sur un schéma.
- **Bruit de lecture et défauts de colonne appliqués une fois** sur la sortie regroupée (hypothèse : regroupement analogique dans le capteur, avant la conversion).
- **Filtre de réduction de la caméra** (lecture vers taille vidéo) : moyenne par surface, inconnu en réalité. Pas d'accentuation des contours ni de compression vidéo (étape « enregistrement »).
- **Profondeur** : la vidéo du D850 est lue en 12 bits (analyse citée) ; le profil garde la profondeur photo.
- **Vitesse** : 2 à 5 s par image de 1920 × 1920 selon l'appareil.

**Total des tests : 149 sur 149 en Python** (21 noyau, 13 défauts, 22 pannes, 32 CCD et tubes, 8 lanceur, 16 caméscope, 12 résolution, 14 sources, 11 vidéo), plus 10 contrôles du panneau.

### Ajouts du 2026-11-18 (suite) : fin du caméscope

**Étage vidéo** (`engine/camsim/camdsp.py`, onglet « Etage video »). Chaîne classique d'un processeur de caméra vidéo, appliquée après le traitement des couleurs :
1. Courbe BT.709 (100 % vidéo = 1,0 en lumière de scène).
2. **Coude**, voie par voie (R, V, B) : au-dessus du point de coude, la pente est réduite. Une couleur vive qui dépasse le coude sur une voie se rapproche du blanc.
3. **Accentuation des contours** sur la luminance : différence avec les points à ±2 photosites (retards de deux périodes d'horloge, pic de réponse au quart de la fréquence d'échantillonnage, 3,5 MHz pour le caméscope) et avec les lignes voisines de la même trame (retard 1H). Seuil (coring) pour ne pas accentuer le bruit, limiteur pour borner les halos.
4. **Couleurs réduites dans les blancs** quand la voie la plus haute dépasse le coude.
5. **Écrêtage du blanc.**
Puis retour en linéaire par l'inverse de BT.709 : l'image reste linéaire pour AE mais garde le coude, les halos et l'écrêtage.

**Niveau du signal.** L'étage travaille au niveau réel du signal de la caméra (image × 2^ev) et rend l'image à ce niveau. Avec l'étage actif, +2 diaphragmes donnent donc une image 4 fois plus lumineuse, écrêtée et pâlie, comme un caméscope surexposé. Sans l'étage, le moteur garde son comportement : l'image est ramenée à la luminosité de la scène (l'exposition agit sur le bruit et la saturation).

**Activation.** Réglage `dsp_enabled` : `profil` (défaut), `on`, `off`. Actif par défaut pour le caméscope seulement (clé `dsp` du profil) ; les autres caméras gardent exactement leur résultat d'avant. Valeurs du caméscope, **libres** (la fiche du capteur ne décrit pas le processeur) : coude à 85 % avec une pente de 0,25 (la saturation du brut, environ 158 % vidéo, arrive vers 103 %), blanc écrêté à 107 %, accentuation 0,6 en horizontal et 0,4 en vertical, seuil 1 %, halos limités à 12 %, couleurs dans les blancs réduites à 80 %.

**Défauts de la fiche Sony** (onglets « Camescope » et « Capteur ») : `sensor_shading` (ombrage parabolique, définition de la fiche (Ymax − Ymin)/Y, maximum 0,20), `ccd_lag` (rémanence d'une image à l'autre, maximum 0,005), `cmyg_field_flicker` (écart de niveau de la trame B, maximum 0,02), `cmyg_line_crawl` (écart de luminance entre lignes « rouges » et « bleues », maximum 0,03). **Laissés à 0 par défaut** : la fiche ne donne que des maximums garantis, pas des valeurs typiques. L'ombrage et la rémanence servent aussi aux autres capteurs (ombrage : tous les capteurs ; rémanence : CCD).

**Fichiers.** `camdsp.py`, `tests/test_camdsp.py` (11 tests : coude, aplat inchangé, halos bornés, seuil, couleurs dans les blancs, écrêtage, activation par profil, ombrage, rémanence, écart entre trames, écart entre lignes ; tous retrouvent la valeur réglée), planche `out/apercu_etage_video_1010.png` (`tools/make_preview_camdsp.py`).

**Limites.**
- Toutes les valeurs de l'étage vidéo sont libres : aucune fiche de processeur de caméscope n'a été lue.
- Pas de dépendance de l'accentuation au niveau (les vrais processeurs l'atténuent souvent dans les noirs), pas de « skin detail », pas de correction gamma réglable (BT.709 fixe).
- Ombrage parabolique et symétrique : la fiche ne donne pas sa forme.

**Total des tests : 160 sur 160 en Python** (21 noyau, 13 défauts, 22 pannes, 32 CCD et tubes, 8 lanceur, 16 caméscope, 12 résolution, 14 sources, 11 vidéo, 11 étage vidéo), plus 10 contrôles du panneau.

### Ajouts du 2026-11-18 (fin) : automatismes

Demande de l'utilisateur : des automatismes **activables ou non**, avec des réglages pour leurs paramètres. Nouvel onglet « Automatismes » (16 réglages), tout est coupé par défaut.

**Exposition automatique** (`auto_exposure`). La caméra mesure l'image qu'elle vient de produire et règle la suivante (une image de retard, vérifié). Boucle du second ordre sur l'exposition totale x, en diaphragmes : erreur e = log2(cible / mesure), nulle dans une zone morte ; accélération w²e − 2zwv avec w = 1 / temps de réaction et z = amortissement ; vitesse bornée. Amortissement 1 : aucun dépassement ; 0,2 : dépassements et allers-retours (pompage). L'exposition est prise d'abord par le diaphragme (plus de lumière, donc plus d'électrons) entre ses butées, puis par le gain (AGC : électrons inchangés, signal et bruit amplifiés), plafonné. Mesures : moyenne, pondérée centrale, spot (12 % du centre), hautes lumières (le 99e centile vise 100 %).
Réglages : mesure, luminosité visée (0,18), temps de réaction (0,4 s), amortissement (0,7), écart toléré (0,15 diaph.), vitesse maximale du diaphragme (4 diaph./s), butées du diaphragme (−6 / +2 diaph.), gain automatique (oui), gain maximal (18 dB).

**Balance des blancs automatique** (`auto_wb`). Estimation de la couleur de l'éclairage : moyenne des couleurs (« monde gris ») ou moyenne des 3 % de points les plus clairs (« point blanc »), sans les points trop sombres ni le demi-pourcent le plus clair. Correction progressive (premier ordre) des gains rouge et bleu, éventuellement partielle (part 0,7 : il reste de la chaleur, comme sur beaucoup de caméras). Réglages : méthode, temps de réaction (1,5 s), part de la correction (1), écart toléré.

**Niveau de sortie.** Avec l'exposition automatique, l'image sort au niveau du signal de la caméra : quand la lumière change, l'image change, puis la caméra la ramène.

**Mise en route.** Les images doivent être traitées dans l'ordre. Le lanceur traite d'abord 2 secondes d'images avant la première image rendue (1 seconde pour l'aperçu), pour que la caméra soit déjà réglée. L'aperçu est donc plus lent avec les automatismes.

**Fichiers.** `engine/camsim/auto.py`, `Sensor.set_exposure` (gain et exposition changeables à chaque image, avec le bruit calibré ELD qui suit le gain), `CameraSim._apply_auto` et `process_frame`, `tests/test_auto.py` (11 tests : coupé = identique bit à bit, retour vers le gris après un échelon ×4 à −2,00 diaph., image de retard, amortissement et pompage, zone morte, butée du diaphragme puis gain jusqu'à 18 dB, mesure des hautes lumières, balance complète et partielle, refus pour les tubes). Démonstration : `out/automatismes_demo.mp4` et `out/apercu_automatismes.png` (`tools/make_preview_auto.py` : l'éclairage de l'image 1010 est modifié pour la démonstration, ×4 puis orange puis normal).

**Limites.**
- Valeurs par défaut courantes, pas mesurées sur une caméra précise.
- Pas pour les tubes (refusé avec un message).
- Le temps de pose ne fait pas partie du programme d'exposition (il agit sur la traînée, le scintillement et le flou, réglés à part).
- La mise au point automatique n'est pas simulée : elle passe par l'objectif, hors du projet.

**Total des tests : 171 sur 171 en Python** (21 noyau, 13 défauts, 22 pannes, 32 CCD et tubes, 8 lanceur, 16 caméscope, 12 résolution, 14 sources, 11 vidéo, 11 étage vidéo, 11 automatismes), plus 10 contrôles du panneau.

### Ajouts du 2026-11-18 (nuit) : enregistrement, codecs numériques

Le signal de la caméra passe par un **vrai codec** (ffmpeg), puis il est relu. Chaîne : ACEScg linéaire → Rec.709 → courbe BT.709 (signal vidéo) → image placée dans le cadre du format, sans déformation, avec la forme réelle des pixels → Y'CbCr et sous-échantillonnage de la couleur → codec → erreurs de flux éventuelles → décodage → retour en linéaire ACEScg → taille du calque. Fichier `engine/camsim/record.py`, onglet « Enregistrement ».

| Format | Cadre | Couleur | Codec | Source des chiffres |
|---|---|---|---|---|
| DV / MiniDV PAL | 720 × 576, pixels 16/15 | 4:2:0 | DV, 25 Mbit/s, image par image, entrelacé | IEC 61834 / SMPTE 314M |
| DV / MiniDV NTSC | 720 × 480, pixels 10/11 | 4:1:1 | idem | idem |
| DVD | 720 × 576 | 4:2:0 | MPEG-2 MP@ML, 5 Mbit/s (max. 9,8), groupes de 12 | DVD-Video (débit et groupe : valeurs courantes) |
| HDV 1080 | 1440 × 1080, pixels 4/3 | 4:2:0 | MPEG-2 MP@H-14, 25 Mbit/s, groupes de 15 | HDV 1080i |
| Motion JPEG | 640 × 480 | 4:2:0 | MJPEG, qualité 6 | compacts des années 2000 (qualité libre) |
| H.264 téléphone | 1920 × 1080 | 4:2:0 | H.264 High, 17 Mbit/s, groupes de 30, sans images B | ordre de grandeur, libre |
| H.264 reflex | 1920 × 1080 | 4:2:0 | H.264 IPB, 45 Mbit/s, groupes de 12 | ordre de grandeur des Canon, libre |
| 3GP | 176 × 144, pixels 12/11 | 4:2:0 | H.263, 64 kbit/s, 15 images/s | 3GPP TS 26.234 (débit et cadence : courants) |

**Réglages** : format (`rec_format`, coupé par défaut), débit × (`rec_bitrate_scale`), longueur des groupes d'images (`rec_gop`), part d'octets abîmés (`rec_errors`), part de paquets perdus (`rec_drop`), cadence propre du format (`rec_native_fps`, le 3GP à 15 images/s : images répétées à la relecture).

**Erreurs de flux** : filtre `noise` de ffmpeg sur les paquets compressés (octets modifiés, paquets perdus). Ce sont de vraies données abîmées, décodées par un vrai décodeur avec ses propres masquages d'erreur (blocs déplacés, images figées). Le résultat est reproductible d'un calcul à l'autre (vérifié).

**Vérifié** (`tests/test_record.py`, 9 tests) : gris juste dans les 8 formats (écart de 0,2 % ; 0,9 % en H.264 ; 2 % en MJPEG), cadrage sans déformation, perte de couleur plus forte en 4:1:1 qu'en 4:2:0, effet du débit, erreurs de flux reproductibles, images figées par paquets perdus, cadence du 3GP, rendu et aperçu complets depuis le lanceur. Démonstration sur l'animation du rendu : `out/enregistrement_demo.mp4` et `out/apercu_enregistrement.png` (`tools/make_preview_record.py`).

**Limites.**
- **Pas de couche alpha en sortie** : les codecs n'en ont pas.
- **Débit minimal du MPEG-2** : le quantificateur de ffmpeg plafonne, donc « débit × » ne descend pas sous un plancher pour le DVD et le HDV. Le H.264 descend aussi bas qu'on veut.
- **MJPEG** : écart d'un code sur la couleur (invisible, mesurable dans les gris très sombres).
- **Surblancs écrêtés** : le signal est limité à 100 % avant le codec (les vrais caméscopes enregistrent jusqu'à 109 %).
- **Aperçu** : pour les formats à groupes d'images, l'aperçu d'une image seule montre une image clé. Les défauts entre images (images P et B, propagation des erreurs) n'apparaissent qu'au rendu de la séquence.
- **Pas encore faits** : signal analogique (composite PAL/NTSC), bande magnétique (VHS, Hi8), sortie dans un vrai fichier vidéo.

### Intégration de l'enregistrement dans After Effects (demande de l'utilisateur)

**Le problème.** Un effet After Effects calcule une image à la fois, dans l'ordre que choisit AE, souvent dans le désordre (lecture, tête de lecture déplacée, rendu multiprocesseur). Or une partie de l'enregistrement dépend des images voisines.

**Le code sépare donc deux familles** (`kind` dans `record.FORMATS`) :
- **« intra »** (DV, MJPEG) : chaque image est codée seule. Elle peut être calculée image par image, donc **en direct** dans un effet.
- **« gop »** (DVD, HDV, H.264, 3GP) : une image dépend des précédentes et des suivantes, et les erreurs de flux se propagent jusqu'à la prochaine image clé. Il faut **toute la séquence, dans l'ordre**.

Même contrainte pour les automatismes, la rémanence des tubes et du CCD, et l'entrelacement : tout ce qui a une mémoire d'une image à l'autre.

**Plan proposé pour l'effet natif** (à décider avec l'utilisateur au moment de le faire) :
1. **Deux effets** : « CameraSim Caméra » (capteur, électronique, traitement, en direct sur la carte graphique) et « CameraSim Enregistrement » (appliqué après, sur le même calque ou sur un calque d'effets).
2. **Formats intra en direct** : codage et décodage d'une image à la fois, avec la même bibliothèque que ffmpeg (libavcodec, licence LGPL, liée au module) ou en réécrivant la compression DV et JPEG, dont les normes sont publiques (IEC 61834, ITU-T T.81). Résultat identique au calcul actuel.
3. **Formats à groupes en direct, en approximation** : chaque image est codée comme une image clé du bon codec au bon débit. On voit les blocs et le flou du format, pas les défauts entre images. Le panneau d'effet l'indique.
4. **Rendu exact des formats à groupes, des erreurs de flux et des automatismes** : un bouton « Calculer la séquence » qui fait le calcul complet dans l'ordre (comme le panneau actuel), écrit une séquence et la pose dans la composition. Autre possibilité, plus élégante mais plus risquée : l'effet calcule toute la plage du calque en arrière-plan au premier besoin et garde le résultat en cache. À trancher plus tard.

Le panneau actuel fait déjà le calcul exact (point 4) : l'enregistrement y est appliqué sur toute la séquence, dans l'ordre.

**Total des tests : 180 sur 180 en Python** (… 11 automatismes, 9 enregistrement), plus 10 contrôles du panneau.

### Ajouts du 2026-11-18 (fin de nuit) : signal analogique et bande

Fichier `engine/camsim/analog.py`, onglet « Analogique » (20 réglages, coupé par défaut). Code écrit à partir des normes, **sans reprendre le code de ntsc-rs** (licence non vérifiée), qui a seulement servi de liste d'effets connus.

**Signal.** L'image (après la caméra) est convertie en signal vidéo : courbe BT.709, Y'UV (PAL) ou Y'IQ (NTSC), une ligne d'échantillons par ligne de balayage, échantillonnée à 4 fois la sous-porteuse (PAL : 922 échantillons sur 52 µs, 576 lignes ; NTSC : 754 sur 52,66 µs, 486 lignes). L'image est placée dans la trame 4:3 sans déformation. Tous les filtres agissent le long des lignes, en MHz.

**Bande** (`analog_tape` : VHS SP, VHS LP, S-VHS, Video8, Hi8), système « color-under » :
- luminance limitée (passe-bas d'ordre 4, −3 dB à la bande passante du format) et bruit de démodulation FM au **spectre triangulaire** (amplitude proportionnelle à la fréquence) : vérifié, 20 fois plus de puissance vers 2,2 MHz que vers 0,5 MHz, et écart-type exact pour le rapport signal/bruit réglé ;
- couleur très étroite (0,4 MHz), bruitée (traînées colorées), en retard sur la luminance (vérifié), moyennée sur deux lignes à la lecture ;
- base de temps instable (lignes qui ondulent), haut de l'image qui penche, commutation des têtes sur les dernières lignes de chaque trame, pertes de signal (traînées claires, ou ligne précédente recopiée par le compensateur), bande mal calée (bande de lignes bruitées), accentuation du magnétoscope (halos), copies successives.
- Chiffres : VHS et Video8 vers 3 MHz (234 lignes, avec 78 lignes par MHz = 2 × 1 MHz × 52 µs × 3/4), S-VHS et Hi8 vers 5 MHz (environ 400 lignes), couleur vers 0,4 MHz (30 lignes) pour tous (articles S-VHS et Hi8, sources citées dans la recherche). Rapports signal/bruit (43 dB en VHS SP, 45 en S-VHS et Hi8, couleur 38-39), retards, instabilités, commutation, LP : **valeurs courantes, libres**.

**Liaison composite** (`analog_composite`) : couleur modulée sur la sous-porteuse (PAL 4,43361875 MHz, phase de V inversée une ligne sur deux ; NTSC 3,579545 MHz, I et Q), phase suivie dans l'ordre réel du balayage (trame par trame), canal vidéo plat jusqu'à 5 MHz (PAL B/G) ou 4,2 MHz (NTSC M), bruit de liaison, puis séparation par **filtre coupe-bande** (téléviseur simple : irisations sur les détails fins, chenillement de points aux bords des couleurs) ou **filtre en peigne** (ligne de phase opposée dans la même trame : plus propre), démodulation, ligne à retard PAL. Erreur de teinte réglable en NTSC (le PAL la corrige par construction). Bandes de couleur : PAL 1,3 MHz ; NTSC I 1,3 MHz et Q 0,4 MHz (valeurs classiques de la norme NTSC, non revérifiées ici).

**Place dans la chaîne.** Après la caméra et ses automatismes (la caméra mesure avant la bande), avant l'enregistrement numérique (on peut donc faire « une VHS copiée sur DVD »). **Image par image** : le résultat ne dépend que du numéro d'image (vérifié), pas des images voisines. Cet étage pourra donc tourner **en direct** dans un futur effet After Effects (famille « intra »).

**Vérifié** (`tests/test_analog.py`, 11 tests) : 78 lignes par MHz, gris neutre dans 16 combinaisons (écart max. 1,7 %), bande passante VHS (−3 dB à 3 MHz) contre S-VHS, spectre et niveau du bruit FM, retard et étalement de la couleur, fausses couleurs avec le coupe-bande et pas avec le peigne, chenillement d'une image à l'autre, commutation limitée au bas de l'image, pertes de signal avec et sans compensateur, indépendance de l'ordre de calcul, branchement dans la caméra (coupé = identique bit à bit). Démonstration : `out/analogique_demo.mp4`, `out/apercu_analogique.png` (`tools/make_preview_analog.py`, mire de couleurs ajoutée).

**Limites.**
- Pas de synchronisation simulée (pas d'image qui « décroche » verticalement, pas de salve visible) ; pas de SECAM.
- Pas de préaccentuation/désaccentuation FM explicite ni de « bavure » non linéaire des forts contrastes en FM.
- La couleur sur bande n'est pas remodulée à 627 kHz : son effet (bande étroite, bruit, retard) est appliqué directement.
- Bruit, retards, instabilités : valeurs libres, à caler si on trouve des mesures de magnétoscopes.

**Total des tests : 191 sur 191 en Python** (… 9 enregistrement, 11 analogique), plus 10 contrôles du panneau.

### Ajouts du 2026-11-18 (dernière étape du plan de base) : couleur spectrale

Les appareils photo voient maintenant les couleurs à travers leurs **vraies sensibilités spectrales mesurées**, au lieu d'une matrice 3 × 3. Fichier `engine/camsim/spectral.py`, réglage `spectral` (onglet « Traitement » : `profil`, `on`, `off`). Actif par défaut pour les cinq appareils photo.

**Données** (téléchargées avec l'accord de l'utilisateur, dans `references/third_party/`) :
- `rawtoaces-data/` (Academy Software Foundation, licence Apache-2.0, fichier `LICENSE` joint) : observateur CIE 1931, 190 couleurs de référence mesurées, sensibilités R, V, B de 380 à 780 nm par pas de 5 nm, mesurées par Weta Digital (système « lightsaber », 2022). **Sony ILCE-7SM2 (A7S II), Nikon D850 et Canon EOS 5D Mark IV sont mesurés eux-mêmes.** Le 700D et le 70D ne sont pas mesurés : **appareils voisins** (Canon EOS 100D, même génération et même définition, pour le 700D ; Canon EOS 200D, même technologie Dual Pixel, pour le 70D), signalés dans les profils.
- `cie/CIE_std_illum_D65.csv` : illuminant D65 officiel (ISO/CIE 11664-2:2022, tableau B.1).

**Méthode.**
1. **Spectre de chaque couleur** (les rendus sont en RVB) : méthode de Jakob et Hanika (Eurographics 2019), spectre lisse sigmoïde d'un polynôme du second degré, ajusté par Levenberg-Marquardt pour que, éclairé par D65 et vu par l'observateur CIE 1931, il redonne la couleur ACEScg (adaptée à D65). La forme ne dépend que de la chromaticité : elle est calculée une fois sur une grille de 48 × 48 par voie dominante (3 tables), mise en cache (`_coef_jakob2019_48.npy`). La réponse de la caméra à ces spectres est gardée directement dans la table : à l'usage, c'est une **table interpolée**, rapide et transposable sur carte graphique.
2. **Réponse de la caméra** : intégrale spectre × D65 × sensibilités.
3. **Matrice de la caméra** : ajustée comme le font les fabricants (matrices DNG), par moindres carrés sur les 190 couleurs éclairées par D65, avec le blanc conservé exactement.

**Vérifié** (`tests/test_spectral.py`, 10 tests) : blanc D65 retrouvé avec les données (x 0,3127, y 0,3290) ; spectres ajustés à moins de 0,55 % pour 99 % des couleurs de la grille (pire 3,1 %, au bord des couleurs possibles) ; gris plat, rouge saturé étroit ; **balance des blancs calculée depuis les courbes égale à celle des matrices Adobe des profils à 6 % près** (sources indépendantes) ; gris neutre en sortie ; **erreur de couleur sur les 190 couleurs : ΔE*ab moyen 2,15 (A7S II), 2,41 (D850), 1,89 (5D IV)**, ordre de grandeur réel d'un appareil profilé ; erreur 25 fois plus grande sur des couleurs très saturées que sur des couleurs presque neutres ; mode `off` identique bit à bit à la matrice d'avant ; refus pour un capteur sans courbes. Planche : `out/apercu_spectral.png` (`tools/make_preview_spectral.py`).

Les tests d'aller-retour exact du noyau (`test_core`) et des sources tournent maintenant en mode `off` : ils vérifient la mécanique du moteur, que la couleur spectrale rend volontairement inexacte, comme un vrai capteur.

**Limites.**
- Les rendus RVB n'ont pas de spectre : celui qui est reconstruit est **un** spectre plausible, lisse. Deux couleurs identiques en RVB restent identiques (pas de métamérisme entre objets de la scène) ; ce qui change, c'est la façon dont chaque capteur voit chaque couleur.
- Couleurs à la limite de l'espace ACEScg (rouge ou vert purs) : spectre approché (erreur jusqu'à 3 %).
- Pas de courbes pour les CCD et les tubes (matrice idéale, comme avant). 700D et 70D : appareils voisins.
- L'éclairage de la scène est supposé être celui de la couleur RVB (pas de lumière spectrale séparée : néons, sodium… impossibles à retrouver depuis un rendu RVB).

**Total des tests : 201 sur 201 en Python** (… 11 analogique, 10 couleur spectrale), plus 10 contrôles du panneau. **Le plan de base (capteur, électronique, traitement) est terminé**, ainsi que l'enregistrement numérique et analogique.

### Effet natif After Effects : architecture prévue (2026-11-18)

**État du PC.** Absents : Visual Studio (compilateur C++), CMake, kit de développement After Effects (SDK), CUDA. Présents : Git, After Effects 2025.

**Principe.**
- **Un cœur de calcul en C++** (`native/core/`), indépendant d'After Effects, qui reprend le moteur Python étape par étape. Le moteur Python reste la **référence** : chaque étape portée est comparée à lui par un petit outil en ligne de commande (`native/cli/`). Les comparaisons portent sur les statistiques quand le hasard intervient (bruit), et à l'identique sinon.
- **Un module After Effects mince** (`native/ae/`), qui ne fait que relier les curseurs d'AE au cœur. Effet « SmartFX » en 32 bits flottants, compatible avec le rendu multi-images d'AE 2025.
- **Profils** : les mêmes fichiers JSON que le moteur Python, lus par le module.

**Étapes.**
1. Squelette de l'effet + caméra CMOS (exposition, bruit calibré ELD, non-uniformités, mosaïque, dématriçage, balance des blancs, couleur spectrale par table) sur le processeur. But : voir l'effet dans AE, en direct sur de petites tailles.
2. Défauts, pannes, CCD, caméscope, étage vidéo, signal analogique, DV/MJPEG : tout ce qui se calcule image par image.
3. Carte graphique (CUDA ou DirectX 12, au choix au moment venu) pour le direct en pleine résolution.
4. Ce qui demande la séquence entière (automatismes, rémanence, tubes, codecs à groupes d'images, erreurs de flux) : approximation en direct + bouton « Calculer la séquence » (voir la question ouverte en section 11).

**Outils à installer par l'utilisateur** : Visual Studio 2022 Community, charge de travail « Développement Desktop en C++ » (compilateur, Windows SDK, CMake) ; kit de développement After Effects 2025 pour Windows (site Adobe Developer, compte Adobe).

### Effet natif, étape 1 faite (2026-11-18) : la caméra CMOS dans After Effects

**Outils installés par l'utilisateur** : Visual Studio Community 2026 (version 18.12, jeu d'outils v145, CMake inclus) ; SDK After Effects 25.6 dans `<dossier du SDK After Effects>/Examples`. Chaîne vérifiée en compilant l'exemple « Skeleton » d'Adobe.

**Fichiers.**
| Fichier | Rôle |
|---|---|
| `tools/export_native.py` | Écrit `native/core/profiles_data.h` : profils et constantes calculées par le moteur Python (bruit ELD, matrices, tables spectrales). À relancer après toute modification d'un profil |
| `native/core/camsim.h`, `camsim.cpp` | Cœur C++ : scène → caméra (matrice ou table spectrale), filtre passe-bas (63 coefficients), mosaïque, non-uniformités, courant d'obscurité, photons, saturation, biais par voie, bruit de lecture Tukey-lambda ou gaussien, bruit de ligne, convertisseur ; noir, balance des blancs, écrêtage, dématriçage Malvar / bilinéaire / plus proche, matrice. Calcul parallèle (OpenMP) |
| `native/cli/main.cpp` | Outil de comparaison (image brute float32) |
| `native/ae/CameraSim.cpp`, `CameraSimPiPL.r` | Effet After Effects « CameraSim » (catégorie CameraSim) : SmartFX, 8/16/32 bits, rendu multi-images |
| `native/CMakeLists.txt` | Construction (`-DAE_SDK=<dossier Examples>`), bibliothèques d'exécution intégrées (reste `VCOMP140.DLL`, installée avec AE) |
| `native/dist/CameraSim.aex` | Le module compilé, à copier dans After Effects |
| `tests/compare_native.py` | Comparaison du C++ au moteur Python |

**Hasard** : générateur « à compteur » (une valeur par photosite, par image et par usage). Le résultat ne dépend que du numéro d'image et de la graine, pas de l'ordre de calcul ni du découpage en fils : indispensable dans AE, qui calcule les images dans le désordre et en parallèle. Loi de Poisson exacte sous 30 électrons, approximation normale au-dessus.

**Comparaison au moteur Python** (`tests/compare_native.py`, 5 sur 5) : sans bruit, **même image au pixel près** (écart 1,5·10⁻⁷) dans 30 cas (5 appareils × matrice ou spectral × 3 dématriçages) ; filtre passe-bas à 0,4 % près ; avec le bruit, même niveau moyen (écart inférieur à 4 fois l'incertitude statistique) et même écart-type (à 1,3 % près), pour 3 lumières × 2 sensibilités × 2 appareils ; image indépendante de l'ordre de calcul ; **0,2 s par image 1920 × 1080** sur le processeur (Python : plusieurs secondes).

**Réglages de l'effet** : caméra (5 appareils photo), espace de travail (ACEScg linéaire, sRGB non linéaire, Rec.709 linéaire) ; Prise de vue (ISO, exposition, temps de pose, température, numéro de série) ; Bruit (photons, modèle de bruit de lecture, bruit de lecture ×, bruit de ligne ×, biais du noir ×, non-uniformité × profil, courant d'obscurité × profil) ; Capteur (filtres passe-bas, profondeur, saturation du puits) ; Traitement (dématriçage, balance des blancs rouge et bleu, écrêtage, couleur spectrale). Tous animables par images clés.

**Vérifié dans After Effects par l'utilisateur** (2026-11-18), après correction d'une erreur « Checkout ID is not unique » (le calque était demandé deux fois avec le même identifiant) : l'effet se charge et fonctionne.

**Limites de cette étape.**
- Appareils photo CMOS seulement ; pas encore les défauts, pannes, CCD, tubes, étage vidéo, analogique, codecs, automatismes, grille du capteur, mode vidéo.
- En aperçu à résolution réduite (1/2, 1/4), chaque pixel calculé compte comme un photosite : le grain paraît plus gros qu'en pleine résolution.
- Couche alpha transmise telle quelle ; pixels supposés non prémultipliés.
- Calcul sur le processeur (l'étape 3 passera sur la carte graphique).

### Effet natif, version 2.0 (2026-11-18) : tout ce qui se calcule image par image

Demande de l'utilisateur : « il manque certains effets ? » → « Oui, tout ».

**Ajouté au cœur C++** (comparé au moteur Python à chaque étape) :
| Fichier | Contenu |
|---|---|
| `native/core/util.cpp` | Transformée de Fourier (Bluestein), filtres de lignes, redimensionnements identiques à OpenCV (surface, bilinéaire, Lanczos, plus proche) |
| `native/core/camsim.cpp` | Défauts fixes et pannes (pixels chauds, morts, collés, colonnes et lignes mortes, décalage et gain des colonnes, bits d'adresse collés, lignes et colonnes lues au mauvais endroit, parasite, convertisseur : marches irrégulières, courbure, bit collé, bits inversés, glissement de bits, lignes qui glissent), ombrage ; CCD (traînée, éblouissement, pertes au transfert, colonnes bloquées, lecture par trames), 3 CCD (décalage des capteurs, entrelacement), caméscope à un CCD (mosaïque CMYG, séparation, ligne à retard, papillotement des trames, lignes qui rampent) ; lecture vidéo des appareils photo |
| `native/core/post.cpp` | Étage vidéo (coude, contours, seuil, limiteur, blancs) et signal analogique (PAL/NTSC, bandes, composite, peigne, pertes, calage, copies) |
| `native/core/grid.h`, `frame.cpp` | Vraie grille du capteur, mode vidéo 1080p/4K, enchaînement complet d'une image |

**Comparaisons** (toutes réussies) : `compare_native.py` 5/5, `compare_native_post.py` 5/5, `compare_native_faults.py` 16/16, `compare_native_ccd.py` 14/14, `compare_native_frame.py` 14/14.

**Effet After Effects 2.0** : 7 caméras (5 appareils photo, 3 CCD, caméscope) ; 14 groupes de réglages : Prise de vue, Résolution et lecture vidéo, Bruit, Capteur, Défauts fixes, Pannes, Convertisseur, CCD et 3 capteurs, Caméscope, Traitement, Étage vidéo, Analogique. Tous animables. Convention : « -1 = profil » ou « -1 = bande » garde la valeur mesurée du profil ou du format de bande. La liste des réglages a changé : un effet 1.0 déjà posé dans un projet doit être remplacé.

**Retour de l'utilisateur : « quelques réglages n'ont pas l'air d'avoir d'effet »** (2026-11-18). Mesure sur une mire d'essai (`tools/make_fiche.py`) : 9 réglages sur 97 ne changeaient rien. Causes et corrections :
- Non-uniformité et courant d'obscurité « × profil » : non mesurés (0) sur les 5 appareils photo, donc multipliés par zéro ; pixels chauds, temps de pose et température en dépendent. → Curseurs en valeur directe (%, e-/s à 20 °C), -1 = profil.
- Commutation des têtes : une image 16:9 est placée dans la trame 4:3 avec des bandes noires, et la commutation tombe dans la bande noire. → Nouveau choix « Cadrage dans l'image 4:3 » : bandes noires ou anamorphose (Python `analog_framing`, natif `anamorphic`, comparé : écart 2·10⁻⁶).
- Saturation du puits, éblouissement : effets réels mais très localisés (autour des lumières fortes).
- Les réglages qui n'agissent pas avec la caméra ou les choix actuels sont maintenant **grisés** dans After Effects (PF_UpdateParamUI, PF_OutFlag_SEND_UPDATE_PARAMS_UI).
- Fiche explicative de chaque réglage, avec avant / après / différence calculés par le cœur natif : `tools/make_fiche.py` puis `tools/build_fiche_html.py` → `out/fiche/CameraSim_fiche.html`.

**Séparation en deux effets** (demande de l'utilisateur, 2026-11-18) :
- `CameraSim` 2.1 (`native/dist/CameraSim.aex`) : caméra (capteur, défauts, pannes, convertisseur, CCD, caméscope, traitement, étage vidéo). Le cœur de la caméra reste un seul effet : l'ordre des étapes suit la physique (une panne du capteur arrive avant le dématriçage).
- `CameraSim Analogique` 1.0 (`native/dist/CameraSimAnalog.aex`, `native/ae/CameraSimAnalog.cpp`) : signal PAL / NTSC et bande, à placer après CameraSim ; marche aussi sur n'importe quelle image. Par défaut PAL + VHS. Sa propre graine du hasard (1 par défaut = même tirage qu'avant la séparation).
- En 32 bits, CameraSim puis CameraSim Analogique donne le même résultat que l'ancien effet unique. En 8 ou 16 bits, l'image est arrondie entre les deux effets.

**Effet 2.2 (2026-11-18) : temps et mouvement**, avec les images voisines.
- `native/core/shutter.cpp` : obturateur déroulant, scintillement de l'éclairage, seconde trame une demi-image plus tard (comme `engine/camsim/shutter.py`). Le mouvement est estimé par **DIS** (Kroeger et al. 2016) réécrit en C++ avec les réglages de `cv2.DISOpticalFlow` preset MEDIUM utilisés par le moteur Python : blocs de 8 px tous les 3 px, 25 itérations, propagation, densification, affinage variationnel (α 20, δ 5, γ 10, ε 0,001, ω 1,6, intensités ramenées à [0, 1]). Un premier essai avec Farnebäck était trop imprécis au-delà de 15 px de mouvement : abandonné.
- `camsim::process_impl` accepte la scène de la seconde trame (3 CCD entrelacés, caméscope) ; `process_frame` reçoit l'image précédente et la suivante (vraie grille : elles passent aussi par la grille).
- Identifiants des réglages (uu.id) stables : chaque réglage garde à vie le numéro enregistré dans les projets (fonction `disk_id`) ; les réglages de « Temps et mouvement » ont 200 et plus. Une première version 2.2 les avait renumérotés et ne pouvait plus ouvrir les projets faits en 2.1 (« missing data in file »).
- Effet AE : groupe « Temps et mouvement » (temps de lecture des lignes, sens, mouvement estimé ou ignoré, scintillement : fréquence, profondeur, phase). L'effet demande à After Effects l'image précédente et la suivante (identifiants de lecture 1001 et 1002) seulement si c'est utile ; une image voisine entièrement transparente (avant ou après le calque) est ignorée et le mouvement est supposé régulier. Cadence prise dans la composition.
- Comparaison `tests/compare_native_shutter.py` (15 sur 15) : flux à 0,03-0,05 px de celui d'OpenCV sur des translations jusqu'à 30 px ; scintillement au pixel près ; obturateur et seconde trame à 1-3·10⁻⁴ près (médiane) ; objet qui bouge : écarts seulement aux occultations. Vitesse : environ 0,2 s par estimation de mouvement en 1920 × 1080 (OpenCV : 0,15 s) ; l'obturateur en demande deux, la seconde trame une.

**Effet 2.3 (2026-11-18) : vitesse sur le processeur** (le processeur était à 100 % dans After Effects).
- Rendu multi-images : chaque image ne prend que sa part des cœurs (`ThreadShare` dans `frame.cpp` : cœurs / images en cours), au lieu que chacune les prenne tous.
- Filtre passe-bas optique réécrit (lignes recopiées avec bords en miroir, additions de lignes entières) : 2,5 fois plus rapide, résultat identique.
- Moins de copies dans `process_frame` ; luminance de l'image courante calculée une fois pour les deux mesures de mouvement ; mouvement vers l'image suivante réutilisé pour la seconde trame ; retour du flux à pleine résolution sans tableaux intermédiaires.
- Nouveau réglage « Précision du mouvement » : Fine (comme OpenCV, moitié de la résolution) ou Rapide (quart de la résolution) : 104 ms au lieu de 199 ms par mesure en 1920 × 1080, obturateur déroulant identique à 0,02-0,04 % près. Identifiant disque 208.
- Mesures (1920 × 1080, sans After Effects) : caméra seule 133 ms par image (276 avant) ; mesure du mouvement 200 ms (fine) ou 104 ms (rapide). Variable `CAMSIM_TIMING=1` : temps de chaque étape.
- Comparaisons : 71 sur 71 (dont `compare_native_shutter.py` 16/16).

**Effet 2.4 (2026-11-18) : carte graphique NVIDIA, première étape.** Demande de l'utilisateur : compatible avec les cartes NVIDIA en général, pas seulement la sienne.
- **Choix technique.** CUDA Toolkit 12.9 (installé par l'utilisateur) : dernière version qui couvre Maxwell (GTX 900, 2014) jusqu'à Blackwell (RTX 50) ; CUDA 13 abandonne les cartes d'avant Turing. `nvcc` plante avec Visual Studio 2026 (`cudafe++` ACCESS_VIOLATION, même sur un fichier vide) : les noyaux sont donc compilés par **NVRTC** (sans Visual Studio) à la construction, pour sm_50, 52, 60, 61, 70, 75, 80, 86, 89, 90, 100, 120, plus du PTX compute_50 recompilé par le pilote pour les cartes futures. Le module charge `nvcuda.dll` à l'exécution (`LoadLibrary`) : aucune dépendance, il s'ouvre sur un PC sans NVIDIA (dépendances vérifiées : KERNEL32, VCOMP140). Pilote requis : 528 ou plus récent (binaires), plus récent pour le PTX.
- **Fichiers.** `native/gpu/kernels.cu` (noyaux, sans bibliothèque standard), `gpu_params.h` (paramètres, lu par les deux compilateurs), `build_kernels.cpp` (construction NVRTC → `gpu_kernels_data.h`), `gpu.h/.cpp` (pilote chargé à l'exécution, choix du binaire selon la génération, sessions par image pour le rendu multi-images, réserve de mémoire de 1,5 Go au plus). CMake : `CUDA_TOOLKIT` (sans toolkit, construction pour le processeur seul).
- **Porté :** toute la chaîne d'un appareil photo (mosaïque de Bayer, sans lecture vidéo) : scène → caméra (matrice ou courbes), filtre passe-bas (somme compensée en simple précision : les cartes grand public calculent 64 fois plus lentement en double), capteur (non-uniformité, ombrage, obscurité, pixels chauds, photons, saturation), pannes d'adressage, pixels morts ou collés, bruits de lecture, de ligne, colonnes, parasite, convertisseur (marches, courbure, bit collé, bits inversés), glissement de bits, lignes qui glissent, noir, balance des blancs, dématriçage (3 méthodes), matrice. Les petites tables (colonnes, lignes, adresses, seuils) sont préparées par le processeur avec les mêmes tirages.
- **Comparaison** `tests/compare_gpu.py` (32 sur 32) : **image identique au processeur, écart 0**, sans bruit comme avec tout le bruit et toutes les pannes, 4 appareils, matrice ou courbes, 3 dématriçages, décalage dans la grille. Vitesse 1920 × 1080 : 40 ms (transferts compris) contre 150 ms sur le processeur.
- **Effet AE :** réglage « Calcul » (Carte graphique NVIDIA si disponible / Processeur, identifiant disque 209) ; « À propos » affiche la carte utilisée et le binaire chargé.
- **Encore sur le processeur :** CCD et caméscope, mode vidéo des appareils photo, vraie grille (redimensionnements), étage vidéo, mesure du mouvement (DIS), signal analogique.

**Carte graphique, deuxième étape (2026-11-18) : mesure du mouvement.** Mesure dans After Effects par l'utilisateur : 6,5 s par image en 1920 × 1920 avec l'obturateur déroulant, le mouvement et l'analogique étant encore sur le processeur.
- `native/gpu/gpu_flow.cpp` + noyaux dans `kernels.cu` : DIS complet sur la carte (luminance 8 bits avec le centile exact par histogrammes de 16 bits, pyramide avec les tables de `cv::resize`, Sobel, recherche par blocs, densification, affinage variationnel, retour à pleine résolution) et obturateur déroulant (`k_rolling`). La recherche par blocs suit les mêmes dépendances que le processeur (bandes de 16, chaque bloc attend son voisin de gauche et du haut) en traitant les blocs par diagonales : **résultat identique au bit près** au processeur.
- Branchement : `estimate_flow` et `rolling_shutter` prennent `use_gpu` (repli automatique sur le processeur) ; réglage « Calcul » de l'effet.
- Comparaison `tests/compare_gpu_flow.py` (12 sur 12) : flux, objet qui bouge, obturateur, scintillement, seconde trame du caméscope, tous à écart nul. Vitesse 1920 × 1080 : 61 ms par mesure (processeur 250 ms) ; caméra + obturateur 129 ms (processeur 644 ms). Limite : la recherche par blocs reste séquentielle le long d'une ligne de blocs (environ 37 ms sur les 61).
- Total des comparaisons : 115 sur 115.

**Journal des temps et corrections (2026-11-18).** Malgré la carte graphique, l'utilisateur mesurait encore 4 à 7 s par image (1920 × 1920, 16 bits, Sony a7S II sur la vraie grille, obturateur déroulant, CameraSim Analogique au-dessus).
- Journal : les deux effets écrivent le temps de chaque étape dans `%TEMP%\CameraSim_temps.txt` (`core/log.h/.cpp`, `camsim::Timer`), et la raison de chaque échec de la carte (mémoire refusée, lancement refusé, erreur de calcul).
- Ce que le journal a montré : (1) l'obturateur déroulant retombait sur le processeur (3 à 4 s) : sur la vraie grille (environ 2830 × 2830), la recherche par blocs demandait 960 fils par groupe et la carte refusait le lancement → limité à 256 fils (8 groupes de 32), même résultat ; (2) le passage sur la vraie grille et le retour à la taille de l'image se faisaient sur le processeur (0,45 s) → redimensionnements sur la carte (`k_resize_hc/vc`, tables de `cv::resize`, image identique) ; (3) les conversions de couleur des effets étaient sur un seul cœur → `ae/ae_io.h` (parallèle, tables exactes 8/16 bits).
- Résultat hors After Effects dans le cas de l'utilisateur : 3,0 s → 0,52 s par image (sans l'analogique, environ 0,4 s sur le processeur, prochain à porter).

**Signal analogique accéléré (2026-11-18).** Profil à 1920 × 1920 (VHS + composite PAL) : 553 ms, dont 278 ms de conversions de couleur sur un seul cœur, 128 ms de bande, 81 ms de composite. Corrections (sur le processeur, sans carte graphique) :
- conversions d'entrée et de sortie, décalages de lignes et modulation en parallèle ;
- grands bruits (bande, couleur, commutation des têtes, liaison) tirés par un hasard à compteur (une valeur par échantillon, par image, par copie et par usage), donc en parallèle ; les petits événements (instabilité des lignes, pertes, calage) gardent le tirage séquentiel. Le hasard natif n'était déjà pas celui de Python : les comparaisons restent statistiques ;
- filtres en fréquence : deux lignes réelles par transformée (filtre réel et symétrique), deux fois moins de calcul.
- Résultat : 214 ms à 1920 × 1920, 168 ms en 1080p (contre 450). Comparaison `compare_native_post.py` 6 sur 6 ; le test des pertes de bande moyenne désormais 8 images (une seule image, avec une dizaine de pertes tirées au hasard, donnait un écart de pur hasard).
- Le passage sur la carte graphique ne ferait plus gagner qu'environ 0,15 s : laissé de côté tant que d'autres étapes coûtent plus (rendu des calques sous l'effet par After Effects).

**Signal analogique sur la carte graphique (2026-11-18, CameraSim Analogique 1.1).** Demande de l'utilisateur malgré le gain modeste annoncé.
- `core/analog_plan.h` : les tirages séquentiels d'un passage sur bande (base de temps, haut qui penche, commutation des têtes, pertes, calage) sont faits d'abord par `plan_tape()`, dans le même ordre ; le processeur applique ce plan (image identique à avant, vérifié sur 3 cas : écart 0) et la carte aussi : mêmes défauts.
- `gpu/gpu_analog.cpp` + noyaux `k_an_*` : toute la chaîne (conversion BT.709, réduction à la trame, Y'UV, bande, composite, décodage, retour). Les filtres en fréquence deviennent des convolutions circulaires par ligne (réponse impulsionnelle calculée par le processeur, somme compensée en simple précision, une ligne par groupe de fils en mémoire partagée).
- Comparaison `tests/compare_gpu_analog.py` (8 sur 8) : écart maximal 3·10⁻⁸ (PAL, NTSC, peigne, liaison séparée, bandes, pertes, calage, copies, accentuation, anamorphose). Vitesse 1920 × 1920 : 35 ms contre 224 ms.
- Effet : réglage « Calcul » ajouté en fin de liste (identifiant neuf), version 1.1.

**Pas encore dans l'effet** (demandent toute la séquence ou une bibliothèque de codecs) : caméras à tube et rémanence, automatismes, déchirure d'image, enregistrement (DV, MJPEG, MPEG-2, H.264). Ces éléments restent dans le moteur Python (panneau). Voir la question ouverte sur le calcul de séquence (section 11).

## 11. Questions encore ouvertes
- Séquence de test : (rendu 3D prive, non fourni), EXR demi-flottant RVBA, ACEScg (`lin_ap1_scene`), 1920 × 1920. Dossier en lecture seule pour l'outil : ne jamais y écrire.
- L'utilisateur a-t-il une vraie caméra à calibrer (proposition 5.1) ?
- Ordre de réalisation des étages et des familles de caméras.
- **Effet natif AE et enregistrement** : pour les formats à groupes d'images et tout ce qui a une mémoire (automatismes, rémanence), approximation en direct + calcul de séquence par un bouton, ou calcul en arrière-plan avec cache ? (voir « Intégration de l'enregistrement dans After Effects », section 12).
