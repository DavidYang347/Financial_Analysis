"""Symbol normalization between our canonical form and each data source.

Canonical form: ``600000.SH`` / ``000001.SZ`` / ``920002.BJ``.
"""
from __future__ import annotations

import re

SH_PREFIXES = ("600", "601", "603", "605", "688", "689")
SZ_PREFIXES = ("000", "001", "002", "003", "300", "301", "302")
# 920xxx is the current BSE code range; 43/83/87/88 are legacy BSE/NEEQ codes.
BJ_PREFIXES = ("920", "43", "83", "87", "88")

_PATTERN = re.compile(r"^(?:(SH|SZ|BJ)[.]?)?(\d{6})(?:[.](SH|SZ|BJ|SS))?$")


def infer_exchange(code: str) -> str:
    if code.startswith(SH_PREFIXES):
        return "SH"
    if code.startswith(SZ_PREFIXES):
        return "SZ"
    if code.startswith(BJ_PREFIXES):
        return "BJ"
    raise ValueError(f"cannot infer exchange for code {code!r}")


def normalize(symbol: str) -> str:
    """Accepts 600000 / sh600000 / SH.600000 / sh.600000 / 600000.SH / 600000.SS."""
    s = str(symbol).strip().upper()
    m = _PATTERN.match(s)
    if not m:
        raise ValueError(f"invalid A-share symbol: {symbol!r}")
    prefix_ex, code, suffix_ex = m.groups()
    ex = prefix_ex or suffix_ex
    if ex == "SS":
        ex = "SH"
    if ex is None:
        ex = infer_exchange(code)
    return f"{code}.{ex}"


def split(symbol: str) -> tuple[str, str]:
    code, ex = normalize(symbol).split(".")
    return code, ex


def is_a_share(symbol: str) -> bool:
    try:
        code, ex = split(symbol)
    except ValueError:
        return False
    # BSE: only the current 920xxx range. Legacy 43/83/87 codes were all
    # migrated to 920xxx (history carried over), so counting both would duplicate.
    prefixes = {"SH": SH_PREFIXES, "SZ": SZ_PREFIXES, "BJ": ("920",)}[ex]
    return code.startswith(prefixes)


def board(symbol: str) -> str:
    code, ex = split(symbol)
    if ex == "BJ":
        return "北交所"
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("300", "301", "302")):
        return "创业板"
    return "主板"


# ---- per-source formats -------------------------------------------------

def to_tencent(symbol: str) -> str:
    code, ex = split(symbol)
    return f"{ex.lower()}{code}"


def to_sina(symbol: str) -> str:
    return to_tencent(symbol)


def to_baostock(symbol: str) -> str:
    code, ex = split(symbol)
    return f"{ex.lower()}.{code}"


def to_tdx(symbol: str) -> tuple[int, str]:
    """(market, code) for the TDX protocol: 0=SZ, 1=SH, 2=BJ."""
    code, ex = split(symbol)
    return {"SZ": 0, "SH": 1, "BJ": 2}[ex], code


def to_eastmoney_secid(symbol: str) -> str:
    code, ex = split(symbol)
    return f"{1 if ex == 'SH' else 0}.{code}"


def to_tushare(symbol: str) -> str:
    return normalize(symbol)
