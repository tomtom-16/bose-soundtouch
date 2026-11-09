"""Commandes et affichages en ligne de commande."""
import sys, argparse
from . import config, device, discovery, playback, server


def main():
    # Windows consoles default to cp1252 which can't print ▶/■ — force UTF-8.
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            try: _s.reconfigure(encoding="utf-8", errors="replace")
            except Exception: pass

    cfg = config.get_config()
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
        sp = discovery.discover_speakers()
        for n, d in sp.items(): print(f"  {n:14s} {d['ip']:15s} {d.get('model','')}")
        if not sp: print("  (none found)")
    elif args.cmd == "list":
        print("Speakers:")
        for n, d in playback.speakers().items(): print(f"  {n:14s} {d['ip']:15s} {d.get('model','')}")
        print("Stations:")
        for k, v in config.load_json(config.STATIONS_PATH, {}).items():
            url = v["url"] if isinstance(v, dict) else v
            print(f"  {k:24s} {url}")
        print(f"Proxy: host={playback.resolve_proxy_host(cfg)} port={cfg['proxy_port']}")
    elif args.cmd == "serve":
        server.serve(cfg)
    elif args.cmd == "play":
        try:
            playback.play(cfg, args.station, args.speaker, args.volume)
        except device.RendererWedged as e:
            raise SystemExit(str(e))
    elif args.cmd == "stop":
        playback.stop(args.speaker)
    elif args.cmd == "status":
        playback.status(args.speaker)
    else:
        ap.print_help()
