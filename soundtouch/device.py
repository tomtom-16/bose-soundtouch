"""Primitives HTTP/XML et UPnP des enceintes."""
import re
import urllib.request
from . import config


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
        for d in config.load_json(config.SPEAKERS_PATH, {}).values():
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
