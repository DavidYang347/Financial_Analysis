"""Where review statistics and report texts live.

    data/lake/reviews/{monthly,weekly}/<label>.json      statistics computed by review.stats
    materials/复盘报告/月度复盘/<YYYY-MM>.md               report text (written per the SOP)
    materials/复盘报告/周度复盘/<YYYY-Www>.md
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from data import config

STATS = config.LAKE_DIR / "reviews"
REPORTS = config.PROJECT_ROOT / "materials" / "复盘报告"
KIND_DIR = {"monthly": "月度复盘", "weekly": "周度复盘"}
LABEL_RE = {"monthly": re.compile(r"^\d{4}-\d{2}$"), "weekly": re.compile(r"^\d{4}-W\d{2}$")}


def check(kind: str, label: str) -> None:
    if kind not in KIND_DIR or not LABEL_RE[kind].match(label or ""):
        raise ValueError(f"invalid review {kind}/{label}")


def stats_path(kind: str, label: str) -> Path:
    check(kind, label)
    return STATS / kind / f"{label}.json"


def report_path(kind: str, label: str) -> Path:
    check(kind, label)
    return REPORTS / KIND_DIR[kind] / f"{label}.md"


def save_stats(kind: str, label: str, data: dict) -> None:
    p = stats_path(kind, label)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(p)


def load_stats(kind: str, label: str) -> dict | None:
    p = stats_path(kind, label)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def load_research(label: str) -> dict:
    """Web-verified catalysts per symbol (data/lake/reviews/research/<YYYY-MM>.json), if present."""
    p = STATS / "research" / f"{label}.json"
    if not p.exists():
        return {}
    return {r["symbol"]: r for r in json.loads(p.read_text(encoding="utf-8"))}


def load_report(kind: str, label: str) -> str | None:
    p = report_path(kind, label)
    return p.read_text(encoding="utf-8") if p.exists() else None


def list_reviews() -> dict:
    out = {}
    for kind in KIND_DIR:
        labels = set()
        d = STATS / kind
        if d.exists():
            labels |= {p.stem for p in d.glob("*.json")}
        r = REPORTS / KIND_DIR[kind]
        if r.exists():
            labels |= {p.stem for p in r.glob("*.md") if LABEL_RE[kind].match(p.stem)}
        items = []
        for lb in sorted(labels, reverse=True):
            s = load_stats(kind, lb) if (STATS / kind / f"{lb}.json").exists() else None
            items.append({"label": lb, "has_stats": s is not None, "has_report": report_path(kind, lb).exists(),
                          "start": s.get("start") if s else None, "end": s.get("end") if s else None,
                          "summary": s.get("summary") if s else None})
        out[kind] = items
    return out
