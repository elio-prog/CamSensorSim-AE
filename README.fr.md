# CamSensorSim-AE

**Un simulateur physique de caméra pour Adobe After Effects.** On lui donne une image propre (rendu 3D,
composite, prise de vue) et il la fait passer par une caméra simulée : photons, capteur, électronique,
convertisseur, traitement, étage vidéo, et si on veut un signal analogique PAL/NTSC et une bande. Les défauts
sont le but : bruit, motifs fixes, pixels chauds ou morts, traînée et éblouissement des CCD, obturateur
déroulant, entrelacement, pannes d'adressage, défauts du convertisseur, pertes de bande VHS, mauvais calage,
défauts du composite, et beaucoup d'autres.

[English version](README.md)

> [!WARNING]
> **Ce projet a été « vibe codé ».** Presque tout le code a été écrit par un assistant d'IA (Claude,
> d'Anthropic) en conversation avec l'auteur, qui a dirigé le travail, testé dans After Effects et pris les
> décisions. Les modèles reposent sur des recherches publiées et des données mesurées, et le module natif est
> vérifié contre le moteur Python de référence par des tests automatiques, mais le code **n'a pas été relu par
> un développeur expérimenté**. À utiliser à vos risques ; gardez des copies de vos projets.

## Installer le module (le plus simple)

1. Téléchargez ce dépôt : bouton vert **Code** > **Download ZIP**, puis décompressez-le.
2. Fermez After Effects.
3. Ouvrez le dossier `plugin` et double-cliquez sur **`Install.bat`**. Acceptez la demande d'administrateur de
   Windows (les modules vont dans `C:\Program Files\Adobe\...`).
4. Lancez After Effects : les effets sont dans **Effet > CameraSim**.

L'installateur trouve toutes les versions d'After Effects installées et copie les deux modules dans leur dossier
`Support Files\Plug-ins\CameraSim`. Pour les retirer : **`Uninstall.bat`**.

## Configuration

- Windows 10 ou 11, 64 bits ; After Effects (développé et testé avec After Effects 2025).
- Facultatif : une carte **NVIDIA** (GeForce GTX 900 ou plus récente) avec un pilote 528 ou plus récent. Le
  calcul se fait alors sur la carte, 4 à 6 fois plus vite. Sans carte NVIDIA, il se fait sur le processeur,
  avec la même image.

## Démarrage rapide

1. Appliquez **CameraSim** à votre image (ou à un calque d'effets au-dessus).
2. Réglez **Espace de travail** selon votre projet (ACEScg linéaire, sRGB ou Rec.709 linéaire) ; en 32 bits de
   préférence.
3. Choisissez une **Caméra**, un **ISO** et une **Exposition** : tout le reste part de valeurs mesurées.
4. Pour un aspect PAL / NTSC / VHS, ajoutez **CameraSim Analogique** sous CameraSim.

Le document de conception complet (physique, sources, décisions, mesures) est dans `docs/CAHIER_DES_CHARGES.md`.
Le reste de la documentation (construction, moteur Python, sources, licences) est dans le [README anglais](README.md).
