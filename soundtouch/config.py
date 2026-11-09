"""Configuration JSON, validations et écritures atomiques."""
import os, json, re, threading, ipaddress, tempfile, hashlib, uuid
import urllib.parse

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.abspath(os.environ.get("BOSE_CONFIG_DIR", os.path.join(HERE, "config")))
DATA_DIR = os.path.abspath(os.environ.get("BOSE_DATA_DIR", os.path.join(HERE, "data")))
CONFIG_PATH   = os.path.join(CONFIG_DIR, "config.json")
STATIONS_PATH = os.path.join(CONFIG_DIR, "stations.json")
SPEAKERS_PATH = os.path.join(CONFIG_DIR, "speakers.json")
PRESETS_PATH  = os.path.join(CONFIG_DIR, "presets.json")
STATE_PATH    = os.path.join(DATA_DIR, "state.json")   # last intended playback (for watchdog)

_CONFIG_LOCK = threading.RLock()

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


def speaker_snapshot():
    """Caller holds the configuration lock."""
    speakers_config = load_json(SPEAKERS_PATH, {})
    revision = hashlib.sha256(json.dumps([
        speakers_config, load_json(PRESETS_PATH, {}), load_json(CONFIG_PATH, {})
    ], sort_keys=True).encode()).hexdigest()
    return {"speakers": speakers_config, "revision": revision}


def configure_speakers(rows, revision=None):
    """Validate the complete list before writing; retain metadata and renamed presets."""
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError("Liste d'enceintes invalide (100 maximum).")
    with _CONFIG_LOCK:
        if revision is not None and revision != speaker_snapshot()["revision"]:
            raise ValueError("Configuration modifiée : fermez puis rouvrez les réglages avant de confirmer à nouveau.")
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
        for name in result:
            updated_presets.setdefault(name, {})
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
        result = load_json(PRESETS_PATH, {})
        for scope, mapping in changes.items():
            if scope != "default" and scope not in speakers_config:
                raise ValueError("Enceinte inconnue ou renommée : " + scope)
            if not isinstance(mapping, dict):
                raise ValueError("Liste de presets invalide.")
            for number, station in mapping.items():
                if number not in ("1", "2", "3", "4", "5", "6"):
                    raise ValueError("Les presets doivent être numérotés de 1 à 6.")
                if station is None and scope != "default":
                    continue  # Explicitly leave this speaker button unassigned.
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
            mapping = presets.get(name, {})
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
