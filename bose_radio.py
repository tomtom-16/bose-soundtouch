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
    with open(path, "w") as f: json.dump(obj, f, indent=2, ensure_ascii=False)

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

def api_play(cfg, station_id, volume=None):
    master = api_master_ip(cfg)
    force_invalid(master)          # free the engine — push won't take over an active source
    time.sleep(1.5)
    play(cfg, station_id, master, volume)   # push + volume + state.json (watchdog intent)
    if cfg.get("sync", True):
        time.sleep(1.5)            # let the master start before grouping
        build_zone(master)
    if volume is not None:         # volume applies to the whole group, not just the master
        for d in load_json(SPEAKERS_PATH, {}).values():
            if d.get("ip") and d["ip"] != master:
                set_volume(d["ip"], volume)
    return master

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
    out = {"station": (load_json(STATE_PATH, {}) or {}).get("station"),
           "stations": stations, "speakers": []}
    for name, d in load_json(SPEAKERS_PATH, {}).items():
        try:
            src, ps, _ = now_playing_fields(d["ip"])
        except Exception:
            src, ps = "unreachable", ""
        out["speakers"].append({"name": name, "ip": d["ip"], "source": src,
                                "playStatus": ps, "volume": get_volume(d["ip"])})
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
#hero .t{min-width:0}
#hTitle{font-size:19px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#hSub{font-size:13px;color:#cbd5e1;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* ---- volume cluster ---- */
.panel{background:var(--card);border-radius:18px;padding:14px;margin-bottom:16px}
.vrow{display:flex;align-items:center;gap:12px}
.vbtn{flex:none;width:52px;height:52px;border-radius:16px;border:0;background:var(--card2);color:var(--txt);
font-size:28px;font-weight:600;line-height:1}
.vbtn:active{background:#33333d}
input[type=range]{flex:1;height:44px;accent-color:var(--acc)}
#vv{width:30px;text-align:right;font-variant-numeric:tabular-nums;color:var(--dim);font-size:15px}
#spkToggle{background:none;border:0;color:var(--dim);font-size:13px;padding:10px 4px 2px;width:100%;text-align:left}
#spkList{display:none;margin-top:6px}
#spkList.open{display:block}
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
.tile .nm{font-size:16px;font-weight:700}
.tile .nt{font-size:11px;opacity:.75;margin-top:4px;overflow:hidden;display:-webkit-box;
-webkit-line-clamp:2;-webkit-box-orient:vertical}
.tile .badge{position:absolute;top:10px;right:12px;font-size:11px;font-weight:700;opacity:0}
.tile.playing{outline:2px solid #fff6}
.tile.playing .badge{opacity:1}
/* ---- stop ---- */
#stop{display:block;width:100%;padding:17px;margin-top:18px;font-size:17px;font-weight:600;border:0;
border-radius:18px;background:var(--danger);color:#fff}
#stop:active{opacity:.8}
.toast{position:fixed;left:50%;bottom:calc(30px + env(safe-area-inset-bottom));transform:translateX(-50%);
background:#000c;color:#fff;padding:10px 18px;border-radius:99px;font-size:14px;opacity:0;transition:opacity .3s;pointer-events:none}
.toast.show{opacity:1}
</style></head><body>
<header><h1>Bose Radio</h1><div id="conn"><span class="dot"></span><span id="connTxt">…</span></div></header>

<div id="hero"><div class="eq"><span></span><span></span><span></span></div>
<div class="t"><div id="hTitle">Off</div><div id="hSub">Tap a station to play everywhere</div></div></div>

<div class="panel">
  <div class="vrow">
    <button class="vbtn" id="vdn">&minus;</button>
    <input type="range" id="vol" min="0" max="70" value="30">
    <button class="vbtn" id="vup">+</button>
    <span id="vv">30</span>
  </div>
  <button id="spkToggle">Speakers ▸</button>
  <div id="spkList"></div>
</div>

<div class="sec">Stations</div>
<div id="grid"></div>
<button id="stop">■&ensp;Stop / Off</button>
<div class="toast" id="toast"></div>
<script>
const $=id=>document.getElementById(id);
let busy=false,volT=null,spkT={},lastTouch=0;
const touch=()=>lastTouch=Date.now();          // suppress poll updates while user interacts
const idle=()=>Date.now()-lastTouch>1200;
const pretty=s=>s.replace(/-/g,' ').replace(/\\b\\w/g,c=>c.toUpperCase());
const hue=s=>{let h=0;for(const c of s)h=(h*31+c.charCodeAt(0))%360;return h};
async function api(p){const r=await fetch(p);return r.json().catch(()=>({}))}
function toast(m){const t=$('toast');t.textContent=m;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),1800)}

function buildTiles(stations){
  stations.forEach(s=>{
    const b=document.createElement('button');b.className='tile';b.id='st-'+s.id;
    const h=hue(s.id);
    b.style.background=`linear-gradient(135deg,hsl(${h} 45% 30%),hsl(${h} 55% 16%))`;
    b.innerHTML=`<span class="badge">▶ PLAYING</span><span class="nm">${pretty(s.id)}</span>
                 <span class="nt">${s.note||'Internet radio'}</span>`;
    b.onclick=async()=>{if(busy)return;busy=true;touch();
      b.querySelector('.nm').textContent='Starting…';
      await api('/api/play/'+encodeURIComponent(s.id)+'?volume='+$('vol').value);
      b.querySelector('.nm').textContent=pretty(s.id);busy=false;toast(pretty(s.id)+' on all speakers');refresh()};
    $('grid').appendChild(b)});
}
function buildSpeakers(speakers){
  speakers.forEach(s=>{
    const r=document.createElement('div');r.className='srow';r.id='sp-'+s.ip;
    r.innerHTML=`<span class="st"></span><span class="nm">${s.name.replace('ST - ','').replace('ST ','')}</span>
      <input type="range" min="0" max="70" value="${s.volume??30}">`;
    const sl=r.querySelector('input');
    sl.oninput=()=>{touch();clearTimeout(spkT[s.ip]);
      spkT[s.ip]=setTimeout(()=>api('/api/volume/'+sl.value+'?ip='+s.ip),350)};
    $('spkList').appendChild(r)});
}
function render(st){
  $('conn').classList.remove('off');$('connTxt').textContent=
    st.speakers.filter(s=>s.playStatus=='PLAY_STATE').length+'/'+st.speakers.length+' playing';
  if($('grid').childElementCount===0)buildTiles(st.stations);
  if($('spkList').childElementCount===0)buildSpeakers(st.speakers);
  const live=!!st.station&&st.speakers.some(s=>s.playStatus=='PLAY_STATE');
  $('hero').classList.toggle('live',live);
  $('hTitle').textContent=live?pretty(st.station):'Off';
  const note=(st.stations.find(x=>x.id===st.station)||{}).note;
  $('hSub').textContent=live?(note||'Playing on all speakers'):'Tap a station to play everywhere';
  st.stations.forEach(s=>$('st-'+s.id).classList.toggle('playing',live&&s.id===st.station));
  st.speakers.forEach(s=>{const r=$('sp-'+s.ip);if(!r)return;
    r.querySelector('.st').classList.toggle('on',s.playStatus=='PLAY_STATE');
    if(idle()&&s.volume!=null)r.querySelector('input').value=s.volume});
  if(idle()){const v=st.speakers.find(s=>s.volume!=null);
    if(v){$('vol').value=v.volume;$('vv').textContent=v.volume}}
}
async function refresh(){try{render(await api('/api/status'))}
  catch(e){$('conn').classList.add('off');$('connTxt').textContent='offline'}}
function setGroupVol(v){v=Math.max(0,Math.min(70,v));$('vol').value=v;$('vv').textContent=v;touch();
  clearTimeout(volT);volT=setTimeout(()=>api('/api/volume/'+v),350)}
$('vol').oninput=()=>setGroupVol(+$('vol').value);
$('vdn').onclick=()=>setGroupVol(+$('vol').value-5);
$('vup').onclick=()=>setGroupVol(+$('vol').value+5);
$('spkToggle').onclick=()=>{const o=$('spkList').classList.toggle('open');
  $('spkToggle').textContent=o?'Speakers ▾':'Speakers ▸'};
$('stop').onclick=async()=>{if(busy)return;busy=true;touch();await api('/api/stop');busy=false;toast('Speakers off');refresh()};
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
        self.end_headers(); self.wfile.write(body)

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
        m = re.match(r"/api/play/(.+)$", path)
        if m:
            station = urllib.parse.unquote(m.group(1))
            vol = int(qs["volume"][0]) if qs.get("volume") else None
            try:
                master = api_play(cfg, station, vol)
                return self._json({"ok": True, "station": station, "master": master})
            except SystemExit as e:           # unknown station / wedged renderer
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
                api_stop_all()                # everything off, regardless of zone shape
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
    print(f"Proxy serving on http://{host}:{port}/stream/<station>  (Ctrl-C to stop)")
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
    import urllib.parse  # noqa
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
