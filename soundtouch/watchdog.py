"""Surveillance des interruptions et reprise de la lecture."""
import sys, time
from . import config, device, playback, streaming


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
        st = config.load_json(config.STATE_PATH, None)
        if not st or not st.get("station") or not st.get("master_ip"):
            stalled = 0; continue
        master = device.zone_master_ip(st["master_ip"])
        try:
            src, ps, _ = device.now_playing_fields(master)
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
                        and not streaming._active_has(master))
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
            playback.play(cfg, st["station"], master, st.get("volume"))
            last_heal = time.time(); stalled = 0
        except (device.RendererWedged, SystemExit) as e:   # wedged renderer / unknown station
            log(f"heal could not complete: {e}")
        except Exception as e:
            log(f"heal error: {e}")
