"""Découverte ponctuelle SSDP et ancienne découverte mDNS."""
import socket, time, subprocess, re, http.client, ipaddress, threading
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from . import config

_DISCOVERY_LOCK = threading.Lock()

def speaker_info(ip):
    """Read identity only, directly from a LAN IPv4, without redirects or proxies."""
    try:
        if not isinstance(ip, str): raise ValueError()
        address = ipaddress.IPv4Address(ip)
        if (not address.is_private or address.is_loopback or address.is_multicast
                or address.is_unspecified or address.is_reserved):
            raise ValueError()
    except ValueError:
        raise ValueError("Adresse IPv4 locale invalide.") from None
    connection = http.client.HTTPConnection(str(address), 8090, timeout=1.5)
    try:
        connection.request("GET", "/info")
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("L’enceinte ne répond pas à la demande d’informations.")
        body = response.read(65537)
        if len(body) > 65536: raise ValueError("Réponse de l’enceinte trop volumineuse.")
        root = ET.fromstring(body)
        device_id = root.get("deviceID", "").upper()
        name = (root.findtext("name") or "").strip()
        model = (root.findtext("type") or "").strip()
        if root.tag != "info" or not re.fullmatch(r"[0-9A-F]{12}", device_id) or not name:
            raise ValueError("Cet appareil ne fournit pas une identité SoundTouch valide.")
        return {"name": name, "ip": str(address), "deviceID": device_id, "model": model}
    except (OSError, http.client.HTTPException, ET.ParseError):
        raise ValueError("Impossible de lire l’enceinte. Vérifiez son IP et son accès au réseau.") from None
    finally:
        connection.close()


def scan_speakers(timeout=4):
    """Bounded, on-demand SSDP search. Never writes configuration or controls playback."""
    if not _DISCOVERY_LOCK.acquire(blocking=False):
        raise ValueError("Une recherche est déjà en cours. Réessayez dans quelques secondes.")
    try:
        candidates = set()
        message = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
                   'MAN: "ssdp:discover"\r\nMX: 2\r\nST: ssdp:all\r\n\r\n').encode()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            sock.bind(("", 0))
            sock.sendto(message, ("239.255.255.250", 1900))
            deadline = time.monotonic() + timeout
            while len(candidates) < 32:
                remaining = deadline - time.monotonic()
                if remaining <= 0: break
                sock.settimeout(remaining)
                try: packet, sender = sock.recvfrom(8192)
                except socket.timeout: break
                # Only contact the sender, never a URL advertised by an arbitrary device.
                if packet.upper().startswith(b"HTTP/1.1 200"):
                    candidates.add(sender[0])
        def identify(ip):
            try: return speaker_info(ip)
            except ValueError: return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            found = [speaker for speaker in pool.map(identify, sorted(candidates)) if speaker]
        unique = {}
        for speaker in found: unique.setdefault(speaker["deviceID"], speaker)
        return sorted(unique.values(), key=lambda speaker: speaker["name"].casefold())
    finally:
        _DISCOVERY_LOCK.release()


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
    if found: config.save_json(config.SPEAKERS_PATH, found)
    return found
