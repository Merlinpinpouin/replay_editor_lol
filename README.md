# lolclip — éditeur de replays League of Legends

Transforme un replay `.rofl` en clip monté automatiquement : repérage des champions, caméra cinématique ou vue du
dessus, transitions « traversée du brouillard », encadré du joueur (rang, pseudo, champion), étalonnage, musique
calée sur le drop.

## Utilisation

1. Lancer `lolclip.bat` (ouvre l'interface dans le navigateur).
2. Choisir le replay `.rofl` (glisser-déposer).
3. **Lancer le replay**, **Analyser les actions**, choisir les actions ou des passages sur la timeline, régler
   le style, puis **Générer**. Les vidéos arrivent dans le dossier `renders/`.

Le jeu doit tourner pendant la génération : c'est lui qui calcule les images (aucun moteur de rendu de LoL ne
fonctionne sans le jeu). Si le client LoL est ouvert, c'est lui qui lance le replay ; sinon le jeu est lancé
directement (autorisation administrateur : Vanguard est coupé quelques secondes).

## Prérequis

- Windows 10/11, League of Legends installé
- Python 3.12 + `numpy`, `pillow`
- `ffmpeg` / `ffprobe` dans le PATH
- `tools/ManifestDownloader.exe` (pour lire les replays d'anciens patchs) :
  https://github.com/Morilli/ManifestDownloader/releases

## Organisation du dépôt

| Dossier | Contenu |
| --- | --- |
| `app/` | moteur Python (serveur local, caméra, capture, montage) et interface web (`app/static`) |
| `assets/` | étalonnage (LUT) et vignette |
| `tools/` | scripts Windows (lancement du replay, son du jeu) |

## Git

- `main` = production : on n'y commite pas directement, tout arrive par une branche fusionnée.
- Une branche par fonctionnalité : `feature/<nom>`, ou `fix/<nom>` pour une correction.
- Les versions publiées sont des tags `vX.Y.Z` sur `main`.
