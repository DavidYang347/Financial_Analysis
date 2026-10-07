"""python -m fundamentals fetch <dataset|all> [--budget 150] [--workers 3] [--refresh] [--part i/n]
   python -m fundamentals build
   python -m fundamentals status
"""
from __future__ import annotations

import argparse
import sys

from fundamentals import fetch as F


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fundamentals")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download missing raw data (resumable)")
    f.add_argument("dataset", choices=F.DATASETS + ["all"])
    f.add_argument("--budget", type=float, default=150.0, help="stop starting new requests after N seconds")
    f.add_argument("--workers", type=int, default=3)
    f.add_argument("--refresh", action="store_true", help="also re-download the most recent units")
    f.add_argument("--part", help="i/n: handle only every n-th unit starting at i (to run several processes)")
    sub.add_parser("build", help="merge raw downloads into the point-in-time tables")
    sub.add_parser("status", help="show how much is downloaded")
    a = ap.parse_args(argv)

    if a.cmd == "fetch":
        part = tuple(int(x) for x in a.part.split("/")) if a.part else None
        names = F.DATASETS if a.dataset == "all" else [a.dataset]
        left = 0
        for n in names:
            left += F.fetch(n, a.budget, a.workers, a.refresh, part)["remaining"]
        return 2 if left else 0  # exit code 2 = run again
    if a.cmd == "build":
        from fundamentals import build
        build.build_all()
        return 0
    from fundamentals import build
    build.status()
    return 0


if __name__ == "__main__":
    sys.exit(main())
