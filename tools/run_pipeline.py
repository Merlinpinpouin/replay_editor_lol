"""Génère automatiquement les N meilleurs clips d'un replay (le replay doit être ouvert, jeu au premier plan).
Usage : python tools/run_pipeline.py <fichier.rofl> [n] [joueur_focus]"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import pipeline, rofl
from app.paths import REPLAYS
path = os.path.join(REPLAYS, sys.argv[1])
info = rofl.read(path); info["path"] = path
pipeline.run_async(info, n=int(sys.argv[2]) if len(sys.argv) > 2 else 3, focus=sys.argv[3] if len(sys.argv) > 3 else None)
while pipeline.busy() or pipeline.STATE["step"] == "idle":
    time.sleep(1)
print("\n".join(pipeline.STATE["results"]) or pipeline.STATE["error"])
