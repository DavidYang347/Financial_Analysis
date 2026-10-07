"""python -m fundlab fetch [--budget SEC] [--only notices_all,delisted]  |  build  |  status"""
from __future__ import annotations

import argparse
import json
import logging


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m fundlab")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="下载 / 增量更新原始基本面和公告数据（可断点续传）")
    f.add_argument("--budget", type=float, default=None, help="最多运行多少秒，到时安全退出，再次运行会继续")
    f.add_argument("--only", default="", help="只跑某几步: snapshots,periodic,unlock,pledge,delisted,abstract,notices,notices_more,notices_all")
    sub.add_parser("build", help="由原始数据重建时点表 data/lake/fundlab/")
    sub.add_parser("status", help="查看各表的行数和时间范围")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if a.cmd == "fetch":
        from fundlab.fetch import fetch_all
        only = [s.strip() for s in a.only.split(",") if s.strip()] or None
        print(json.dumps(fetch_all(a.budget, only=only), ensure_ascii=False, indent=1, default=str))
    elif a.cmd == "build":
        from fundlab.build import build_all
        print(json.dumps(build_all(), ensure_ascii=False, indent=1, default=str))
    else:
        from fundlab.store import FundStore
        print(json.dumps(FundStore().status(), ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
