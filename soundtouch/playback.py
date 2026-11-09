"""Sélection, commandes de lecture et coordination des groupes."""
import sys, os, socket, re, time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from . import config, device, discovery


def resolve_proxy_host(cfg, a_speaker_ip=None):
    host = cfg.get("proxy_host", "auto")
    if host and host != "auto":
        return host
    if a_speaker_ip is None:   # probe address: any known speaker (route selection only)
        sp = config.load_json(config.SPEAKERS_PATH, {})
        a_speaker_ip = next((d["ip"] for d in sp.values() if d.get("ip")), "192.168.1.1")
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((a_speaker_ip, 9)); return s.getsockname()[0]
    finally:
        s.close()


def speakers():
    sp = config.load_json(config.SPEAKERS_PATH, {})
    if not sp:
        print("No speakers cached; running discovery…", file=sys.stderr)
        sp = discovery.discover_speakers()
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


def build_zone(master_ip):
    """Group ALL known speakers into one zone under master_ip."""
    sp = config.load_json(config.SPEAKERS_PATH, {})
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
    q = cfg.get("master") or next(iter(config.load_json(config.SPEAKERS_PATH, {})), None)
    if not q:
        raise RuntimeError("no speakers known — run: ./bose_radio.py discover")
    _, default_ip = find_speaker(q)
    return device.zone_master_ip(default_ip)


def api_target_ip(target):
    """Only configured speakers may be selected by the web API."""
    sp = config.load_json(config.SPEAKERS_PATH, {})
    for name, d in sp.items():
        if target in (name, d.get("ip")):
            return d["ip"]
    raise ValueError(f"Unknown speaker: {target}")


def detach_speaker(ip):
    """Detach a member, or dissolve the zone when selecting its master."""
    master = device.get_zone_master(ip)
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
    if station_id not in config.load_json(config.STATIONS_PATH, {}):
        raise ValueError(f"Unknown station: {station_id}")
    if not group:
        detach_speaker(master)
    device.force_invalid(master)
    time.sleep(1.5)
    play(cfg, station_id, master, volume)
    if group:
        time.sleep(1.5)
        build_zone(master)
    if group and volume is not None:
        for d in config.load_json(config.SPEAKERS_PATH, {}).values():
            if d.get("ip") and d["ip"] != master:
                device.set_volume(d["ip"], volume)
    return master


def api_stop_target(target):
    ip = api_target_ip(target)
    detach_speaker(ip)
    device.standby(ip)
    if config.load_json(config.STATE_PATH, {}).get("master_ip") == ip:
        try: os.remove(config.STATE_PATH)
        except OSError: pass


def api_stop_all():
    """The app's Stop/Off semantics: EVERYTHING off, regardless of which speaker
    is currently the zone master (a physical button press makes the pressed
    speaker the master, so it can be any of them)."""
    sp = list(config.load_json(config.SPEAKERS_PATH, {}).values())
    masters = set()
    for d in sp:                       # find actual zone master(s) first
        m = device.get_zone_master(d.get("ip", ""))
        if m: masters.add(m)
    for m in masters:                  # master standby cascades to the zone
        device.standby(m)
    if masters:
        time.sleep(2)
    for d in sp:                       # sweep stragglers (solo players, awake members)
        try:
            src, _, _ = device.now_playing_fields(d["ip"])
        except Exception:
            continue
        if src != "STANDBY":
            device.standby(d["ip"])
    try: os.remove(config.STATE_PATH)         # watchdog must not resurrect
    except OSError: pass


def api_status(cfg):
    stations = [{"id": k, "note": (v.get("note", "") if isinstance(v, dict) else "")}
                for k, v in config.load_json(config.STATIONS_PATH, {}).items()]
    state = config.load_json(config.STATE_PATH, {}) or {}
    out = {"station": state.get("station"),
           "stations": stations, "speakers": [],
           "presets": config.load_json(config.PRESETS_PATH, {}),
           "default_speaker": cfg.get("master") or next(iter(config.load_json(config.SPEAKERS_PATH, {})), None)}
    for name, d in config.load_json(config.SPEAKERS_PATH, {}).items():
        try:
            src, ps, title = device.now_playing_fields(d["ip"])
        except Exception:
            src, ps, title = "unreachable", "", ""
        station = title if any(s["id"] == title for s in stations) else None
        if not station and d["ip"] == state.get("master_ip") and src == "UPNP":
            station = state.get("station")
        out["speakers"].append({"name": name, "ip": d["ip"], "source": src,
                                "playStatus": ps, "volume": device.get_volume(d["ip"]),
                                "station": station, "title": title})
    return out


def play(cfg, station_id, speaker_q, volume=None):
    stations = config.load_json(config.STATIONS_PATH, {})
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
    if volume is not None: device.set_volume(ip, volume)
    device.soap(ip, "SetAVTransportURI",
         '<u:SetAVTransportURI xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
         f"<InstanceID>0</InstanceID><CurrentURI>{proxy_url}</CurrentURI>"
         f"<CurrentURIMetaData>{didl}</CurrentURIMetaData></u:SetAVTransportURI>")
    device.soap(ip, "Play",
         '<u:Play xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
         "<InstanceID>0</InstanceID><Speed>1</Speed></u:Play>")
    print(f"▶  '{station_id}' -> {name} ({ip})   via {proxy_url}")
    # record intended playback so the watchdog (in `serve`) can self-heal stalls
    vol_record = volume if volume is not None else device.get_volume(ip)
    config.save_json(config.STATE_PATH, {"station": station_id, "master_name": name,
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
        device.soap(ip, "Stop", '<u:Stop xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">'
                         "<InstanceID>0</InstanceID></u:Stop>")
    except Exception: pass
    if standby: device.st_key(ip, "POWER")
    # deliberate stop: clear intended state so the watchdog won't resurrect it
    try: os.remove(config.STATE_PATH)
    except OSError: pass
    print(f"■  stopped {name} ({ip})")
