"""Serveur HTTP local, API et ressources web."""
import sys, json, re, threading
import urllib.parse
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from . import config, device, discovery, playback, streaming, watchdog


# Table fermée : aucun chemin fourni par le client n’est ouvert sur disque.
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
WEB_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def log_message(self, *a):  # quieter
        sys.stderr.write("[proxy] " + (a[0] % a[1:]) + "\n")
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        if self.path not in ("/api/speakers", "/api/presets", "/api/stations",
                             "/api/speakers/discover", "/api/speakers/identify"):
            return self._json({"ok": False, "error": "Route inconnue."}, 404)
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self._json({"ok": False, "error": "JSON requis."}, 415)
        # Reject cross-origin browser writes to this local configuration endpoint.
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlsplit(origin).netloc != self.headers.get("Host"):
            return self._json({"ok": False, "error": "Origine refusée."}, 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 65536: raise ValueError("Taille de requête invalide.")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict): raise ValueError("Objet JSON requis.")
            if self.path == "/api/speakers/discover":
                try: found = discovery.scan_speakers()
                except OSError:
                    raise ValueError("Recherche réseau impossible. Vérifiez le réseau du serveur ou renseignez une IP.") from None
                return self._json({"ok": True, "speakers": found})
            if self.path == "/api/speakers/identify":
                return self._json({"ok": True, "speaker": discovery.speaker_info(payload.get("ip"))})
            key = self.path.rsplit("/", 1)[1]
            if key == "speakers":
                with config._CONFIG_LOCK:
                    config.configure_speakers(payload.get(key), payload.get("revision"))
                    return self._json({"ok": True, **config.speaker_snapshot()})
            configure = {"presets": config.configure_presets, "stations": config.configure_station}[key]
            result = configure(payload if key == "stations" else payload.get(key))
            return self._json({"ok": True, key: result})
        except (ValueError, UnicodeError) as e:
            return self._json({"ok": False, "error": str(e)}, 400)
        except OSError:
            return self._json({"ok": False, "error": "Écriture impossible. Vérifier les droits du dossier de configuration."}, 500)

    def do_GET(self):
        cfg = config.get_config()
        path = self.path.split("?", 1)[0]
        qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        # ---- phone control UI + API ----
        if path in WEB_FILES:
            filename, content_type = WEB_FILES[path]
            body = (WEB_DIR / filename).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        if path == "/api/status":
            return self._json(playback.api_status(cfg))
        if path == "/api/speakers":
            with config._CONFIG_LOCK:
                return self._json(config.speaker_snapshot())
        if path == "/api/stations":
            with config._CONFIG_LOCK:
                return self._json(config.station_snapshot())
        if path == "/api/presets":
            with config._CONFIG_LOCK:
                return self._json({"presets": config.load_json(config.PRESETS_PATH, {}),
                                   "speakers": config.load_json(config.SPEAKERS_PATH, {}),
                                   "stations": [{"id": k, "note": v.get("note", "") if isinstance(v, dict) else ""}
                                                for k, v in config.load_json(config.STATIONS_PATH, {}).items()]})
        m = re.match(r"/api/play/(.+)$", path)
        if m:
            station = urllib.parse.unquote(m.group(1))
            try:
                vol = int(qs["volume"][0]) if qs.get("volume") else None
                master = playback.api_play(cfg, station, vol, qs.get("speaker", [None])[0])
                return self._json({"ok": True, "station": station, "master": master})
            except (SystemExit, ValueError) as e:  # invalid request
                return self._json({"ok": False, "error": str(e)}, 400)
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 500)
        m = re.match(r"/api/volume/(\d+)$", path)
        if m:
            vol = int(m.group(1))
            one_ip = qs.get("ip", [None])[0]       # ?ip=x.x.x.x -> just that speaker
            for d in config.load_json(config.SPEAKERS_PATH, {}).values():
                if one_ip is None or d.get("ip") == one_ip:
                    device.set_volume(d["ip"], vol)
            return self._json({"ok": True, "volume": vol, "ip": one_ip})
        if path == "/api/stop":
            try:
                target = qs.get("speaker", [None])[0]
                if target and target != "all":
                    playback.api_stop_target(target)
                else:
                    playback.api_stop_all()
                return self._json({"ok": True})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 500)
        # ---- stream proxy ----
        m = re.match(r"/stream/(.+)$", path)
        if not m:
            self.send_response(404); self.end_headers(); return
        station_id = urllib.parse.unquote(m.group(1))
        streaming.stream_audio(self, station_id)


def serve(cfg):
    port = cfg["proxy_port"]
    host = playback.resolve_proxy_host(cfg)
    srv = ThreadingHTTPServer(("0.0.0.0", port), ProxyHandler)
    if cfg.get("watchdog", True):
        threading.Thread(target=watchdog.watchdog, args=(cfg,), daemon=True).start()
    print(f"Interface web : http://{host}:{port}/  (Ctrl-C pour arrêter)")
    print("Keep this running while you want radio to play.")
    try: srv.serve_forever()
    except KeyboardInterrupt: print("\nbye")
