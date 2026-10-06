"""Base class and shared helpers for market-data sources."""
from __future__ import annotations

import logging
import threading
import time
from abc import ABC
from datetime import date

import numpy as np
import pandas as pd
import requests

from data import config

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


class SourceError(Exception):
    """Transient failure (network, parse, throttling). Counts toward the circuit breaker."""


class SourceUnavailable(SourceError):
    """Permanent failure for this run (missing dependency, missing token, unsupported)."""


class DataSource(ABC):
    """A market-data provider.

    Every method returns data in the canonical schema (see ``data.schema``) or
    raises ``SourceError``. Returning an empty frame means "this source has no
    data for the request", which is different from an error.
    """

    name: str = "base"

    def check(self) -> None:
        """Raise SourceUnavailable if the source cannot be used at all."""

    def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        raise SourceUnavailable(f"{self.name} does not provide daily bars")

    def fetch_adj_events(self, symbol: str) -> pd.DataFrame:
        """Corporate actions with columns: date, cash, bonus, rights, rights_price (all per share)."""
        raise SourceUnavailable(f"{self.name} does not provide corporate actions")

    def fetch_adj_factor(self, symbol: str) -> pd.DataFrame:
        """Cumulative hfq factor change points with columns: date, adj_factor."""
        raise SourceUnavailable(f"{self.name} does not provide adjustment factors")

    def list_stocks(self) -> pd.DataFrame:
        raise SourceUnavailable(f"{self.name} does not provide a stock list")

    def trade_calendar(self) -> list[date]:
        raise SourceUnavailable(f"{self.name} does not provide a trading calendar")

    def close(self) -> None:
        pass

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name}>"


class HttpMixin:
    """Thread-local requests sessions with simple retry."""

    _local = threading.local()
    default_headers: dict[str, str] = {"User-Agent": UA}

    def _session(self) -> requests.Session:
        key = f"session_{id(self)}"
        s = getattr(self._local, key, None)
        if s is None:
            s = requests.Session()
            s.headers.update(self.default_headers)
            adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=16)
            s.mount("http://", adapter)
            s.mount("https://", adapter)
            setattr(self._local, key, s)
        return s

    def get_json(self, url: str, params: dict | None = None, retries: int = 2, **kw):
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                r = self._session().get(url, params=params, timeout=config.HTTP_TIMEOUT, **kw)
                r.raise_for_status()
                return r.json()
            except (requests.RequestException, ValueError) as e:
                last = e
                time.sleep(0.5 * (attempt + 1))
        raise SourceError(f"GET {url} failed: {last!r}")


def volume_to_shares(volume: pd.Series, amount_cny: pd.Series, close: pd.Series,
                     default_multiplier: int) -> pd.Series:
    """Convert a volume series to shares.

    Sources report volume in 手 (100 shares) or in shares, and the unit is not
    always consistent (e.g. Tencent reports STAR-market volume in shares). When
    turnover amount is available, infer the unit per row: amount / (vol * close)
    is ~100 for 手 and ~1 for shares. Otherwise use ``default_multiplier``.
    """
    vol = pd.to_numeric(volume, errors="coerce").astype(float)
    amt = pd.to_numeric(amount_cny, errors="coerce").astype(float)
    px = pd.to_numeric(close, errors="coerce").astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = amt / (vol * px)
    mult = pd.Series(np.where(ratio > 10, 100.0, np.where(ratio > 0, 1.0, np.nan)), index=vol.index)
    known = mult.dropna()
    fallback = float(known.median()) if len(known) else float(default_multiplier)
    mult = mult.fillna(100.0 if fallback > 10 else 1.0)
    return vol * mult


def clip_range(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if df.empty:
        return df
    d = pd.to_datetime(df["date"]).dt.date
    return df[(d >= start) & (d <= end)].reset_index(drop=True)
