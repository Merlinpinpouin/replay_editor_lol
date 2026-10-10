"""Rang des joueurs d'un replay (pour l'encadré de présentation), par ordre de priorité :
1. choisi à la main dans l'interface ;
2. le client LoL ouvert (API locale du client, fichier lockfile : aucune clé à fournir) ;
3. l'API Riot, si une clé a été enregistrée dans l'interface (settings.json).
Rang solo/duo en priorité (sinon flex), mis en cache 12 h par PUUID dans projects/ranks.json."""
import base64, json, os, ssl, time, urllib.parse, urllib.request
from . import clients
from .paths import PROJECTS, ROOT

CACHE = os.path.join(PROJECTS, "ranks.json")
SETTINGS = os.path.join(ROOT, "settings.json")
TIERS = ("IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD", "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER")
TTL = 12 * 3600
_CTX = ssl._create_unverified_context()
REGIONS = {"EUW1": "EUW", "EUN1": "EUNE", "NA1": "NA", "KR": "KR", "JP1": "JP", "BR1": "BR", "LA1": "LAN",
           "LA2": "LAS", "OC1": "OCE", "TR1": "TR", "RU": "RU", "ME1": "ME", "SG2": "SG", "TW2": "TW", "VN2": "VN"}


def region(platform):
    return REGIONS.get((platform or "").upper(), (platform or "").upper().rstrip("0123456789"))


def settings():
    try:
        return json.load(open(SETTINGS, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(**kw):
    s = settings()
    s.update(kw)
    json.dump(s, open(SETTINGS, "w", encoding="utf-8"), indent=1)


def _cache():
    try:
        return json.load(open(CACHE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(c):
    os.makedirs(PROJECTS, exist_ok=True)
    json.dump(c, open(CACHE, "w", encoding="utf-8"), indent=1, ensure_ascii=False)


def _pick(entries):
    """[(file, tier, division)] -> {"tier", "division"} : solo/duo, sinon flex ; None = pas classé."""
    by = {q: (t, d) for q, t, d in entries if t and t.upper() in TIERS}
    for q in ("RANKED_SOLO_5x5", "RANKED_FLEX_SR"):
        if q in by:
            t, d = by[q]
            return {"tier": t.upper(), "division": "" if t.upper() in TIERS[7:] else (d or "")}
    return None


def _lcu(puuid):
    """API locale du client LoL (ouvert) : rang de n'importe quel joueur à partir de son PUUID."""
    for d in [clients.main_install(), r"C:\Riot Games\League of Legends"]:
        f = os.path.join(d or "", "lockfile")
        if not d or not os.path.isfile(f):
            continue
        try:
            _, _, port, pw, proto = open(f).read().strip().split(":")
            auth = base64.b64encode(f"riot:{pw}".encode()).decode()
            req = urllib.request.Request(f"{proto}://127.0.0.1:{port}/lol-ranked/v1/ranked-stats/{puuid}",
                                         headers={"Authorization": "Basic " + auth})
            with urllib.request.urlopen(req, context=_CTX, timeout=4) as r:
                q = json.loads(r.read()).get("queueMap", {})
            return True, _pick([(k, v.get("tier"), v.get("division")) for k, v in q.items()])
        except Exception:
            continue
    return False, None


def _riot(puuid, platform):
    key = settings().get("riot_api_key")
    if not key or not platform:
        return False, None
    url = (f"https://{platform.lower()}.api.riotgames.com/lol/league/v4/entries/by-puuid/"
           f"{urllib.parse.quote(puuid)}")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"X-Riot-Token": key}), timeout=6) as r:
            return True, _pick([(e.get("queueType"), e.get("tier"), e.get("rank")) for e in json.loads(r.read())])
    except Exception:
        return False, None


def lookup(players, platform):
    """{puuid: {"tier", "division", "source"} | None} pour les joueurs (avec "puuid") d'un replay."""
    cache, out, dirty = _cache(), {}, False
    for p in players:
        pu = p.get("puuid")
        if not pu:
            continue
        e = cache.get(pu) or {}
        if e.get("manual"):
            out[pu] = dict(e["manual"], source="manuel")
            continue
        if e.get("t", 0) > time.time() - TTL and "rank" in e:
            out[pu] = e["rank"] and dict(e["rank"], source=e.get("source"))
            continue
        for src, fn in (("client", lambda: _lcu(pu)), ("api", lambda: _riot(pu, platform))):
            ok, r = fn()
            if ok:
                cache[pu] = {**e, "rank": r, "source": src, "t": time.time()}
                out[pu] = r and dict(r, source=src)
                dirty = True
                break
        else:
            out[pu] = e.get("rank") and dict(e["rank"], source="ancien")  # dernier rang connu
    if dirty:
        _save(cache)
    return out


def set_manual(puuid, tier, division=""):
    """Rang choisi à la main (tier vide : revenir à la recherche automatique)."""
    cache = _cache()
    e = cache.setdefault(puuid, {})
    if tier and tier.upper() in TIERS:
        e["manual"] = {"tier": tier.upper(), "division": "" if tier.upper() in TIERS[7:] else division}
    else:
        e.pop("manual", None)
        e.pop("t", None)
    _save(cache)
