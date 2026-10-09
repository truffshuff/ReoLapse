import argparse
import asyncio
import json
import sys
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
    sp.add_argument("--camera-settings", default="",
                    help='per-camera overrides as JSON, e.g. {"Porch Left": {"imgsz": 960}}')
    sp.add_argument("--done-since", type=float, default=None,
                    help="skip cameras already analyzed with the same settings after this time (epoch seconds); "
                         "the job queue passes its job's creation time so a resumed job doesn't redo finished cameras")


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
    sp.add_argument("--clock", choices=["even", "adaptive"], default="even",
                    help="even: the clock runs at one rate all day; adaptive: busy times get more of the video")
    sp.add_argument("--second-clock", action="store_true",
                    help="also render the first style with the other clock, so the Player can switch")


def main() -> None:
    p = argparse.ArgumentParser(prog="reolapse", description="Daily motion synopsis videos from a Reolink NVR")
    sub = p.add_subparsers(dest="cmd", required=True)

    _common(sub.add_parser("probe", help="check NVR access and what a day's events look like"))
    pr = sub.add_parser("prune", help="set aside objects that never really moved (for days analyzed before this check)")
    _common(pr)
    cl = sub.add_parser("cleanup", help="delete what the retention settings allow (old footage, results, leftovers)")
    cl.add_argument("--dry-run", action="store_true", help="only list what would be deleted")
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
    if args.cmd == "cleanup":
        from . import cleanup
        from .settings import load

        p = cleanup.plan(load()["retention"])
        for i in p.items:
            print(f"   {'would delete' if args.dry_run else 'delete'} {i.path}  ({i.bytes / 1e9:.2f} GB, {i.reason})")
        if args.dry_run:
            print(f"{len(p.items)} item(s), {p.bytes / 1e9:.1f} GB would be freed")
        else:
            print(f"freed {cleanup.execute(p) / 1e9:.1f} GB")
        return
    cameras = args.camera or DEFAULT_CAMERAS
    day = _day(args.day)
    if args.cmd == "probe":
        from . import probe
        asyncio.run(probe.run(cameras, day))
        return
    if args.cmd == "prune":
        from .config import DayPaths
        from .tube_index import prune

        for cam in cameras:
            paths = DayPaths(cam, day)
            kept, gone = prune(paths.tubes)
            print(f"{cam} {day}: kept {len(kept)} objects, set aside {gone} that never really moved"
                  + (f" (in {paths.tubes / '_pruned'})" if gone else ""))
        return
    if args.cmd in ("fetch", "run"):
        from . import fetch
        asyncio.run(fetch.run(cameras, day, args.stream))
    if args.cmd in ("analyze", "run"):
        from . import analyze
        try:
            analyze.run(cameras, day, fps=args.fps, model=args.model, imgsz=args.imgsz,
                        types=set(args.types.split(",")), skip_bg=args.skip_bg,
                        hours=tuple(float(x) for x in args.hours.split("-")), done_since=args.done_since,
                        per_camera=json.loads(args.camera_settings) if args.camera_settings else None)
        except analyze.MemoryRestart:
            sys.exit(analyze.EXIT_MEMORY_RESTART)
    if args.cmd in ("render", "run"):
        from . import render
        render.run(cameras, day, styles=args.style.split(","), color=args.color, length=args.length,
                   density=args.density, speed=args.speed, max_tube=args.max_tube, fps=args.out_fps,
                   labels=not args.no_labels, only=set(args.only.split(",")) if args.only else None,
                   per_type=[k for k in args.per_type.split(",") if k],
                   stills=not args.no_stills, video=not args.no_video, drift_min=args.drift, clock_mode=args.clock,
                   second_clock=args.second_clock)
