"""Parameter declarations for screener scripts.

A script lists its tunable inputs in ``PARAMS``; the web page builds a form
from them and the runner validates values before calling ``screen()``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Literal

ParamType = Literal["int", "float", "bool", "select", "multiselect", "date", "str"]


class ParamError(ValueError):
    pass


@dataclass
class Param:
    key: str
    label: str
    type: ParamType
    default: Any
    help: str = ""
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: list[dict] | None = None  # [{"value": ..., "label": ...}] for select/multiselect
    unit: str = ""
    group: str = ""  # optional heading the form groups params under

    def to_dict(self) -> dict:
        d = asdict(self)
        if isinstance(d["default"], date):
            d["default"] = d["default"].isoformat()
        return d

    def coerce(self, value: Any) -> Any:
        if value is None or value == "":
            return self.default
        t = self.type
        try:
            if t == "int":
                if isinstance(value, bool) or float(value) != int(float(value)):
                    raise ParamError(f"{self.label} 需要整数")
                v: Any = int(float(value))
            elif t == "float":
                v = float(value)
            elif t == "bool":
                if isinstance(value, str):
                    v = value.strip().lower() in ("1", "true", "yes", "on")
                else:
                    v = bool(value)
            elif t == "date":
                v = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
            elif t == "select":
                allowed = [o["value"] for o in self.options or []]
                if value not in allowed:
                    raise ParamError(f"{self.label} 只能是 {allowed}")
                v = value
            elif t == "multiselect":
                allowed = [o["value"] for o in self.options or []]
                v = list(value) if isinstance(value, (list, tuple)) else [value]
                bad = [x for x in v if x not in allowed]
                if bad:
                    raise ParamError(f"{self.label} 包含无效选项 {bad}")
            else:
                v = str(value)
        except (TypeError, ValueError) as e:
            if isinstance(e, ParamError):
                raise
            raise ParamError(f"{self.label} 的值 {value!r} 无效") from e
        if t in ("int", "float"):
            if self.min is not None and v < self.min:
                raise ParamError(f"{self.label} 不能小于 {self.min}")
            if self.max is not None and v > self.max:
                raise ParamError(f"{self.label} 不能大于 {self.max}")
        return v


def opt(value: Any, label: str | None = None) -> dict:
    """Shorthand for a select option."""
    return {"value": value, "label": label if label is not None else str(value)}


def resolve_params(declared: list[Param], given: dict | None) -> dict:
    given = dict(given or {})
    known = {p.key for p in declared}
    unknown = sorted(set(given) - known)
    if unknown:
        raise ParamError(f"未知参数: {', '.join(unknown)}")
    return {p.key: p.coerce(given.get(p.key)) for p in declared}


# Parameters most screeners want. Scripts can include them with ``*COMMON_FILTERS``.
COMMON_FILTERS = [
    Param("exclude_st", "排除 ST / *ST", "bool", True, group="股票池"),
    Param("exclude_bj", "排除北交所", "bool", False, group="股票池"),
    Param("boards", "板块", "multiselect", ["主板", "创业板", "科创板", "北交所"],
          options=[opt("主板"), opt("创业板"), opt("科创板"), opt("北交所")], group="股票池"),
    Param("min_list_days", "上市满", "int", 120, min=0, max=5000, unit="个交易日",
          help="剔除次新股，按交易日计", group="股票池"),
    Param("min_amount", "近 20 日日均成交额不低于", "float", 0.5, min=0, max=1000, step=0.1, unit="亿元",
          group="股票池"),
]
