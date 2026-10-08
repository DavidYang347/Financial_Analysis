"""python -m review fetch | monthly 2026-09 | weekly 2026-09-30 | list"""
from __future__ import annotations

import argparse
import json
import logging


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m review")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="下载指数、申万行业、涨跌停池、两融、宏观等外部数据")
    f.add_argument("--budget", type=float, default=150)
    m = sub.add_parser("monthly", help="计算月度复盘统计")
    m.add_argument("month", help="YYYY-MM")
    w = sub.add_parser("weekly", help="计算周度复盘统计（包含该日的那一周）")
    w.add_argument("day", help="YYYY-MM-DD")
    r = sub.add_parser("render", help="把报告草稿里的数据占位符替换成统计表，输出到 materials/复盘报告/")
    r.add_argument("kind", choices=["monthly", "weekly"])
    r.add_argument("label")
    sub.add_parser("list")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if a.cmd == "fetch":
        from review.fetch import fetch_all
        print(json.dumps(fetch_all(a.budget), ensure_ascii=False, default=str, indent=1))
    elif a.cmd == "monthly":
        from review.stats import monthly
        r = monthly(a.month)
        print(json.dumps(r["summary"], ensure_ascii=False, default=str, indent=1))
    elif a.cmd == "weekly":
        from review.stats import weekly
        r = weekly(a.day)
        print(json.dumps(r["summary"], ensure_ascii=False, default=str, indent=1))
    elif a.cmd == "render":
        from review.render import render
        print(render(a.kind, a.label))
    else:
        from review.store import list_reviews
        print(json.dumps(list_reviews(), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
