"""通达信 (TDX) TCP quote servers, the protocol behind ``mootdx``.

We talk to the servers through ``tdxpy``, the protocol library that mootdx
is built on. Importing ``mootdx`` itself drags in ``py_mini_racer``, which
often fails to build. The server list comes from mootdx when it is installed,
otherwise from tdxpy.

Notes:
- Daily bars come newest-first in pages of 800 (offset = bars back from latest).
- Volume is in 手 (100 shares); amount is in CNY.
- Delisted stocks return no bars, so the fallback chain moves on.
- ``get_xdxr_info`` returns corporate actions, which we use for adjustment factors.
"""
from __future__ import annotations

import logging
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pandas as pd

from data import config
from data import symbols as sym
from data.schema import empty_daily, normalize_daily
from data.sources.base import DataSource, SourceError, SourceUnavailable, clip_range, volume_to_shares

log = logging.getLogger(__name__)

PAGE = 800
KLINE_DAILY = 9


def _candidate_servers() -> list[tuple[str, int]]:
    servers: list[tuple[str, int]] = []
    for s in config.TDX_SERVERS:
        host, _, port = s.partition(":")
        servers.append((host, int(port or 7709)))
    pools = []
    try:
        from tdxpy.constants import hq_hosts as tdxpy_hosts
        pools.append(tdxpy_hosts)
    except Exception:
        pass
    try:
        from pytdx.config.hosts import hq_hosts as pytdx_hosts
        pools.append(pytdx_hosts)
    except Exception:
        pass
    for pool in pools:
        for item in pool:
            host, port = item[1], int(item[2])
            if (host, port) not in servers:
                servers.append((host, port))
    return servers


class MootdxSource(DataSource):
    name = "mootdx"

    def __init__(self) -> None:
        self._local = threading.local()
        self._servers: list[tuple[str, int]] | None = None
        self._lock = threading.Lock()

    # ---- connection management --------------------------------------
    def _api_cls(self):
        try:
            from tdxpy.hq import TdxHq_API
        except ImportError:
            try:
                from pytdx.hq import TdxHq_API
            except ImportError as e:
                raise SourceUnavailable("tdxpy/pytdx not installed (run: uv sync)") from e
        return TdxHq_API

    @staticmethod
    def _probe(api_cls, server: tuple[str, int]) -> bool:
        api = api_cls()
        try:
            if not api.connect(server[0], server[1], time_out=3):
                return False
            bars = api.get_security_bars(KLINE_DAILY, 1, "600000", 0, 1)
            return bool(bars)
        except Exception:
            return False
        finally:
            try:
                api.disconnect()
            except Exception:
                pass

    def working_servers(self) -> list[tuple[str, int]]:
        with self._lock:
            if self._servers is None:
                api_cls = self._api_cls()
                cands = _candidate_servers()
                with ThreadPoolExecutor(32) as ex:
                    ok = [s for s, good in zip(cands, ex.map(lambda s: self._probe(api_cls, s), cands)) if good]
                log.info("mootdx: %d/%d TDX servers usable", len(ok), len(cands))
                self._servers = ok
            if not self._servers:
                raise SourceUnavailable("no usable TDX server")
            return self._servers

    def _api(self):
        api = getattr(self._local, "api", None)
        if api is not None:
            return api
        api_cls = self._api_cls()
        servers = list(self.working_servers())
        random.shuffle(servers)
        for host, port in servers[:4]:
            api = api_cls()
            try:
                if api.connect(host, port, time_out=8):
                    self._local.api = api
                    return api
            except Exception:
                continue
        raise SourceError("could not connect to any TDX server")

    def _reset(self) -> None:
        api = getattr(self._local, "api", None)
        if api is not None:
            try:
                api.disconnect()
            except Exception:
                pass
        self._local.api = None

    def _call(self, fn_name: str, *args):
        for attempt in range(2):
            api = self._api()
            try:
                return getattr(api, fn_name)(*args)
            except Exception as e:
                self._reset()
                if attempt == 1:
                    raise SourceError(f"tdx {fn_name}{args} failed: {e!r}") from e
        return None

    def check(self) -> None:
        self.working_servers()

    # ---- data ---------------------------------------------------------
    def fetch_daily(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        market, code = sym.to_tdx(symbol)
        rows: list[dict] = []
        offset = 0
        for _ in range(40):
            try:
                page = self._call("get_security_bars", KLINE_DAILY, market, code, offset, PAGE)
            except SourceError:
                # Asking past the start of history raises a parse error on some servers.
                if rows:
                    break
                raise
            if not page:
                break
            rows = list(page) + rows
            first = date(page[0]["year"], page[0]["month"], page[0]["day"])
            if len(page) < PAGE or first <= start:
                break
            offset += PAGE
        if not rows:
            return empty_daily()
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df[["year", "month", "day"]]).dt.date
        df = df.rename(columns={"vol": "volume"})
        df["volume"] = volume_to_shares(df["volume"], df["amount"], df["close"], default_multiplier=100)
        df["turnover"] = None
        return normalize_daily(clip_range(df, start, end), symbol, self.name)

    def fetch_adj_events(self, symbol: str) -> pd.DataFrame:
        market, code = sym.to_tdx(symbol)
        rows = self._call("get_xdxr_info", market, code)
        if rows is None:
            raise SourceError(f"tdx xdxr returned None for {symbol}")
        recs = []
        for r in rows:
            if r.get("category") != 1:  # 1 = 除权除息; others are share-count changes
                continue
            recs.append({
                "date": date(r["year"], r["month"], r["day"]),
                # TDX reports per 10 shares
                "cash": (r.get("fenhong") or 0.0) / 10.0,
                "bonus": (r.get("songzhuangu") or 0.0) / 10.0,
                "rights": (r.get("peigu") or 0.0) / 10.0,
                "rights_price": r.get("peigujia") or 0.0,
            })
        cols = ["date", "cash", "bonus", "rights", "rights_price"]
        return pd.DataFrame(recs, columns=cols)

    def list_stocks(self) -> pd.DataFrame:
        recs = []
        for market, ex in ((0, "SZ"), (1, "SH"), (2, "BJ")):
            count = self._call("get_security_count", market) or 0
            for start in range(0, count, 1000):
                for r in self._call("get_security_list", market, start) or []:
                    code = r["code"]
                    s = f"{code}.{ex}"
                    if sym.is_a_share(s) and sym.infer_exchange(code) == ex:
                        recs.append({"symbol": s, "name": r["name"].strip()})
        return pd.DataFrame(recs, columns=["symbol", "name"]).drop_duplicates("symbol")

    def close(self) -> None:
        self._reset()
