# lolclip : éditeur de replays League of Legends

> **Le développement continue dans [Server_web_symfo](https://github.com/Merlinpinpouin/Server_web_symfo), dossier `desktop/engine/`.**
> Ce dépôt reste utilisable en l'état jusqu'à son archivage.

Transforme un replay `.rofl` en clip monté automatiquement : repérage des champions, caméra cinématique ou vue du
dessus, transitions « traversée du brouillard », encadré du joueur (rang, pseudo, champion), étalonnage, musique
calée sur le drop.

## Utilisation

1. Installer puis lancer **lolclip** (installeur `lolclip_<version>_x64-setup.exe` des
   [releases](https://github.com/Merlinpinpouin/replay_editor_lol/releases)). Depuis les sources : `lolclip.bat`
   (interface dans le navigateur) ou `cd desktop && npm run dev` (fenêtre de l'application).
2. Choisir le replay `.rofl` (glisser-déposer).
3. **Lancer le replay**, **Analyser les actions**, choisir les actions ou des passages sur la timeline, régler
   le style, puis **Générer**. Les vidéos arrivent dans le dossier `renders/`.

Le jeu doit tourner pendant la génération : c'est lui qui calcule les images (aucun moteur de rendu de LoL ne
fonctionne sans le jeu). Si le client LoL est ouvert, c'est lui qui lance le replay ; sinon le jeu est lancé
directement (autorisation administrateur : Vanguard est coupé quelques secondes).

## Données

Les anciens clients LoL, analyses, vidéos, musiques et réglages vont dans `%LOCALAPPDATA%\lolclip` (appli installée)
ou dans le dossier du projet (sources). Pour utiliser un autre dossier (par exemple un disque avec de la place pour
les anciens clients, ~30 Go chacun) : `%LOCALAPPDATA%\lolclip\config.json` → `{"data_dir": "D:/lolclip"}`.

## Prérequis (sources)

- Windows 10/11, League of Legends installé
- Python 3.12 + `pip install -r requirements.txt`
- `ffmpeg` / `ffprobe` dans le PATH
- `tools/ManifestDownloader.exe` (pour lire les replays d'anciens patchs) :
  https://github.com/Morilli/ManifestDownloader/releases

## Organisation du dépôt

| Dossier | Contenu |
| --- | --- |
| `app/` | moteur Python (serveur local, caméra, capture, montage) et interface web (`app/static`) |
| `assets/` | étalonnage (LUT) et vignette |
| `tools/` | scripts Windows (lancement du replay, son du jeu) |
| `desktop/` | application de bureau (Tauri, Rust) : lance le moteur et affiche son interface |
| `packaging/` | empaquetage du moteur en exécutable (PyInstaller) |
| `scripts/build.ps1` | construit l'installeur (moteur + ffmpeg + application) |

## Git

- `main` = production : on n'y commite pas directement, tout arrive par une branche fusionnée.
- Une branche par fonctionnalité : `feature/<nom>`, ou `fix/<nom>` pour une correction.
- Les versions publiées sont des tags `vX.Y.Z` sur `main` (même numéro que `desktop/src-tauri/tauri.conf.json`) :
  pousser le tag construit l'installeur (GitHub Actions, `.github/workflows/release.yml`) et publie la release.

## Construire l'installeur

Prérequis : Python 3.12, Node 20+, Rust (toolchain MSVC + Visual Studio Build Tools C++).

```powershell
./scripts/build.ps1
# -> desktop/src-tauri/target/release/bundle/nsis/lolclip_<version>_x64-setup.exe
```
