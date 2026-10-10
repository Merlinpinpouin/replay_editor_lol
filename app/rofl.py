"""Lecture des métadonnées d'un replay .rofl (format ROFL2, patchs 14.x+)."""
import json, struct

MAGIC = b"RIOT"


def read(path):
    with open(path, "rb") as f:
        head = f.read(64)
        if head[:4] != MAGIC:
            raise ValueError("pas un fichier .rofl")
        n = head[14]
        version = head[15:15 + n].decode("ascii")
        f.seek(-4, 2)
        ml = struct.unpack("<I", f.read(4))[0]
        f.seek(-4 - ml, 2)
        meta = json.loads(f.read(ml))
    stats = meta.get("statsJson")
    if isinstance(stats, str):
        stats = json.loads(stats)
    players = [{
        "champion": p.get("SKIN"),
        "name": p.get("RIOT_ID_GAME_NAME") or p.get("NAME"),
        "puuid": p.get("PUUID"),
        "team": int(p.get("TEAM", 0)),
        "position": p.get("TEAM_POSITION"),
        "kills": int(p.get("CHAMPIONS_KILLED", 0)),
        "deaths": int(p.get("NUM_DEATHS", 0)),
        "assists": int(p.get("ASSISTS", 0)),
        "multikill": int(p.get("LARGEST_MULTI_KILL", 0)),
        "win": p.get("WIN") == "Win",
    } for p in stats or []]
    return {
        "version": version,
        "patch": ".".join(version.split(".")[:2]),
        "duration": meta.get("gameLength", 0) / 1000.0,
        "players": players,
    }
