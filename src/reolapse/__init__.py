import argparse
import asyncio
from datetime import date, timedelta

from .config import DEFAULT_CAMERAS


def _day(s: str | None) -> date:
    return date.fromisoformat(s) if s else date.today() - timedelta(days=1)


def _common(sp):
    sp.add_argument("--camera", action="append", help="camera name (repeatable); default: the fixed cams")
    sp.add_argument("--day", help="YYYY-MM-DD (default: yesterday)")


def _fetch_opts(sp):
    sp.add_argument("--stream", choices=["sub", "main"], default=None,
                    help="sub = Fluent, main = Clear (default: each camera's setting, else sub)")


def _analyze_opts(sp):
    sp.add_argument("--fps", type=float, default=5.0, help="analysis frame rate (default 5)")
    sp.add_argument("--model", default="yolo26n-seg.pt", help="YOLO segmentation weights in models/")
    sp.add_argument("--imgsz", type=int, default=1280)
    sp.add_argument("--types", default="all",
                    help="NVR event types that define where to look (comma list), or 'all' to scan the whole day")
    sp.add_argument("--skip-bg", action="store_true", help="reuse existing backgrounds")
    sp.add_argument("--hours", default="0-24", help="only scan events in this hour range, e.g. 7-9")


def _render_opts(sp):
    sp.add_argument("--style", default="cutout", help="comma list of cutout,ghost,outline (one video per style)")
    sp.add_argument("--color", choices=["category", "time"], default="category",
                    help="label/outline colour: by object type or by time of day")
    sp.add_argument("--length", type=float, help="target video length in seconds (default: auto from density)")
    sp.add_argument("--density", type=float, default=6.0, help="auto length: average objects on screen")
    sp.add_argument("--speed", type=float, default=2.0, help="object playback speed vs real time")
    sp.add_argument("--max-tube", type=float, default=20.0, help="longest any object stays on screen (s)")
    sp.add_argument("--out-fps", type=float, default=20.0)
    sp.add_argument("--only", help="render only these types (comma list of person,vehicle,animal)")
    sp.add_argument("--per-type", default="",
                    help="also render a video of only each of these types, in the first style (comma list)")
    sp.add_argument("--no-labels", action="store_true")
    sp.add_argument("--no-stills", action="store_true")
    sp.add_argument("--no-video", action="store_true", help="stills only")
    sp.add_argument("--drift", type=float, default=10.0,
                    help="max minutes an object may be shifted from its true time to avoid overlaps (0 = never)")


def main() -> None:
    p = argparse.ArgumentParser(prog="reolapse", description="Daily motion synopsis videos from a Reolink NVR")
    sub = p.add_subparsers(dest="cmd", required=True)

    _common(sub.add_parser("probe", help="check NVR access and what a day's events look like"))
    sv = sub.add_parser("serve", help="run the local API server for the desktop app")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8765)
    for name, help_, opts in [
        ("fetch", "download a day of recordings and the NVR event list", [_fetch_opts]),
        ("analyze", "build backgrounds and moving-object tubes from fetched footage", [_analyze_opts]),
        ("render", "render synopsis video(s) and stills from analysed tubes", [_render_opts]),
        ("run", "fetch + analyze + render", [_fetch_opts, _analyze_opts, _render_opts]),
    ]:
        sp = sub.add_parser(name, help=help_)
        _common(sp)
        for o in opts:
            o(sp)

    args = p.parse_args()
    if args.cmd == "serve":
        from .server import serve
        serve(args.host, args.port)
        return
    cameras = args.camera or DEFAULT_CAMERAS
    day = _day(args.day)
    if args.cmd == "probe":
        from . import probe
        asyncio.run(probe.run(cameras, day))
        return
    if args.cmd in ("fetch", "run"):
        from . import fetch
        asyncio.run(fetch.run(cameras, day, args.stream))
    if args.cmd in ("analyze", "run"):
        from . import analyze
        analyze.run(cameras, day, fps=args.fps, model=args.model, imgsz=args.imgsz,
                    types=set(args.types.split(",")), skip_bg=args.skip_bg,
                    hours=tuple(float(x) for x in args.hours.split("-")))
    if args.cmd in ("render", "run"):
        from . import render
        render.run(cameras, day, styles=args.style.split(","), color=args.color, length=args.length,
                   density=args.density, speed=args.speed, max_tube=args.max_tube, fps=args.out_fps,
                   labels=not args.no_labels, only=set(args.only.split(",")) if args.only else None,
                   per_type=[k for k in args.per_type.split(",") if k],
                   stills=not args.no_stills, video=not args.no_video, drift_min=args.drift)
