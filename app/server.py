"""Serveur local de l'appli : http://127.0.0.1:8765"""
import json, mimetypes, os, re, threading, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, unquote, urlparse
from . import clients, game, lcu, music, pipeline, ranks, rofl
from .paths import DATA, REPLAYS, RENDERS

STATIC = os.path.join(os.path.dirname(__file__), "static")
PORT = int(os.environ.get("LOLCLIP_PORT") or 8765)  # (autre port : tests, l'appli de bureau utilise 8765)
CURRENT = {}


def info_for(path):
    i = rofl.read(path)
    i["path"] = path
    i["file"] = os.path.basename(path)
    i["installed"] = clients.installed(i["patch"])
    i["main"] = clients.is_main(i["patch"])
    i["free_gb"] = round(clients.free_gb(), 1)
    i["platform"] = i["file"].split("-")[0] if "-" in i["file"] else ""
    i["region"] = ranks.region(i["platform"])
    _ranks(i)
    return i


def _ranks(i):
    """Rang de chaque joueur (encadré de présentation) : client LoL ouvert, clé API, ou choisi à la main."""
    try:
        r = ranks.lookup(i["players"], i["platform"])
    except Exception:
        r = {}
    for p in i["players"]:
        p["rank"] = r.get(p.get("puuid"))


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        import sys
        if not isinstance(sys.exc_info()[1], (ConnectionError, OSError)):  # navigateur qui coupe : rien à signaler
            super().handle_error(request, client_address)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _file(self, path, ctype=None):
        """Fichier, avec prise en charge des requêtes partielles (Range) : le lecteur vidéo du navigateur en a besoin
        pour avancer dans la vidéo, et coupe ses connexions en cours de route (ce n'est pas une erreur)."""
        if not os.path.isfile(path):
            return self._json({"error": "not found"}, 404)
        size = os.path.getsize(path)
        start, end = 0, size - 1
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype or mimetypes.guess_type(path)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        try:
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0 and (chunk := f.read(min(1 << 20, left))):
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (ConnectionError, OSError):
            pass

    def _safe_error(self, e):
        try:
            return self._json({"error": str(e)}, 500)
        except (ConnectionError, OSError):
            return None

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path == "/":
                return self._file(os.path.join(STATIC, "index.html"), "text/html; charset=utf-8")
            if u.path == "/api/replays":
                files = sorted((f for f in os.listdir(REPLAYS) if f.endswith(".rofl")),
                               key=lambda f: -os.path.getmtime(os.path.join(REPLAYS, f)))
                return self._json(files[:30])
            if u.path == "/api/status":
                cur = CURRENT.get("info")
                job = clients.job(cur["patch"]) if cur else None
                if cur:
                    cur["installed"] = clients.installed(cur["patch"])
                    cur["main"] = clients.is_main(cur["patch"])
                return self._json({"current": cur, "install": job, "game": game.running(), "pipeline": pipeline.STATE,
                                   "launch": game.LAUNCH})
            if u.path == "/api/music":
                return self._json(music.tracks())
            if u.path == "/api/settings":
                return self._json({"has_key": bool(ranks.settings().get("riot_api_key")), "data": DATA,
                                   "renders": RENDERS})
            if u.path == "/api/music/info":  # drop du morceau (détecté au 1er appel, ~1-2 s)
                path = music.path_for(parse_qs(u.query).get("name", [""])[0])
                return self._json(music.info(path)) if path else self._json({"error": "morceau introuvable"}, 404)
            if u.path.startswith("/music/"):  # écoute dans l'interface
                path = music.path_for(unquote(u.path[len("/music/"):]))
                return self._file(path) if path else self._json({"error": "not found"}, 404)
            if u.path.startswith("/renders/"):
                return self._file(os.path.join(RENDERS, os.path.basename(unquote(u.path))))
            return self._json({"error": "not found"}, 404)
        except (ConnectionError, OSError) as e:
            if isinstance(e, (ConnectionError, BrokenPipeError)):
                return None
            return self._safe_error(e)
        except Exception as e:
            return self._safe_error(e)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            if u.path == "/api/rofl":
                name = os.path.basename(unquote(self.headers.get("X-Filename", "replay.rofl")))
                data = self._body()
                path = os.path.join(REPLAYS, name)
                if not os.path.exists(path) or os.path.getsize(path) != len(data):
                    os.makedirs(REPLAYS, exist_ok=True)
                    open(path, "wb").write(data)
                CURRENT["info"] = info_for(path)
                pipeline.load_actions(CURRENT["info"])
                return self._json(CURRENT["info"])
            if u.path == "/api/music/upload":
                name = music.add(unquote(self.headers.get("X-Filename", "")), self._body())
                return self._json({"name": name, "tracks": music.tracks(), **music.info(music.path_for(name))})
            if u.path == "/api/music/drop":  # correction manuelle du drop (t = null : revenir au drop détecté)
                body = json.loads(self._body() or b"{}")
                path = music.path_for(body.get("name"))
                if not path:
                    return self._json({"error": "morceau introuvable"}, 404)
                return self._json(music.set_manual(path, body.get("t")))
            if u.path == "/api/rank":  # rang choisi à la main (tier vide : recherche automatique)
                body = json.loads(self._body() or b"{}")
                ranks.set_manual(body["puuid"], body.get("tier") or "", body.get("division") or "")
                _ranks(CURRENT["info"])
                return self._json(CURRENT["info"])
            if u.path == "/api/settings":
                body = json.loads(self._body() or b"{}")
                if "riot_api_key" in body:
                    ranks.save_settings(riot_api_key=(body["riot_api_key"] or "").strip())
                if CURRENT.get("info"):
                    _ranks(CURRENT["info"])
                return self._json({"info": CURRENT.get("info"), "has_key": bool(ranks.settings().get("riot_api_key"))})
            if u.path == "/api/select":
                CURRENT["info"] = info_for(os.path.join(REPLAYS, os.path.basename(json.loads(self._body())["file"])))
                pipeline.load_actions(CURRENT["info"])
                return self._json(CURRENT["info"])
            if u.path == "/api/analyze":
                return self._json({"ok": pipeline.analyze_async(CURRENT["info"])})
            if u.path == "/api/open_renders":  # dossier des vidéos dans l'explorateur
                os.makedirs(RENDERS, exist_ok=True)
                os.startfile(RENDERS)
                return self._json({"ok": True})
            if u.path == "/api/close":
                return self._json({"ok": game.close_replay()})
            if u.path == "/api/install":
                return self._json(clients.install(CURRENT["info"]["version"]))
            if u.path == "/api/launch":
                i = CURRENT["info"]
                return self._json({"ok": True, "mode": game.launch(i["path"], i["patch"])})
            if u.path == "/api/make":
                body = json.loads(self._body() or b"{}")
                num = lambda k: float(body[k]) if body.get(k) not in (None, "") else None
                ok = pipeline.run_async(CURRENT["info"], n=int(body.get("n") or 3), focus=body.get("focus") or None,
                                        ids=body.get("ids") or None, mode=body.get("mode") or "cinema",
                                        follow=body.get("follow") or None, pre=num("pre"), post=num("post"),
                                        clips=body.get("clips") or None, close=body.get("close", True),
                                        loudness=num("loudness"), fmt=body.get("fmt") or "cinema",
                                        grade=body.get("grade", True), combine=body.get("combine", False),
                                        zoom=num("zoom") or 1.0, music=body.get("music") or None,
                                        follows=body.get("follows") or None, fog=num("fog"),
                                        banner=body.get("banner", True))
                return self._json({"ok": ok})
            return self._json({"error": "not found"}, 404)
        except (ConnectionError, OSError) as e:
            if isinstance(e, (ConnectionError, BrokenPipeError)):
                return None
            return self._safe_error(e)
        except Exception as e:
            return self._safe_error(e)


def main(open_browser=True):
    # réglages du jeu principal encore modifiés (appli fermée pendant un replay) : remis maintenant, ou à la
    # fermeture du jeu s'il tourne encore
    if not clients.restore_config() and os.path.isdir(clients.BACKUP):
        threading.Thread(target=clients._restore_when_closed, args=(0,), daemon=True).start()
    # réglages changés par le client LoL pendant un replay (appli fermée avant la fin) : remis si possible
    threading.Thread(target=lcu.restore_settings, daemon=True).start()
    srv = Server(("127.0.0.1", PORT), H)
    print(f"lolclip : http://127.0.0.1:{PORT}", flush=True)
    if open_browser and not os.environ.get("LOLCLIP_NOBROWSER"):
        threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
