#!/usr/bin/env python3
"""
bose_radio.py — Local internet-radio control for Bose SoundTouch 10 speakers
after the Bose cloud shutdown (May 2026).

Why this exists
---------------
The SoundTouch cloud is dead, so:
  * TUNEIN presets               -> UNKNOWN_SOURCE_ERROR (cloud gone)
  * LOCAL_INTERNET_RADIO select  -> rejected by ST-10 firmware (error 1005)
The one path that still works on the ST-10 is its built-in UPnP/DLNA renderer
(AVTransport on port 8091). We push a stream URL to it and tell it to Play.

Old firmware is picky about HTTPS + redirects + ICY responses, so this script
also runs a tiny local PROXY: the speaker pulls plain HTTP from us, and we deal
with the messy upstream (HTTPS, 302 redirects, SHOUTcast "ICY 200 OK", and live
TuneIn token refresh). The proxy must stay running while radio is playing.

Usage
-----
  ./bose_radio.py discover                 # find speakers on the LAN (mDNS)
  ./bose_radio.py list                     # show speakers + stations
  ./bose_radio.py serve                    # run the proxy (keep running!)
  ./bose_radio.py play <station> <speaker> [--volume N]
  ./bose_radio.py stop <speaker>
  ./bose_radio.py status <speaker>

`serve` is the long-lived process. `play` / `stop` are quick UPnP commands.
Speaker + station names are matched case-insensitively on a substring.

Relocating to an always-on host (Pi / NAS):
  copy this folder there, set proxy_host in config/config.json to that host's LAN IP
  (reachable by the speakers), run `serve` under systemd/launchd, done.
"""
import sys, os, json, socket, ssl, re, time, threading, subprocess, argparse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import xml.etree.ElementTree as ET
import urllib.parse
import ipaddress
import tempfile
import hashlib
import uuid

# Windows consoles default to cp1252 which can't print ▶/■ — force UTF-8.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try: _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception: pass

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.abspath(os.environ.get("BOSE_CONFIG_DIR", os.path.join(HERE, "config")))
DATA_DIR = os.path.abspath(os.environ.get("BOSE_DATA_DIR", os.path.join(HERE, "data")))
CONFIG_PATH   = os.path.join(CONFIG_DIR, "config.json")
STATIONS_PATH = os.path.join(CONFIG_DIR, "stations.json")
SPEAKERS_PATH = os.path.join(CONFIG_DIR, "speakers.json")
PRESETS_PATH  = os.path.join(CONFIG_DIR, "presets.json")
STATE_PATH    = os.path.join(DATA_DIR, "state.json")   # last intended playback (for watchdog)

# --- watchdog support: track which speaker IPs currently have an open stream ---
# The master speaker pulls from the proxy directly; members relay off the master
# and never hit the proxy. So "master claims PLAY_STATE/UPNP but has no open
# proxy stream" is the exact signature of the buffer-drain stall we self-heal.
_ACTIVE_LOCK = threading.Lock()
_ACTIVE = {}  # client_ip -> count of open /stream connections

def _active_inc(ip):
    with _ACTIVE_LOCK: _ACTIVE[ip] = _ACTIVE.get(ip, 0) + 1

def _active_dec(ip):
    with _ACTIVE_LOCK:
        n = _ACTIVE.get(ip, 0) - 1
        if n <= 0: _ACTIVE.pop(ip, None)
        else:      _ACTIVE[ip] = n

def _active_has(ip):
    with _ACTIVE_LOCK: return _ACTIVE.get(ip, 0) > 0

# ---------------------------------------------------------------- config -----
def load_json(path, default):
    try:
        with open(path) as f: return json.load(f)
    except Exception:
        return default

def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".config-")
    try:
        with os.fdopen(fd, "w") as f:
            try: previous = os.stat(path)
            except FileNotFoundError: previous = None
            os.chmod(tmp, previous.st_mode & 0o777 if previous else 0o644)
            # Docker runs as root; retain the host user's ownership on replacement.
            if previous and hasattr(os, "fchown") and os.geteuid() == 0:
                os.fchown(f.fileno(), previous.st_uid, previous.st_gid)
            json.dump(obj, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

_CONFIG_LOCK = threading.Lock()

def configure_speakers(rows):
    """Validate the complete list before writing; retain metadata and renamed presets."""
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError("Liste d'enceintes invalide (100 maximum).")
    with _CONFIG_LOCK:
        old = load_json(SPEAKERS_PATH, {})
        result, renames, originals, ips, ids = {}, {}, set(), set(), set()
        for row in rows:
            if not isinstance(row, dict): raise ValueError("Enceinte invalide.")
            fields = [row.get(k, "") for k in ("name", "ip", "deviceID", "model", "original_name")]
            if not all(isinstance(v, str) for v in fields):
                raise ValueError("Les champs doivent être du texte.")
            name, ip, device_id, model, original = [v.strip() for v in fields]
            if not name or len(name) > 80 or name.lower() in ("default", "all"):
                raise ValueError("Nom requis (80 caractères maximum, hors default et all).")
            if name in result: raise ValueError("Chaque enceinte doit avoir un nom unique.")
            try: ip = str(ipaddress.IPv4Address(ip))
            except ValueError: raise ValueError("Adresse IPv4 invalide : " + ip)
            if ip in ips: raise ValueError("Chaque enceinte doit avoir une IP unique.")
            if not re.fullmatch(r"[0-9a-fA-F]{12}", device_id):
                raise ValueError("L'identifiant Bose doit contenir 12 caractères hexadécimaux.")
            device_id = device_id.upper()
            if device_id in ids: raise ValueError("Identifiant Bose utilisé plusieurs fois.")
            if len(model) > 100: raise ValueError("Modèle trop long.")
            if original and (original not in old or original in originals):
                raise ValueError("Configuration modifiée : fermer puis rouvrir les réglages.")
            if original:
                originals.add(original)
                if original != name: renames[original] = name
            result[name] = {**old.get(original, {}), "ip": ip, "deviceID": device_id, "model": model}
            ips.add(ip); ids.add(device_id)
        presets = load_json(PRESETS_PATH, {})
        for source, dest in renames.items():
            if dest in presets and dest not in renames and dest != source:
                raise ValueError("Ce nom possède déjà des presets : " + dest)
        updated_presets = {renames.get(k, k): v for k, v in presets.items()}
        cfg = load_json(CONFIG_PATH, {})
        updated_cfg = dict(cfg)
        if cfg.get("master") in renames: updated_cfg["master"] = renames[cfg["master"]]
        elif cfg.get("master") in old and cfg["master"] not in result: updated_cfg.pop("master")
        changes = []
        if updated_presets != presets: changes.append((PRESETS_PATH, updated_presets, presets))
        if updated_cfg != cfg: changes.append((CONFIG_PATH, updated_cfg, cfg))
        changes.append((SPEAKERS_PATH, result, old))
        written = []
        try:
            for path, value, previous in changes:
                save_json(path, value)
                written.append((path, previous))
        except OSError:
            for path, previous in reversed(written): save_json(path, previous)
            raise
        return result

def configure_presets(changes):
    """Update selected scopes while preserving other speakers' mappings."""
    if not isinstance(changes, dict):
        raise ValueError("Configuration des presets invalide.")
    with _CONFIG_LOCK:
        speakers_config = load_json(SPEAKERS_PATH, {})
        stations = load_json(STATIONS_PATH, {})
        result = dict(load_json(PRESETS_PATH, {}))
        for scope, mapping in changes.items():
            if scope != "default" and scope not in speakers_config:
                raise ValueError("Enceinte inconnue ou renommée : " + scope)
            if not isinstance(mapping, dict):
                raise ValueError("Liste de presets invalide.")
            for number, station in mapping.items():
                if number not in ("1", "2", "3", "4", "5", "6"):
                    raise ValueError("Les presets doivent être numérotés de 1 à 6.")
                if station is None and scope != "default":
                    continue  # Explicitly disable an inherited global preset.
                if not isinstance(station, str) or station not in stations:
                    raise ValueError("Station inconnue pour le preset " + number)
            result[scope] = dict(mapping)
        save_json(PRESETS_PATH, result)
        return result

def station_snapshot():
    """Caller holds the configuration lock; revision protects all related edits."""
    stations = load_json(STATIONS_PATH, {})
    presets = load_json(PRESETS_PATH, {})
    speakers = load_json(SPEAKERS_PATH, {})
    revision = hashlib.sha256(json.dumps([stations, presets, speakers], sort_keys=True).encode()).hexdigest()
    usages = {}
    for station_id in stations:
        direct = [{"scope": scope, "numbers": [n for n, value in mapping.items() if value == station_id]}
                  for scope, mapping in presets.items() if station_id in mapping.values()]
        effective = []
        for name in speakers:
            mapping = {**presets.get("default", {}), **presets.get(name, {})}
            numbers = [n for n, value in mapping.items() if value == station_id]
            if numbers: effective.append({"scope": name, "numbers": numbers})
        usages[station_id] = {"direct": direct, "effective": effective}
    return {"stations": stations, "usages": usages, "revision": revision}


def configure_station(payload):
    with _CONFIG_LOCK:
        snapshot = station_snapshot()
        if payload.get("revision") != snapshot["revision"]:
            raise ValueError("Configuration modifiée : rechargez le catalogue et confirmez à nouveau.")
        stations = dict(snapshot["stations"])
        station_id = payload.get("id")
        if station_id is not None and (not isinstance(station_id, str) or station_id not in stations):
            raise ValueError("Station inconnue.")
        action = payload.get("action")
        changes = []
        if action == "save":
            name, url = payload.get("name"), payload.get("url")
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 100:
                raise ValueError("Nom requis (100 caractères maximum).")
            if not isinstance(url, str) or len(url) > 4096:
                raise ValueError("Source invalide.")
            name, url = name.strip(), url.strip()
            parsed = urllib.parse.urlsplit(url)
            if not (re.fullmatch(r"tunein:s[0-9]+", url) or
                    (parsed.scheme in ("http", "https") and parsed.hostname and
                     not parsed.username and not parsed.password and not any(c.isspace() for c in url))):
                raise ValueError("Utilisez une URL HTTP(S) ou une source tunein:s12345.")
            for key, value in stations.items():
                label = value.get("note", key) if isinstance(value, dict) else key
                if key != station_id and label.casefold() == name.casefold():
                    raise ValueError("Une station porte déjà ce nom.")
            if station_id is None: station_id = "station-" + uuid.uuid4().hex[:12]
            previous = stations.get(station_id, {})
            stations[station_id] = {**(previous if isinstance(previous, dict) else {}), "note": name, "url": url}
        elif action == "delete" and station_id is not None:
            if payload.get("confirmed") is not True:
                raise ValueError("Confirmation de suppression requise.")
            presets = load_json(PRESETS_PATH, {})
            updated = {scope: {n: (None if value == station_id else value)
                              for n, value in mapping.items() if scope != "default" or value != station_id}
                       for scope, mapping in presets.items()}
            if updated != presets: changes.append((PRESETS_PATH, updated, presets))
            del stations[station_id]
        else:
            raise ValueError("Action invalide.")
        changes.append((STATIONS_PATH, stations, snapshot["stations"]))
        written = []
        try:
            for path, value, previous in changes:
                save_json(path, value)
                written.append((path, previous))
        except OSError:
            for path, previous in reversed(written): save_json(path, previous)
            raise
        return stations


def get_config():
    cfg = load_json(CONFIG_PATH, {})
    cfg.setdefault("proxy_port", 8077)
    # proxy_host: the IP the SPEAKERS use to reach this machine. "auto" derives it.
    cfg.setdefault("proxy_host", "auto")
    return cfg

def resolve_proxy_host(cfg, a_speaker_ip=None):
    host = cfg.get("proxy_host", "auto")
    if host and host != "auto":
        return host
    if a_speaker_ip is None:   # probe address: any known speaker (route selection only)
        sp = load_json(SPEAKERS_PATH, {})
        a_speaker_ip = next((d["ip"] for d in sp.values() if d.get("ip")), "192.168.1.1")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((a_speaker_ip, 9)); return s.getsockname()[0]
    finally:
        s.close()

# ------------------------------------------------------- speaker discovery ---
def discover_speakers(timeout=6):
    """Browse mDNS for _soundtouch._tcp, resolve each to an IP, read /info."""
    found = {}
    try:
        p = subprocess.Popen(["dns-sd", "-B", "_soundtouch._tcp", "local."],
                             stdout=subprocess.PIPE, text=True)
        time.sleep(timeout); p.terminate()
        names = []
        for line in p.stdout:
            parts = line.split("_soundtouch._tcp.")
            if len(parts) == 2:
                nm = parts[1].strip()
                if nm and nm not in names: names.append(nm)
    except FileNotFoundError:
        names = []

    def resolve(name):
        try:
            p = subprocess.Popen(["dns-sd", "-L", name, "_soundtouch._tcp", "local."],
                                 stdout=subprocess.PIPE, text=True)
            time.sleep(2.5); p.terminate()
            host = None
            for line in p.stdout:
                m = re.search(r"reached at ([\w\-.]+\.local)\.?:(\d+)", line)
                if m: host = m.group(1); break
            if not host: return None
            p = subprocess.Popen(["dns-sd", "-Gv4", host],
                                 stdout=subprocess.PIPE, text=True)
            time.sleep(2); p.terminate()
            for line in p.stdout:
                m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", line)
                if m: return m.group(1)
        except Exception:
            return None

    for nm in names:
        ip = resolve(nm)
        if not ip: continue
        try:
            info = urllib.request.urlopen(f"http://{ip}:8090/info", timeout=4).read().decode()
            real = re.search(r"<name>(.*?)</name>", info)
            model = re.search(r"<type>(.*?)</type>", info)
            found[(real.group(1) if real else nm)] = {
                "ip": ip, "model": model.group(1) if model else "?"}
        except Exception:
            found[nm] = {"ip": ip, "model": "?"}
    if found: save_json(SPEAKERS_PATH, found)
    return found

def speakers():
    sp = load_json(SPEAKERS_PATH, {})
    if not sp:
        print("No speakers cached; running discovery…", file=sys.stderr)
        sp = discover_speakers()
    return sp

def find_speaker(query):
    sp = speakers()
    for name, d in sp.items():
        if query.lower() in name.lower():
            return name, d["ip"]
    # allow passing a raw IP — map back to a friendly name if we know it
    if re.match(r"\d{1,3}(\.\d{1,3}){3}$", query):
        for name, d in sp.items():
            if d.get("ip") == query:
                return name, query
        return query, query
    raise SystemExit(f"Speaker '{query}' not found. Known: {', '.join(sp) or '(none)'}")

# -------------------------------------------------------- upstream fetching --
UA = "Mozilla/5.0 (BoseRadioProxy)"

def _connect(scheme, host, port):
    raw = socket.create_connection((host, port), timeout=8)
    if scheme == "https":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
        return ctx.wrap_socket(raw, server_hostname=host)
    return raw

def open_upstream(url, depth=0):
    """Return (content_type, socket) for a (possibly HTTPS / redirecting / ICY)
    audio stream. Tolerates SHOUTcast 'ICY 200 OK' status lines."""
    if depth > 6: raise IOError("too many redirects")
    m = re.match(r"(https?)://([^/:]+)(?::(\d+))?(/.*)?$", url)
    if not m: raise IOError(f"bad url {url}")
    scheme, host, port, path = m.group(1), m.group(2), m.group(3), m.group(4) or "/"
    port = int(port) if port else (443 if scheme == "https" else 80)
    sock = _connect(scheme, host, port)
    req = (f"GET {path} HTTP/1.0\r\nHost: {host}\r\nUser-Agent: {UA}\r\n"
           f"Icy-MetaData: 0\r\nAccept: */*\r\nConnection: close\r\n\r\n")
    sock.sendall(req.encode())
    f = sock.makefile("rb")
    status_line = f.readline().decode("latin1", "ignore").strip()
    headers = {}
    while True:
        line = f.readline().decode("latin1", "ignore").strip()
        if not line: break
        if ":" in line:
            k, v = line.split(":", 1); headers[k.strip().lower()] = v.strip()
    code = 0
    mm = re.search(r"\s(\d{3})\s", " " + status_line + " ")
    if mm: code = int(mm.group(1))
    if status_line.upper().startswith("ICY"): code = 200
    if code in (301, 302, 303, 307, 308) and "location" in headers:
        try: sock.close()
        except Exception: pass
        return open_upstream(headers["location"], depth + 1)
    if code and code >= 400:
        try: sock.close()
        except Exception: pass
        raise IOError(f"upstream {code} for {url}")
    ct = headers.get("content-type", "audio/mpeg")
    if "text" in ct or ct.startswith("audio/x-mpegurl") or "mpegurl" in ct or "scpls" in ct:
        # It's a playlist (.m3u/.pls): read it, pull first stream URL, recurse.
        body = f.read(8192).decode("latin1", "ignore")
        try: sock.close()
        except Exception: pass
        urls = re.findall(r"https?://[^\s\"']+", body)
        if not urls: raise IOError(f"empty playlist {url}")
        return open_upstream(urls[0], depth + 1)
    return ct, sock

def resolve_station_url(station_id, stations):
    """station entry can be a direct URL, or 'tunein:sXXXXX' (resolved live)."""
    val = stations.get(station_id)
    if val is None:
        raise IOError(f"unknown station '{station_id}'")
    url = val["url"] if isinstance(val, dict) else val
    if url.startswith("tunein:"):
        sid = url.split(":", 1)[1]
        tu = f"http://opml.radiotime.com/Tune.ashx?id={sid}&formats=mp3,aac"
        body = urllib.request.urlopen(tu, timeout=6).read().decode("latin1", "ignore")
        urls = [u for u in re.findall(r"https?://[^\s]+", body)]
        if not urls: raise IOError(f"TuneIn gave no URL for {sid}")
        return urls[0]
    return url

# ------------------------------------------------- phone control API + UI ----
def force_invalid(ip):
    """Select a BOGUS TuneIn station so the renderer fast-fails to INVALID_SOURCE
    (the only state a UPnP push reliably takes over from). Same trick as
    button_listener.js — s99999999 must never match a real stored preset."""
    body = ('<ContentItem source="TUNEIN" type="stationurl" '
            'location="/v1/playback/station/s99999999" sourceAccount="" '
            'isPresetable="false"><itemName>reset</itemName></ContentItem>')
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"http://{ip}:8090/select", data=body.encode(),
            headers={"Content-Type": "text/xml"}), timeout=5).read()
    except Exception:
        pass

def build_zone(master_ip):
    """Group ALL known speakers into one zone under master_ip."""
    sp = load_json(SPEAKERS_PATH, {})
    master_id = next((d["deviceID"] for d in sp.values() if d.get("ip") == master_ip), None)
    if not master_id: return
    members = "".join(f'<member ipaddress="{d["ip"]}">{d["deviceID"]}</member>'
                      for d in sp.values() if d.get("deviceID"))
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"http://{master_ip}:8090/setZone",
            data=f'<zone master="{master_id}">{members}</zone>'.encode(),
            headers={"Content-Type": "text/xml"}), timeout=5).read()
    except Exception as e:
        sys.stderr.write(f"[api] setZone failed: {e}\n")

def api_master_ip(cfg):
    """The speaker to push to: the current zone master if one exists, else the
    configured default master."""
    q = cfg.get("master") or next(iter(load_json(SPEAKERS_PATH, {})), None)
    if not q:
        raise RuntimeError("no speakers known — run: ./bose_radio.py discover")
    _, default_ip = find_speaker(q)
    return zone_master_ip(default_ip)

def api_target_ip(target):
    """Only configured speakers may be selected by the web API."""
    sp = load_json(SPEAKERS_PATH, {})
    for name, d in sp.items():
        if target in (name, d.get("ip")):
            return d["ip"]
    raise ValueError(f"Unknown speaker: {target}")


def detach_speaker(ip):
    """Detach a member, or dissolve the zone when selecting its master."""
    master = get_zone_master(ip)
    if not master:
        return
    with urllib.request.urlopen(f"http://{master}:8090/getZone", timeout=4) as response:
        zone = ET.fromstring(response.read())
    members = [m for m in zone.findall("member")
               if master == ip or m.get("ipaddress") == ip]
    if not members:
        return
    body = ET.Element("zone", master=zone.attrib["master"])
    body.extend(members)
    with urllib.request.urlopen(urllib.request.Request(
            f"http://{master}:8090/removeZone", data=ET.tostring(body),
            headers={"Content-Type": "text/xml"}), timeout=5) as response:
        response.read()


def api_play(cfg, station_id, volume=None, target=None):
    # Omitted target preserves existing API clients and the sync configuration.
    group = target == "all" or (target is None and cfg.get("sync", True))
    master = api_target_ip(target) if target and target != "all" else api_master_ip(cfg)
    if station_id not in load_json(STATIONS_PATH, {}):
        raise ValueError(f"Unknown station: {station_id}")
    if not group:
        detach_speaker(master)
    force_invalid(master)
    time.sleep(1.5)
    play(cfg, station_id, master, volume)
    if group:
        time.sleep(1.5)
        build_zone(master)
    if group and volume is not None:
        for d in load_json(SPEAKERS_PATH, {}).values():
            if d.get("ip") and d["ip"] != master:
                set_volume(d["ip"], volume)
    return master


def api_stop_target(target):
    ip = api_target_ip(target)
    detach_speaker(ip)
    standby(ip)
    if load_json(STATE_PATH, {}).get("master_ip") == ip:
        try: os.remove(STATE_PATH)
        except OSError: pass


def api_stop_all():
    """The app's Stop/Off semantics: EVERYTHING off, regardless of which speaker
    is currently the zone master (a physical button press makes the pressed
    speaker the master, so it can be any of them)."""
    sp = list(load_json(SPEAKERS_PATH, {}).values())
    masters = set()
    for d in sp:                       # find actual zone master(s) first
        m = get_zone_master(d.get("ip", ""))
        if m: masters.add(m)
    for m in masters:                  # master standby cascades to the zone
        standby(m)
    if masters:
        time.sleep(2)
    for d in sp:                       # sweep stragglers (solo players, awake members)
        try:
            src, _, _ = now_playing_fields(d["ip"])
        except Exception:
            continue
        if src != "STANDBY":
            standby(d["ip"])
    try: os.remove(STATE_PATH)         # watchdog must not resurrect
    except OSError: pass

def api_status(cfg):
    stations = [{"id": k, "note": (v.get("note", "") if isinstance(v, dict) else "")}
                for k, v in load_json(STATIONS_PATH, {}).items()]
    state = load_json(STATE_PATH, {}) or {}
    out = {"station": state.get("station"),
           "stations": stations, "speakers": [],
           "presets": load_json(PRESETS_PATH, {}),
           "default_speaker": cfg.get("master") or next(iter(load_json(SPEAKERS_PATH, {})), None)}
    for name, d in load_json(SPEAKERS_PATH, {}).items():
        try:
            src, ps, title = now_playing_fields(d["ip"])
        except Exception:
            src, ps, title = "unreachable", "", ""
        station = title if any(s["id"] == title for s in stations) else None
        if not station and d["ip"] == state.get("master_ip") and src == "UPNP":
            station = state.get("station")
        out["speakers"].append({"name": name, "ip": d["ip"], "source": src,
                                "playStatus": ps, "volume": get_volume(d["ip"]),
                                "station": station, "title": title})
    return out

CONTROL_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="theme-color" content="#0b0b0f">
<title>Bose Radio</title><style>
:root{--bg:#0b0b0f;--card:#17171c;--card2:#1f1f26;--txt:#f2f2f5;--dim:#8e8e96;
--acc:#3b82f6;--accD:#1d4ed8;--ok:#34d399;--danger:#b91c1c}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:var(--bg);color:var(--txt);
margin:0;padding:16px 16px calc(24px + env(safe-area-inset-bottom));max-width:480px;margin:auto;
padding-top:calc(16px + env(safe-area-inset-top))}
header{display:flex;justify-content:space-between;align-items:center;margin:2px 2px 14px}
h1{font-size:21px;margin:0;letter-spacing:.2px}
#conn{font-size:12px;color:var(--dim);display:flex;align-items:center;gap:5px}
#conn .dot{width:8px;height:8px;border-radius:99px;background:var(--ok)}
#conn.off .dot{background:#555}
/* ---- now playing hero ---- */
#hero{border-radius:20px;padding:18px;margin-bottom:16px;background:linear-gradient(135deg,#1e293b,#0f172a);
transition:background .4s;min-height:88px;display:flex;align-items:center;gap:14px}
#hero.live{background:linear-gradient(135deg,#1d4ed8,#172554)}
.eq{display:flex;align-items:flex-end;gap:3px;height:30px;width:26px;flex:none}
.eq span{width:5px;background:#fff;border-radius:2px;opacity:.9;height:30%}
#hero.live .eq span{animation:eq 1s ease-in-out infinite}
#hero.live .eq span:nth-child(2){animation-delay:.25s}#hero.live .eq span:nth-child(3){animation-delay:.5s}
@keyframes eq{0%,100%{height:25%}50%{height:100%}}
#hero .t{min-width:0;flex:1}
#hTitle{font-size:19px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#hSub{font-size:13px;color:#cbd5e1;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* ---- speaker selection ---- */
.targets{display:flex;align-items:center;gap:10px}
#target{display:flex;flex:1;min-width:0;overflow-x:auto;background:var(--bg);border-radius:12px;padding:4px;gap:4px}
.target-btn{flex:1 0 auto;min-height:44px;padding:10px 12px;border:0;border-radius:9px;background:transparent;
color:var(--dim);font:inherit;font-size:14px;cursor:pointer}
#target .target-btn[aria-pressed="true"]{background:var(--card2);color:var(--txt);box-shadow:0 1px 4px #0004}
#targetAll{flex:none;background:var(--card2)}
#targetAll[aria-pressed="true"]{background:var(--accD);color:#fff}
.target-btn:disabled{opacity:.5;cursor:wait}
.target-btn:focus-visible,#stop:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
/* ---- volume cluster ---- */
.panel{background:var(--card);border-radius:18px;padding:14px;margin-bottom:16px}
.vrow{display:flex;align-items:center;gap:12px}
.vbtn{flex:none;width:52px;height:52px;border-radius:16px;border:0;background:var(--card2);color:var(--txt);
font-size:28px;font-weight:600;line-height:1}
.vbtn:active{background:#33333d}
input[type=range]{flex:1;height:44px;accent-color:var(--acc)}
#vv{width:30px;text-align:right;font-variant-numeric:tabular-nums;color:var(--dim);font-size:15px}
#spkList{margin-top:6px}
.srow{display:flex;align-items:center;gap:10px;padding:7px 2px}
.srow .nm{width:86px;font-size:13px;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.srow .st{width:8px;height:8px;border-radius:99px;background:#444;flex:none}
.srow .st.on{background:var(--ok)}
/* ---- station tiles ---- */
.sec{font-size:12px;letter-spacing:1.2px;color:var(--dim);margin:4px 4px 10px;text-transform:uppercase}
#grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.tile{position:relative;border:0;border-radius:18px;padding:16px 14px;min-height:92px;color:#fff;text-align:left;
display:flex;flex-direction:column;justify-content:space-between;overflow:hidden}
.tile:active{transform:scale(.97)}
.tile .preset{font-size:12px;font-weight:700;margin-bottom:8px;opacity:.85}
.tile .nm{font-size:16px;font-weight:700}
.tile .nt{font-size:11px;opacity:.75;margin-top:4px;overflow:hidden;display:-webkit-box;
-webkit-line-clamp:2;-webkit-box-orient:vertical}
.tile .badge{position:absolute;top:10px;right:12px;font-size:11px;font-weight:700;opacity:0}
.tile.playing{outline:2px solid #fff6}
.tile.playing .badge{opacity:1}
/* ---- stop ---- */
#stop{display:flex;flex:none;align-items:center;justify-content:center;width:44px;height:44px;padding:0;border:0;
border-radius:14px;background:#ffffff20;color:#fff;cursor:pointer}
#stop[hidden]{display:none}
#stop:active{opacity:.8}
.toast{position:fixed;left:50%;bottom:calc(30px + env(safe-area-inset-bottom));transform:translateX(-50%);
background:#000c;color:#fff;padding:10px 18px;border-radius:99px;font-size:14px;opacity:0;transition:opacity .3s;pointer-events:none}
.toast.show{opacity:1}
.header-actions{display:flex;align-items:center;gap:12px}
.action{border:0;border-radius:12px;padding:12px;background:var(--card2);color:var(--txt);font:inherit;cursor:pointer}
.action:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
#settingsOpen{font-size:24px;line-height:1;min-width:44px;min-height:44px}
dialog{background:var(--bg);color:var(--txt);border:1px solid var(--card2);border-radius:20px;
padding:20px;width:calc(100% - 24px);max-width:480px;max-height:90dvh;overflow:auto}
dialog::backdrop{background:#000a}
#settingsMenu{position:fixed;inset:65px 16px auto auto;margin:0;padding:8px;background:var(--bg);color:var(--txt);border:1px solid var(--card2);border-radius:14px}
#settingsMenu .action{display:block;width:100%;text-align:left;margin:3px 0}
.station-source{overflow-wrap:anywhere;font-size:13px;color:var(--dim)}
#stationsError{color:#fca5a5}
#stationSave{background:var(--accD)}
.settings-head,.settings-actions{display:flex;justify-content:space-between;align-items:center;gap:10px}
h2{font-size:20px;margin:0}
.hint{color:var(--dim);font-size:13px;line-height:1.5}
.speaker-editor{border:1px solid var(--card2);border-radius:14px;padding:14px;margin:14px 0;min-width:0}
.speaker-editor label,.preset-field{display:block;font-size:13px;margin-bottom:10px;color:var(--dim)}
.speaker-editor input,.preset-field select{display:block;width:100%;margin-top:5px;padding:11px;border:1px solid #444;
border-radius:8px;background:var(--card);color:var(--txt);font:inherit;font-size:16px}
.remove-speaker{color:#fca5a5}
#settingsSave,#presetsSave{background:var(--accD)}
#settingsError,#presetsError{color:#fca5a5;font-size:14px}
.station-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.station-head .sec{margin-bottom:0}
.action:disabled{opacity:.5;cursor:wait}
</style></head><body>
<header><h1>Bose Radio</h1><div class="header-actions"><div id="conn"><span class="dot"></span><span id="connTxt">…</span></div>
<button id="settingsOpen" class="action" aria-label="Réglages" title="Réglages" popovertarget="settingsMenu">⚙</button>
<div id="settingsMenu" popover><button id="stationsOpen" class="action">Stations</button><button id="speakersOpen" class="action">Enceintes</button></div></div></header>

<dialog id="stationsSettings" aria-labelledby="stationsTitle">
<div class="settings-head"><h2 id="stationsTitle">Stations</h2><button type="button" id="stationsClose" class="action" aria-label="Fermer">✕</button></div>
<div id="stationList"></div>
<button type="button" id="stationAdd" class="action">+ Ajouter une station</button>
<form id="stationForm" hidden>
<fieldset id="stationFields" class="speaker-editor">
<label>Nom affiché<input id="stationName" required maxlength="100" autocomplete="off"></label>
<label>URL du flux ou source TuneIn<input id="stationUrl" required maxlength="4096" placeholder="https://… ou tunein:s12345" autocomplete="off"></label>
<p class="hint">Modifier une station conserve ses presets. Utilisez l’URL du flux audio, plutôt que celle du site de la radio.</p>
<div class="settings-actions"><button type="button" id="stationCancel" class="action">Annuler</button><button id="stationSave" type="submit" class="action">Enregistrer</button></div>
</fieldset></form>
<p id="stationsError" role="alert"></p>
</dialog>
<dialog id="stationDelete" aria-labelledby="stationDeleteTitle">
<h2 id="stationDeleteTitle">Supprimer la station ?</h2>
<p id="stationDeleteText" style="white-space:pre-line"></p>
<div class="settings-actions"><button type="button" id="stationDeleteCancel" class="action">Annuler</button><button type="button" id="stationDeleteConfirm" class="action remove-speaker">Supprimer</button></div>
</dialog>

<dialog id="settings" aria-labelledby="settingsTitle">
<form id="settingsForm">
<div class="settings-head"><h2 id="settingsTitle">Enceintes</h2><button type="button" id="settingsClose" class="action" aria-label="Fermer">✕</button></div>
<p class="hint">Ajoutez vos enceintes et leurs adresses réseau. L’identifiant Bose (deviceID) est disponible sur http://IP-enceinte:8090/info. Le nom sert dans cette application.</p>
<div id="speakerEditors"></div>
<button type="button" id="speakerAdd" class="action">+ Ajouter une enceinte</button>
<p class="hint">Enregistrer met à jour la configuration locale. Retirer une enceinte de la liste n’arrête pas sa lecture.</p>
<p id="settingsError" role="alert"></p>
<div class="settings-actions"><button type="button" id="settingsCancel" class="action">Annuler</button><button type="submit" id="settingsSave" class="action">Enregistrer</button></div>
</form></dialog>

<dialog id="presetsSettings" aria-labelledby="presetsTitle">
<form id="presetsForm">
<div class="settings-head"><h2 id="presetsTitle">Presets</h2><button type="button" id="presetsClose" class="action" aria-label="Fermer">✕</button></div>
<p class="hint">Associez les boutons 1 à 6 aux stations. La liste globale s’applique à toutes les enceintes ; chaque enceinte peut remplacer certains presets.</p>
<fieldset id="presetsFields" class="speaker-editor">
<label class="preset-field">Configurer pour<select id="presetsScope"></select></label>
<div id="presetEditors"></div>
<button type="button" id="presetsReset" class="action">Reprendre toute la liste globale</button>
</fieldset>
<p class="hint">« Sans affectation » désactive ce preset pour l’enceinte choisie. Enregistrer applique vos changements aux boutons physiques et à l’affichage des stations, sans lancer de lecture.</p>
<p id="presetsError" role="alert"></p>
<div class="settings-actions"><button type="button" id="presetsCancel" class="action">Annuler</button><button type="submit" id="presetsSave" class="action">Enregistrer</button></div>
</form></dialog>

<div id="hero"><div class="eq"><span></span><span></span><span></span></div>
<div class="t"><div id="hTitle">Off</div><div id="hSub">Choisir une enceinte puis une station</div></div>
<button id="stop" hidden aria-label="Arrêter la lecture" title="Arrêter la lecture"><svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="2" fill="currentColor"/></svg></button></div>

<div class="panel targets"><div id="target" role="group" aria-label="Choisir une enceinte"></div>
  <button id="targetAll" class="target-btn" aria-pressed="false">Toutes</button></div>
<div class="panel">
  <div class="vrow">
    <button class="vbtn" id="vdn">&minus;</button>
    <input type="range" id="vol" min="0" max="70" value="30">
    <button class="vbtn" id="vup">+</button>
    <span id="vv">30</span>
  </div>
  <div id="spkList" hidden></div>
</div>

<div class="station-head"><div class="sec">Stations</div><button id="presetsOpen" class="action">Gérer les presets</button></div>
<div id="grid"></div>
<div class="toast" id="toast"></div>
<script>
const $=id=>document.getElementById(id);
let busy=false,volT=null,spkT={},lastTouch=0,currentStatus=null,tileSignature='',speakerSignature='';
let selectedTarget='';
const targetLabel=()=>selectedTarget==='all'?'Toutes les enceintes':
  currentStatus?.speakers.find(s=>s.ip===selectedTarget)?.name||'une enceinte';
const targetQuery=()=>'?speaker='+encodeURIComponent(selectedTarget);
function disableTargets(disabled){
  Array.from($('target').children).forEach(b=>b.disabled=disabled);$('targetAll').disabled=disabled;
}
function selectTarget(value){if(busy)return;selectedTarget=value;clearTimeout(volT);lastTouch=0;
  if(currentStatus)render(currentStatus)}
const touch=()=>lastTouch=Date.now();          // suppress poll updates while user interacts
const idle=()=>Date.now()-lastTouch>1200;
const pretty=s=>s.replace(/-/g,' ').replace(/\\b\\w/g,c=>c.toUpperCase());
const hue=s=>{let h=0;for(const c of s)h=(h*31+c.charCodeAt(0))%360;return h};
async function api(p,options){const r=await fetch(p,options);const data=await r.json();
  if(!r.ok||data.ok===false)throw new Error(data.error||'Request failed');return data}
function toast(m){const t=$('toast');t.textContent=m;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),1800)}

function buildTiles(stations){
  $('grid').replaceChildren();
  stations.forEach(s=>{
    const b=document.createElement('button');b.className='tile';b.id='st-'+s.id;
    const h=hue(s.id);
    b.style.background=`linear-gradient(135deg,hsl(${h} 45% 30%),hsl(${h} 55% 16%))`;
    b.innerHTML='<span class="badge">▶ PLAYING</span><span class="preset"></span><span class="nm"></span><span class="nt"></span>';
    b.querySelector('.preset').textContent=s.numbers.length?'Preset '+s.numbers.join(' / '):'Sans preset';
    b.querySelector('.nm').textContent=s.note||pretty(s.id);
    b.querySelector('.nt').textContent='Internet radio';
    b.onclick=async()=>{if(busy)return;busy=true;touch();
      b.querySelector('.nm').textContent='Starting…';
      disableTargets(true);
      try{await api('/api/play/'+encodeURIComponent(s.id)+targetQuery()+'&volume='+$('vol').value);
        toast((s.note||pretty(s.id))+' → '+targetLabel())}
      catch(e){toast('Erreur : '+e.message)}
      finally{b.querySelector('.nm').textContent=s.note||pretty(s.id);busy=false;disableTargets(false);refresh()}};
    $('grid').appendChild(b)});
}
function buildSpeakers(speakers){
  speakers.forEach(s=>{
    const r=document.createElement('div');r.className='srow';r.id='sp-'+s.ip;
    r.innerHTML='<span class="st"></span><span class="nm"></span><input type="range" min="0" max="70">';
    r.querySelector('.nm').textContent=s.name.replace('ST - ','').replace('ST ','');
    r.querySelector('input').value=s.volume??30;
    const sl=r.querySelector('input');
    sl.oninput=()=>{touch();clearTimeout(spkT[s.ip]);
      spkT[s.ip]=setTimeout(()=>api('/api/volume/'+sl.value+'?ip='+s.ip).catch(e=>toast(e.message)),350)};
    $('spkList').appendChild(r)});
}
function render(st){
  const signatureSpeakers=JSON.stringify(st.speakers.map(s=>[s.name,s.ip]));
  const changed=signatureSpeakers!==speakerSignature;speakerSignature=signatureSpeakers;
  currentStatus=st;
  if(changed){
    clearTimeout(volT);Object.values(spkT).forEach(clearTimeout);spkT={};
    $('target').replaceChildren();$('spkList').replaceChildren();
    if(selectedTarget!=='all'&&!st.speakers.some(s=>s.ip===selectedTarget))selectedTarget='';
  }
  if(!$('target').childElementCount){
    st.speakers.forEach(s=>{
      const b=document.createElement('button');b.className='target-btn';b.textContent=s.name;
      b.value=s.ip;b.onclick=()=>selectTarget(s.ip);$('target').appendChild(b);
    });
    const preferred=st.speakers.find(s=>s.name===st.default_speaker||s.ip===st.default_speaker);
    if(!selectedTarget)selectedTarget=preferred?.ip||st.speakers[0]?.ip||'all';
  }
  const target=selectedTarget;
  Array.from($('target').children).forEach(b=>b.setAttribute('aria-pressed',String(b.value===target)));
  $('targetAll').setAttribute('aria-pressed',String(target==='all'));
  $('spkList').hidden=target!=='all';
  const selected=st.speakers.find(s=>s.ip===target);
  const mapping={...(st.presets?.default||{}),...(selected?st.presets?.[selected.name]||{}:{})};
  const stations=st.stations.map(s=>({...s,numbers:Object.keys(mapping)
    .filter(n=>/^[1-6]$/.test(n)&&mapping[n]===s.id).map(Number).sort((a,b)=>a-b)}))
    .sort((a,b)=>(a.numbers[0]??99)-(b.numbers[0]??99));
  const signature=JSON.stringify(stations);
  if(signature!==tileSignature){buildTiles(stations);tileSignature=signature}
  $('conn').classList.remove('off');$('connTxt').textContent=
    st.speakers.filter(s=>s.playStatus=='PLAY_STATE').length+'/'+st.speakers.length+' playing';
  if($('spkList').childElementCount===0)buildSpeakers(st.speakers);
  const active=st.speakers.filter(s=>(!selected||s.ip===selected.ip)&&s.playStatus==='PLAY_STATE');
  const live=active.length>0;
  const playingStation=active.length&&active.every(s=>s.station===active[0].station)?active[0].station:null;
  $('hero').classList.toggle('live',live);
  $('stop').hidden=!live;
  $('hTitle').textContent=live?(playingStation?(st.stations.find(s=>s.id===playingStation)?.note||pretty(playingStation)):active.length>1?'Plusieurs sources':active[0].title||'En lecture'):'Off';
  $('hSub').textContent=live?'En lecture : '+active.map(s=>s.name).join(', '):'Choisir une enceinte puis une station';
  st.stations.forEach(s=>$('st-'+s.id).classList.toggle('playing',live&&s.id===playingStation));
  st.speakers.forEach(s=>{const r=$('sp-'+s.ip);if(!r)return;
    r.querySelector('.st').classList.toggle('on',s.playStatus=='PLAY_STATE');
    if(idle()&&s.volume!=null)r.querySelector('input').value=s.volume});
  if(idle()){const v=selected||st.speakers.find(s=>s.volume!=null);
    if(v&&v.volume!=null){$('vol').value=v.volume;$('vv').textContent=v.volume}}
}
async function refresh(){try{render(await api('/api/status'))}
  catch(e){$('conn').classList.add('off');$('connTxt').textContent='offline'}}
function selectedVolumeQuery(){return selectedTarget==='all'?'':'?ip='+encodeURIComponent(selectedTarget)}
$('targetAll').onclick=()=>selectTarget('all');
function setGroupVol(v){v=Math.max(0,Math.min(70,v));$('vol').value=v;$('vv').textContent=v;touch();
  clearTimeout(volT);const query=selectedVolumeQuery();volT=setTimeout(()=>api('/api/volume/'+v+query).catch(e=>toast(e.message)),350)}
$('vol').oninput=()=>setGroupVol(+$('vol').value);
$('vdn').onclick=()=>setGroupVol(+$('vol').value-5);
$('vup').onclick=()=>setGroupVol(+$('vol').value+5);
$('stop').onclick=async()=>{if(busy)return;busy=true;touch();disableTargets(true);
  try{await api('/api/stop'+targetQuery());toast('Arrêt : '+targetLabel())}
  catch(e){toast('Erreur : '+e.message)}finally{busy=false;disableTargets(false);refresh()}};
let settingsSaving=false;
function addSpeakerEditor(name='',speaker={}){
  const row=document.createElement('fieldset');row.className='speaker-editor';row.originalName=name;
  for(const [key,label,value] of [['name','Nom',name],['ip','Adresse IPv4',speaker.ip||''],
    ['deviceID','Identifiant Bose (deviceID)',speaker.deviceID||''],['model','Modèle (facultatif)',speaker.model||'']]){
    const field=document.createElement('label');field.textContent=label;
    const input=document.createElement('input');input.name=key;input.value=value;
    input.required=key!=='model';input.maxLength=key==='name'?80:key==='model'?100:key==='ip'?15:12;
    if(key==='deviceID')input.pattern='[0-9a-fA-F]{12}';
    input.autocomplete='off';field.appendChild(input);row.appendChild(field);
  }
  const remove=document.createElement('button');remove.type='button';remove.className='action remove-speaker';
  remove.textContent='Retirer';remove.onclick=()=>row.remove();row.appendChild(remove);
  $('speakerEditors').appendChild(row);
}
function closeSettings(){if(!settingsSaving)$('settings').close()}
$('settingsClose').onclick=closeSettings;$('settingsCancel').onclick=closeSettings;
$('settings').oncancel=e=>{if(settingsSaving)e.preventDefault()};
$('speakerAdd').onclick=()=>addSpeakerEditor();
$('speakersOpen').onclick=async()=>{
  $('settingsMenu').hidePopover();
  if(busy)return;
  $('speakersOpen').disabled=true;
  try{
    const config=await api('/api/speakers');$('speakerEditors').replaceChildren();
    Object.entries(config.speakers).forEach(([name,speaker])=>addSpeakerEditor(name,speaker));
    if(!Object.keys(config.speakers).length)addSpeakerEditor();
    $('settingsError').textContent='';$('settings').showModal();
  }catch(e){toast('Erreur : '+e.message)}finally{$('speakersOpen').disabled=false}
};
$('settingsForm').onsubmit=async e=>{
  e.preventDefault();if(settingsSaving)return;
  const speakers=Array.from($('speakerEditors').children).map(row=>{
    const speaker={original_name:row.originalName};
    for(const key of ['name','ip','deviceID','model'])speaker[key]=row.querySelector('[name="'+key+'"]').value.trim();
    return speaker;
  });
  settingsSaving=true;$('settingsSave').disabled=true;$('settingsError').textContent='';
  Array.from($('speakerEditors').children).forEach(row=>row.disabled=true);
  $('speakerAdd').disabled=true;
  try{await api('/api/speakers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({speakers})});
    $('settings').close();toast('Enceintes enregistrées');await refresh();
  }catch(e){$('settingsError').textContent=e.message}
  finally{settingsSaving=false;$('settingsSave').disabled=false;$('speakerAdd').disabled=false;
    Array.from($('speakerEditors').children).forEach(row=>row.disabled=false)}
};
let stationConfig=null,stationEditing=null,stationDeleting=null,stationSaving=false;
function stationLabel(id,value){return value&&typeof value==='object'?(value.note||pretty(id)):pretty(id)}
function editStation(id=null){
  stationEditing=id;
  const value=id===null?null:stationConfig.stations[id];
  $('stationName').value=id===null?'':stationLabel(id,value);
  $('stationUrl').value=typeof value==='string'?value:value?.url||'';
  $('stationForm').hidden=false;$('stationsError').textContent='';$('stationName').focus();
}
function renderStationList(){
  $('stationList').replaceChildren();
  if(!Object.keys(stationConfig.stations).length){
    const empty=document.createElement('p');empty.className='hint';empty.textContent='Aucune station. Ajoutez votre première station.';$('stationList').appendChild(empty);
  }
  for(const [id,value] of Object.entries(stationConfig.stations)){
    const row=document.createElement('fieldset');row.className='speaker-editor';
    const name=document.createElement('strong');name.textContent=stationLabel(id,value);row.appendChild(name);
    const source=document.createElement('p');source.className='station-source';source.textContent=typeof value==='string'?value:value.url;row.appendChild(source);
    const edit=document.createElement('button');edit.type='button';edit.className='action';edit.textContent='Modifier';edit.disabled=stationSaving;
    edit.onclick=()=>editStation(id);row.appendChild(edit);
    const remove=document.createElement('button');remove.type='button';remove.className='action remove-speaker';remove.textContent='Supprimer';remove.disabled=stationSaving;
    remove.onclick=async()=>{
      try{
        stationConfig=await api('/api/stations');renderStationList();
        if(!Object.hasOwn(stationConfig.stations,id))throw new Error('Station déjà supprimée.');
        stationDeleting=id;
        const usage=stationConfig.usages[id];
        const lines=['Supprimer « '+stationLabel(id,stationConfig.stations[id])+' » du catalogue ?'];
        for(const item of usage.direct)lines.push((item.scope==='default'?'Liste globale':item.scope)+' : preset(s) '+item.numbers.join(', '));
        if(usage.effective.length)lines.push('Enceintes concernées : '+usage.effective.map(item=>item.scope+' (preset(s) '+item.numbers.join(', ')+')').join(', '));
        lines.push(usage.direct.length?'Ces affectations seront supprimées. Les presets individuels concernés resteront sans affectation.':'Cette station n’est affectée à aucun preset.');
        $('stationDeleteText').textContent=lines.join('\\n');$('stationDelete').showModal();
      }catch(e){$('stationsError').textContent=e.message}
    };row.appendChild(remove);$('stationList').appendChild(row);
  }
}
async function loadStations(){stationConfig=await api('/api/stations');renderStationList()}
$('stationsOpen').onclick=async()=>{
  if(busy)return;$('settingsMenu').hidePopover();
  try{await loadStations();$('stationForm').hidden=true;$('stationsError').textContent='';$('stationsSettings').showModal()}
  catch(e){toast('Erreur : '+e.message)}
};
$('stationsClose').onclick=()=>{if(!stationSaving)$('stationsSettings').close()};
$('stationsSettings').oncancel=e=>{if(stationSaving)e.preventDefault()};
$('stationAdd').onclick=()=>editStation();
$('stationCancel').onclick=()=>{$('stationForm').hidden=true};
$('stationDeleteCancel').onclick=()=>{if(!stationSaving)$('stationDelete').close()};
$('stationDelete').oncancel=e=>{if(stationSaving)e.preventDefault()};
async function writeStation(payload){
  if(stationSaving)return;
  stationSaving=true;$('stationsError').textContent='';$('stationFields').disabled=true;
  $('stationAdd').disabled=true;$('stationDeleteConfirm').disabled=true;renderStationList();
  try{
    await api('/api/stations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,revision:stationConfig.revision})});
    $('stationForm').hidden=true;toast(payload.action==='delete'?'Station supprimée':'Station enregistrée');
    await loadStations();await refresh();
  }catch(e){$('stationsError').textContent=e.message;try{await loadStations()}catch{}}
  finally{stationSaving=false;$('stationFields').disabled=false;$('stationAdd').disabled=false;
    $('stationDeleteConfirm').disabled=false;$('stationDelete').close();renderStationList()}
}
$('stationForm').onsubmit=e=>{e.preventDefault();return writeStation({action:'save',id:stationEditing,name:$('stationName').value,url:$('stationUrl').value})};
$('stationDeleteConfirm').onclick=()=>writeStation({action:'delete',id:stationDeleting,confirmed:true});
let presetsSaving=false,presetDrafts={},presetChanges={},presetStations=[];
function buildPresetEditors(){
  const scope=$('presetsScope').value,mapping=presetDrafts[scope]||{};
  $('presetEditors').replaceChildren();$('presetsReset').hidden=scope==='default';
  for(let n=1;n<=6;n++){
    const label=document.createElement('label');label.className='preset-field';label.textContent='Preset '+n;
    const select=document.createElement('select');select.name=String(n);
    if(scope!=='default'){
      const inherited=presetDrafts.default?.[n];
      const station=presetStations.find(s=>s.id===inherited);
      select.add(new Option('Liste globale — '+(inherited?(station?.note||pretty(inherited)):'sans affectation'),'inherit'));
    }
    select.add(new Option('Sans affectation',''));
    presetStations.forEach(s=>select.add(new Option(s.note||pretty(s.id),'station:'+s.id)));
    const value=mapping[n];
    if(value&&!presetStations.some(s=>s.id===value))select.add(new Option('Station indisponible : '+value,'station:'+value));
    select.value=value?'station:'+value:(scope!=='default'&&value===undefined?'inherit':'');
    select.onchange=()=>{
      const draft=presetDrafts[scope];
      if(select.value==='inherit'||(scope==='default'&&!select.value))delete draft[n];
      else draft[n]=select.value?select.value.slice(8):null;
      presetChanges[scope]=draft;
    };
    label.appendChild(select);$('presetEditors').appendChild(label);
  }
}
function closePresets(){if(!presetsSaving)$('presetsSettings').close()}
$('presetsClose').onclick=closePresets;$('presetsCancel').onclick=closePresets;
$('presetsSettings').oncancel=e=>{if(presetsSaving)e.preventDefault()};
$('presetsScope').onchange=buildPresetEditors;
$('presetsReset').onclick=()=>{
  const scope=$('presetsScope').value;if(scope==='default')return;
  presetDrafts[scope]={};presetChanges[scope]=presetDrafts[scope];buildPresetEditors();
};
$('presetsOpen').onclick=async()=>{
  if(busy)return;$('presetsOpen').disabled=true;
  try{
    const config=await api('/api/presets');
    presetDrafts=Object.assign(Object.create(null),config.presets);presetChanges=Object.create(null);presetStations=config.stations;
    $('presetsScope').replaceChildren();$('presetsScope').add(new Option('Liste globale — toutes les enceintes','default'));
    for(const name of Object.keys(config.speakers)){
      $('presetsScope').add(new Option(name,name));presetDrafts[name]??={};
    }
    presetDrafts.default??={};
    const selected=currentStatus?.speakers.find(s=>s.ip===selectedTarget)?.name;
    $('presetsScope').value=selected&&Object.hasOwn(config.speakers,selected)?selected:'default';
    buildPresetEditors();$('presetsError').textContent='';$('presetsSettings').showModal();
  }catch(e){toast('Erreur : '+e.message)}finally{$('presetsOpen').disabled=false}
};
$('presetsForm').onsubmit=async e=>{
  e.preventDefault();if(presetsSaving)return;
  presetsSaving=true;$('presetsFields').disabled=true;$('presetsSave').disabled=true;$('presetsError').textContent='';
  try{
    await api('/api/presets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({presets:presetChanges})});
    $('presetsSettings').close();toast('Presets enregistrés');await refresh();
  }catch(e){$('presetsError').textContent=e.message}
  finally{presetsSaving=false;$('presetsFields').disabled=false;$('presetsSave').disabled=false}
};
refresh();setInterval(refresh,5000);
</script></body></html>"""

# ----------------------------------------------------------------- proxy -----
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
        if self.path not in ("/api/speakers", "/api/presets", "/api/stations"):
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
            key = self.path.rsplit("/", 1)[1]
            configure = {"presets": configure_presets, "speakers": configure_speakers, "stations": configure_station}[key]
            result = configure(payload if key == "stations" else payload.get(key))
            return self._json({"ok": True, key: result})
        except (ValueError, UnicodeError) as e:
            return self._json({"ok": False, "error": str(e)}, 400)
        except OSError:
            return self._json({"ok": False, "error": "Écriture impossible. Vérifier les droits du dossier de configuration."}, 500)

    def do_GET(self):
        cfg = get_config()
        path = self.path.split("?", 1)[0]
        qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        # ---- phone control UI + API ----
        if path in ("/", "/index.html"):
            body = CONTROL_PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body); return
        if path == "/api/status":
            return self._json(api_status(cfg))
        if path == "/api/speakers":
            return self._json({"speakers": load_json(SPEAKERS_PATH, {})})
        if path == "/api/stations":
            with _CONFIG_LOCK:
                return self._json(station_snapshot())
        if path == "/api/presets":
            with _CONFIG_LOCK:
                return self._json({"presets": load_json(PRESETS_PATH, {}),
                                   "speakers": load_json(SPEAKERS_PATH, {}),
                                   "stations": [{"id": k, "note": v.get("note", "") if isinstance(v, dict) else ""}
                                                for k, v in load_json(STATIONS_PATH, {}).items()]})
        m = re.match(r"/api/play/(.+)$", path)
        if m:
            station = urllib.parse.unquote(m.group(1))
            try:
                vol = int(qs["volume"][0]) if qs.get("volume") else None
                master = api_play(cfg, station, vol, qs.get("speaker", [None])[0])
                return self._json({"ok": True, "station": station, "master": master})
            except (SystemExit, ValueError) as e:  # invalid request
                return self._json({"ok": False, "error": str(e)}, 400)
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 500)
        m = re.match(r"/api/volume/(\d+)$", path)
        if m:
            vol = int(m.group(1))
            one_ip = qs.get("ip", [None])[0]       # ?ip=x.x.x.x -> just that speaker
            for d in load_json(SPEAKERS_PATH, {}).values():
                if one_ip is None or d.get("ip") == one_ip:
                    set_volume(d["ip"], vol)
            return self._json({"ok": True, "volume": vol, "ip": one_ip})
        if path == "/api/stop":
            try:
                target = qs.get("speaker", [None])[0]
                if target and target != "all":
                    api_stop_target(target)
                else:
                    api_stop_all()
                return self._json({"ok": True})
            except Exception as e:
                return self._json({"ok": False, "error": str(e)}, 500)
        # ---- stream proxy ----
        m = re.match(r"/stream/(.+)$", path)
        if not m:
            self.send_response(404); self.end_headers(); return
        station_id = urllib.parse.unquote(m.group(1))
        stations = load_json(STATIONS_PATH, {})
        try:
            url = resolve_station_url(station_id, stations)
            ct, up = open_upstream(url)
        except Exception as e:
            sys.stderr.write(f"[proxy] open fail {station_id}: {e}\n")
            self.send_response(502); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("icy-name", station_id)
        self.end_headers()
        sys.stderr.write(f"[proxy] streaming '{station_id}' <- {url} ({ct})\n")
        client_ip = self.client_address[0]
        _active_inc(client_ip)
        try:
            while True:
                chunk = up.recv(16384)
                if not chunk: break
                self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            sys.stderr.write(f"[proxy] stream end {station_id}: {e}\n")
        finally:
            _active_dec(client_ip)
            try: up.close()
            except Exception: pass

def watchdog(cfg):
    """Self-heal the buffer-drain stall: if the zone master reports a live UPnP
    play state but no stream is actually open to the proxy from it, re-push the
    last intended station to the master. Only acts on a sustained stall, with a
    cooldown, and never resurrects a deliberately stopped / powered-off speaker."""
    poll     = cfg.get("watchdog_poll", 15)      # seconds between checks
    grace    = cfg.get("watchdog_grace", 2)      # consecutive stalls before healing
    cooldown = cfg.get("watchdog_cooldown", 90)  # min seconds between heals
    log = lambda s: sys.stderr.write(f"[watchdog] {s}\n")
    log(f"started (poll={poll}s grace={grace} cooldown={cooldown}s)")
    stalled, last_heal = 0, 0.0
    while True:
        time.sleep(poll)
        st = load_json(STATE_PATH, None)
        if not st or not st.get("station") or not st.get("master_ip"):
            stalled = 0; continue
        master = zone_master_ip(st["master_ip"])
        try:
            src, ps, _ = now_playing_fields(master)
        except Exception:
            stalled = 0; continue
        # Two healable signatures (STANDBY / other sources mean the user took
        # over — leave those alone):
        #  1. UPnP stuck "playing" with no open proxy stream (buffer-drain stall)
        #  2. INVALID_SOURCE while state.json exists — the renderer gave up after
        #     the stream died (e.g. this host rebooted). Never the result of a
        #     deliberate stop (stop() -> STANDBY and clears state.json), so it is
        #     always safe to resume from. This is what auto-resumes after a reboot.
        stuck_play   = (src == "UPNP" and ps in ("PLAY_STATE", "BUFFERING_STATE")
                        and not _active_has(master))
        dead_invalid = (src == "INVALID_SOURCE")
        if not (stuck_play or dead_invalid):
            stalled = 0; continue
        stalled += 1
        why = f"{ps} but no proxy stream" if stuck_play else "INVALID_SOURCE (stream died)"
        log(f"stall {stalled}/{grace}: master {master} {why}")
        if stalled < grace:
            continue
        if time.time() - last_heal < cooldown:
            continue
        log(f"healing -> re-push '{st['station']}' to master {master}")
        try:
            play(cfg, st["station"], master, st.get("volume"))
            last_heal = time.time(); stalled = 0
        except (RendererWedged, SystemExit) as e:   # wedged renderer / unknown station
            log(f"heal could not complete: {e}")
        except Exception as e:
            log(f"heal error: {e}")

def serve(cfg):
    import urllib.parse  # noqa (used in handler)
    port = cfg["proxy_port"]
    host = resolve_proxy_host(cfg)
    srv = ThreadingHTTPServer(("0.0.0.0", port), ProxyHandler)
    if cfg.get("watchdog", True):
        threading.Thread(target=watchdog, args=(cfg,), daemon=True).start()
    print(f"Interface web : http://{host}:{port}/  (Ctrl-C pour arrêter)")
    print("Keep this running while you want radio to play.")
    try: srv.serve_forever()
    except KeyboardInterrupt: print("\nbye")

# ----------------------------------------------------- UPnP / SoundTouch -----
class RendererWedged(Exception):
    """UPnP HTTP 500 — renderer wedged OR command sent to a zone member.
    A real Exception subclass (unlike SystemExit) so server threads can catch it."""

def soap(ip, action, inner):
    ctrl = f"http://{ip}:8091/AVTransport/Control"
    env = ('<?xml version="1.0"?><s:Envelope '
           'xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
           's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
           f"<s:Body>{inner}</s:Body></s:Envelope>")
    req = urllib.request.Request(ctrl, data=env.encode(), headers={
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPAction": f'"urn:schemas-upnp-org:service:AVTransport:1#{action}"'})
    try:
        return urllib.request.urlopen(req, timeout=6).read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        if e.code == 500:
            raise RendererWedged(
                f"UPnP on {ip} returned HTTP 500 — either the command hit a zone "
                f"MEMBER (target the master) or the renderer is wedged "
                f"(power-cycle: unplug ~10s).")
        raise

def st_key(ip, key):
    for state in ("press", "release"):
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"http://{ip}:8090/key",
                data=f'<key state="{state}" sender="Gabbo">{key}</key>'.encode()),
                timeout=4).read()
        except Exception:
            pass

def set_volume(ip, vol):
    vol = max(0, min(100, int(vol)))
    try:
        urllib.request.urlopen(urllib.request.Request(
            f"http://{ip}:8090/volume",
            data=f"<volume>{vol}</volume>".encode()), timeout=4).read()
    except Exception as e:
        print("volume set failed:", e)

def get_volume(ip):
    try:
        v = urllib.request.urlopen(f"http://{ip}:8090/volume", timeout=4).read().decode()
        m = re.search(r"<actualvolume>(\d+)", v)
        return int(m.group(1)) if m else None
    except Exception:
        return None

def now_playing_fields(ip):
    """Return (source, playStatus, itemName) from a speaker's /now_playing."""
    np = urllib.request.urlopen(f"http://{ip}:8090/now_playing", timeout=4).read().decode("utf-8", "ignore")
    src = (re.search(r'<nowPlaying[^>]*source="([^"]+)"', np) or [None, "?"])[1]
    ps  = (re.search(r"<playStatus>([^<]+)", np) or [None, ""])[1]
    itm = (re.search(r"<itemName>([^<]*)", np) or [None, ""])[1]
    return src, ps, itm

def get_zone_master(ip):
    """Master IP of the zone this speaker is in, or None when solo.
    NOTE: a MEMBER's getZone view does NOT list the master among its <member>
    entries — it only carries the master deviceID and a senderIPAddress attr.
    So resolve the master id via speakers.json first, then the member list,
    then senderIPAddress."""
    try:
        z = urllib.request.urlopen(f"http://{ip}:8090/getZone", timeout=4).read().decode("utf-8", "ignore")
        m = re.search(r'<zone master="([0-9A-Fa-f]+)"', z)
        if not m:
            return None
        master_id = m.group(1)
        for d in load_json(SPEAKERS_PATH, {}).values():
            if d.get("deviceID") == master_id and d.get("ip"):
                return d["ip"]
        mm = re.search(rf'<member ipaddress="([^"]+)">\s*{master_id}\s*</member>', z)
        if mm: return mm.group(1)
        ms = re.search(r'senderIPAddress="([^"]+)"', z)
        return ms.group(1) if ms else None
    except Exception:
        return None

def zone_master_ip(ip):
    """Resolve the current zone master's IP for a speaker; ip itself when solo."""
    return get_zone_master(ip) or ip

def standby(ip):
    """Put one speaker in standby. POWER is a TOGGLE — never send it to a
    speaker that is already in STANDBY or it wakes up."""
    try:
        src, _, _ = now_playing_fields(ip)
        if src == "STANDBY": return
    except Exception:
        pass
    try:
        soap(ip, "Stop", '<u:Stop xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
                         "<InstanceID>0</InstanceID></u:Stop>")
    except Exception:
        pass            # members 500 on UPnP commands — POWER below still works
    st_key(ip, "POWER")

def play(cfg, station_id, speaker_q, volume=None):
    stations = load_json(STATIONS_PATH, {})
    if station_id not in stations:
        # substring match on station names/keys
        cand = [k for k in stations if station_id.lower() in k.lower()]
        if len(cand) == 1: station_id = cand[0]
        elif not cand: raise SystemExit(f"No station matching '{station_id}'. Known: {', '.join(stations)}")
        else: raise SystemExit(f"Ambiguous station '{station_id}': {', '.join(cand)}")
    name, ip = find_speaker(speaker_q)
    host = resolve_proxy_host(cfg)
    proxy_url = f"http://{host}:{cfg['proxy_port']}/stream/{urllib.parse.quote(station_id)}"
    title = station_id
    didl = ('&lt;DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/"&gt;'
            '&lt;item id="0" parentID="-1" restricted="1"&gt;'
            f'&lt;dc:title&gt;{title}&lt;/dc:title&gt;'
            '&lt;upnp:class&gt;object.item.audioItem.audioBroadcast&lt;/upnp:class&gt;'
            f'&lt;res protocolInfo="http-get:*:audio/mpeg:*"&gt;{proxy_url}&lt;/res&gt;'
            '&lt;/item&gt;&lt;/DIDL-Lite&gt;')
    if volume is not None: set_volume(ip, volume)
    soap(ip, "SetAVTransportURI",
         '<u:SetAVTransportURI xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
         f"<InstanceID>0</InstanceID><CurrentURI>{proxy_url}</CurrentURI>"
         f"<CurrentURIMetaData>{didl}</CurrentURIMetaData></u:SetAVTransportURI>")
    soap(ip, "Play",
         '<u:Play xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
         "<InstanceID>0</InstanceID><Speed>1</Speed></u:Play>")
    print(f"▶  '{station_id}' -> {name} ({ip})   via {proxy_url}")
    # record intended playback so the watchdog (in `serve`) can self-heal stalls
    vol_record = volume if volume is not None else get_volume(ip)
    save_json(STATE_PATH, {"station": station_id, "master_name": name,
                           "master_ip": ip, "volume": vol_record, "ts": int(time.time())})

def status(speaker_q):
    name, ip = find_speaker(speaker_q)
    try:
        np = urllib.request.urlopen(f"http://{ip}:8090/now_playing", timeout=4).read().decode()
        src = (re.search(r'ContentItem source="([^"]+)"', np) or [None, "?"])[1]
        ps  = (re.search(r"<playStatus>([^<]+)", np) or [None, "(idle)"])[1]
        itm = (re.search(r"<ContentItem[^>]*>\s*<itemName>([^<]*)", np) or [None, ""])[1]
        print(f"{name} ({ip}): source={src} status={ps} {itm}")
    except Exception as e:
        print(f"{name} ({ip}): {e}")

def stop(speaker_q, standby=True):
    name, ip = find_speaker(speaker_q)
    try:
        soap(ip, "Stop", '<u:Stop xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
                         "<InstanceID>0</InstanceID></u:Stop>")
    except Exception: pass
    if standby: st_key(ip, "POWER")
    # deliberate stop: clear intended state so the watchdog won't resurrect it
    try: os.remove(STATE_PATH)
    except OSError: pass
    print(f"■  stopped {name} ({ip})")

# ------------------------------------------------------------------ main -----
def main():
    cfg = get_config()
    ap = argparse.ArgumentParser(description="Bose SoundTouch local radio")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("discover")
    sub.add_parser("list")
    sub.add_parser("serve")
    p = sub.add_parser("play"); p.add_argument("station"); p.add_argument("speaker")
    p.add_argument("--volume", type=int, default=None)
    p = sub.add_parser("stop"); p.add_argument("speaker")
    p = sub.add_parser("status"); p.add_argument("speaker")
    args = ap.parse_args()

    if args.cmd == "discover":
        sp = discover_speakers()
        for n, d in sp.items(): print(f"  {n:14s} {d['ip']:15s} {d.get('model','')}")
        if not sp: print("  (none found)")
    elif args.cmd == "list":
        print("Speakers:")
        for n, d in speakers().items(): print(f"  {n:14s} {d['ip']:15s} {d.get('model','')}")
        print("Stations:")
        for k, v in load_json(STATIONS_PATH, {}).items():
            url = v["url"] if isinstance(v, dict) else v
            print(f"  {k:24s} {url}")
        print(f"Proxy: host={resolve_proxy_host(cfg)} port={cfg['proxy_port']}")
    elif args.cmd == "serve":
        serve(cfg)
    elif args.cmd == "play":
        try:
            play(cfg, args.station, args.speaker, args.volume)
        except RendererWedged as e:
            raise SystemExit(str(e))
    elif args.cmd == "stop":
        stop(args.speaker)
    elif args.cmd == "status":
        status(args.speaker)
    else:
        ap.print_help()

if __name__ == "__main__":
    import urllib.parse
    main()
