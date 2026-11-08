# bose-soundtouch — local internet radio for SoundTouch speakers (post-cloud)

Bose shut down the SoundTouch cloud in **May 2026**, killing TuneIn presets and
most streaming on these otherwise-fine speakers. This project brings internet
radio back — **no Bose account, no cloud, fully local** — including synchronized
multiroom, working physical preset buttons, a self-healing watchdog, and a
phone web app.

Built and tested on 3× **SoundTouch 10** (firmware 27.0.6). Other SoundTouch
models expose the same UPnP/HTTP APIs and will likely work — reports welcome.

## What still works (and what doesn't) after the shutdown

| Method | Status |
|---|---|
| Old TuneIn presets | ❌ Dead — `UNKNOWN_SOURCE_ERROR` |
| `LOCAL_INTERNET_RADIO` via `/select` | ❌ Rejected by ST-10 firmware (`error 1005`) |
| **UPnP/DLNA push (port 8091)** | ✅ **Works — this is what this tool uses** |
| Bluetooth / AUX | ✅ Still works (single speaker, no multiroom) |
| Official SoundTouch app | ❌ Cloud-dependent, dead |

## How it works

1. A small **proxy** (`bose_radio.py serve`, port 8077) runs on any always-on
   machine (Pi, NAS, mini-PC). The speakers' old firmware can't handle HTTPS,
   redirects, SHOUTcast `ICY 200 OK` responses, or playlist files — so the
   speaker pulls plain HTTP from the proxy, and the proxy deals with the messy
   upstream, including **live TuneIn token refresh** (stations configured as
   `tunein:sXXXXX` resolve a fresh stream URL on every play).
2. Playback is started by pushing `SetAVTransportURI` + `Play` to the speaker's
   UPnP AVTransport endpoint (`http://<speaker>:8091/AVTransport/Control`).
3. A **watchdog** thread inside `serve` self-heals dropouts (see below).
4. `button_listener.js` makes the **physical preset buttons** work again.
5. The proxy also serves a **phone web app** for control.

## Quick start

```bash
git clone https://github.com/fredgaiotti/bose-soundtouch
cd bose-soundtouch
./bose_radio.py discover          # find speakers (mDNS) -> speakers.json
./bose_radio.py list              # show speakers + stations
./bose_radio.py serve             # run the proxy (keep it running)
# in another shell:
./bose_radio.py play groove-salad "Living Room" --volume 20
./bose_radio.py status "Living Room"
./bose_radio.py stop "Living Room"
```

Speaker and station names match case-insensitively on substrings.
`discover` uses macOS `dns-sd`; on Linux/Windows, create `speakers.json` by
hand from `speakers.example.json` (get each speaker's `deviceID` from
`http://<speaker-ip>:8090/info`).

## Phone web app + HTTP API

Open `http://<proxy-host>:8077/` on your phone (add to home screen). Station
tiles, group volume with −/+ buttons, per-speaker volume sliders, stop, live
now-playing status.

- `GET /api/status` — station + per-speaker source/playStatus/volume (JSON)
- `GET /api/play/<station>?volume=N` — robust whole-house play
- `GET /api/volume/<n>` — group volume (`?ip=<speaker-ip>` for one speaker)
- `GET /api/stop` — everything off, regardless of zone shape

**No authentication** — same trust domain as the speakers' own unauthenticated
port 8090. Keep port 8077 LAN-only. **Never port-forward it to the internet.**
(Siri/Shortcuts tip: a "Get Contents of URL" shortcut to `/api/play/...` gives
voice control.)

## Stations (`stations.json`)

```json
{
  "newsradio": { "url": "tunein:s24939", "note": "Any TuneIn station id" },
  "myradio":   { "url": "http://direct.example.com/stream.mp3", "note": "Any direct stream" }
}
```

TuneIn IDs: search tunein.com — the station URL ends in `.../sXXXXX`.

## Physical preset buttons (`button_listener.js`)

The firmware refuses to store custom sources in presets, and the old presets
point at dead cloud services. Workaround: the listener watches each speaker's
WebSocket (port 8080, subprotocol `gabbo`), catches preset presses instantly,
and pushes the mapped station via UPnP. Map slots in `presets.json`. Requires
Node + `npm install ws`.

With `"sync": true` (default in `config.json`) a button press groups **all**
speakers into one synchronized zone, mastered by the pressed speaker.

## Self-healing watchdog

Streams die (upstream timeouts, host reboots) and the renderer handles it
badly. The watchdog (inside `serve`) checks the zone master every 15s and
re-pushes the last station when it sees either healable signature:

1. **Buffer-drain stall** — speaker claims `PLAY_STATE` but has no open
   connection to the proxy (it never reconnects on its own).
2. **`INVALID_SOURCE` with saved intent** — the renderer gave up entirely,
   e.g. after the proxy host rebooted. This auto-resumes playback ~30s after
   your server comes back.

It never resurrects a deliberate stop or a speaker in STANDBY. Tunables
(optional keys in `config.json`): `watchdog`, `watchdog_poll`,
`watchdog_grace`, `watchdog_cooldown`.

## Hard-won firmware quirks (read before hacking)

These cost real debugging time. ST-10, firmware 27.0.6:

- **A UPnP push only takes over from `INVALID_SOURCE`** — not from STANDBY,
  and not while another stream is actively playing. To switch stations
  programmatically, first `/select` a **bogus** TuneIn station (e.g.
  `s99999999`) so the speaker fast-fails to `INVALID_SOURCE`, then push.
  (The bogus id must not match any stored preset, or the button listener
  triggers itself in a loop.)
- **UPnP commands to a zone MEMBER return HTTP 500.** Always target the zone
  **master**. Powering the master to standby cascades to the whole group.
- **A member's `getZone` does not list the master's IP** in its `<member>`
  entries — only the master's deviceID and a `senderIPAddress` attribute.
  Resolve the master via your own deviceID table.
- **The POWER key is a toggle.** Sending it to a speaker already in standby
  wakes it up. Check `/now_playing` before "turning off".
- **Old SPOTIFY presets hang in a sticky `BUFFERING` state ~13s** and block
  UPnP takeover; dead TUNEIN presets fast-fail (good). If your preset slots
  hold dead Spotify entries, overwrite them via `/storePreset` with dummy
  TuneIn ids so button presses fail fast and the listener can take over.
- **Heavy rapid API churn wedges the UPnP renderer** (500s, or 200-but-silent,
  stuck `INVALID_SOURCE`). Only a physical power-cycle (~10s unplugged)
  recovers it. Be gentle; don't stress-test on speakers you love.
- Multiroom relay is efficient: the zone master makes **one** connection to
  the proxy and relays to members itself.

## Run it 24/7

The proxy host must stay awake — a Raspberry Pi, NAS, or mini-PC is ideal.
Ready-made service definitions in [`examples/`](examples/):

- **Linux/Pi**: `boseradio.service` (systemd)
- **macOS**: `com.example.boseradio.plist` (launchd)
- **Windows**: `setup_tasks.ps1` (scheduled tasks; note: use `npm.cmd` if
  PowerShell's execution policy blocks `npm.ps1`)

Set `proxy_host` in `config.json` to the host's LAN IP if `auto` picks the
wrong interface. If the host has a firewall, allow inbound TCP 8077 from your
LAN only.

## License

MIT
