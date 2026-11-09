"""Résolution à la demande et relais des flux audio."""
import sys, socket, ssl, re, threading
import urllib.request
from . import config

# Le maître tire le flux du proxy ; les membres le reçoivent du maître.
_ACTIVE_LOCK = threading.Lock()
_ACTIVE = {}  # client_ip -> nombre de connexions /stream ouvertes
UA = "Mozilla/5.0 (BoseRadioProxy)"

def _active_inc(ip):
    with _ACTIVE_LOCK: _ACTIVE[ip] = _ACTIVE.get(ip, 0) + 1


def _active_dec(ip):
    with _ACTIVE_LOCK:
        n = _ACTIVE.get(ip, 0) - 1
        if n <= 0: _ACTIVE.pop(ip, None)
        else:      _ACTIVE[ip] = n


def _active_has(ip):
    with _ACTIVE_LOCK: return _ACTIVE.get(ip, 0) > 0


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



def stream_audio(handler, station_id):
    """Transférer un flux à la demande et libérer sa connexion en fin de lecture."""
    stations = config.load_json(config.STATIONS_PATH, {})
    try:
        url = resolve_station_url(station_id, stations)
        ct, up = open_upstream(url)
    except Exception as e:
        sys.stderr.write(f"[proxy] open fail {station_id}: {e}\n")
        handler.send_response(502); handler.end_headers(); return
    try:
        handler.send_response(200)
        handler.send_header("Content-Type", ct)
        handler.send_header("icy-name", station_id)
        handler.end_headers()
        sys.stderr.write(f"[proxy] streaming '{station_id}' <- {url} ({ct})\n")
        client_ip = handler.client_address[0]
        _active_inc(client_ip)
        try:
            while True:
                chunk = up.recv(16384)
                if not chunk: break
                handler.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            sys.stderr.write(f"[proxy] stream end {station_id}: {e}\n")
        finally:
            _active_dec(client_ip)
    finally:
        try: up.close()
        except Exception: pass
