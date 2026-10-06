"""DuckDB query layer over the Parquet lake.

    from data.query import MarketData
    md = MarketData()
    df = md.daily(["600000.SH", "000001.SZ"], start="2024-01-01", adjust="qfq")
    md.sql("SELECT symbol, count(*) FROM daily GROUP BY 1")

Views available inside ``sql()``: daily (raw), adj_factor, stocks, calendar,
daily_hfq (backward-adjusted with an adj_factor column).
"""
from __future__ import annotations

import threading
from datetime import date
from typing import Iterable, Literal

import duckdb
import pandas as pd

from data import config
from data import symbols as sym
from data.store import Lake

Adjust = Literal["none", "qfq", "hfq"]


def _d(x) -> date | None:
    if x is None or x == "":
        return None
    return pd.Timestamp(x).date()


class MarketData:
    def __init__(self, lake: Lake | None = None) -> None:
        self.lake = lake or Lake()
        self._lock = threading.Lock()
        self._con: duckdb.DuckDBPyConnection | None = None

    # DuckDB connections are not thread-safe; each call uses a cursor under a lock.
    def _connect(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            con = duckdb.connect()
            con.execute(f"SET memory_limit='{config.DUCKDB_MEMORY_LIMIT}'")
            con.execute(f"SET threads={config.DUCKDB_THREADS}")
            self._con = con
            self.refresh_views()
        return self._con

    def refresh_views(self) -> None:
        """Re-create views (call after the lake is rewritten)."""
        con = self._con or self._connect()
        L = self.lake
        if L.has_daily():
            con.execute(f"CREATE OR REPLACE VIEW daily AS SELECT * FROM read_parquet('{L.daily_glob()}', hive_partitioning=false)")
        else:
            con.execute("""CREATE OR REPLACE VIEW daily AS SELECT NULL::VARCHAR AS symbol, NULL::DATE AS "date",
                NULL::DOUBLE AS open, NULL::DOUBLE AS high, NULL::DOUBLE AS low, NULL::DOUBLE AS close,
                NULL::BIGINT AS volume, NULL::DOUBLE AS amount, NULL::DOUBLE AS turnover,
                NULL::VARCHAR AS source WHERE false""")
        if L.adj_path.exists():
            con.execute(f"CREATE OR REPLACE VIEW adj_factor AS SELECT * FROM read_parquet('{L.adj_path}')")
        else:
            con.execute("""CREATE OR REPLACE VIEW adj_factor AS SELECT NULL::VARCHAR AS symbol, NULL::DATE AS "date",
                NULL::DOUBLE AS adj_factor, NULL::VARCHAR AS source WHERE false""")
        if L.stocks_path.exists():
            con.execute(f"CREATE OR REPLACE VIEW stocks AS SELECT * FROM read_parquet('{L.stocks_path}')")
        else:
            con.execute("""CREATE OR REPLACE VIEW stocks AS SELECT NULL::VARCHAR AS symbol, NULL::VARCHAR AS code,
                NULL::VARCHAR AS exchange, NULL::VARCHAR AS "name", NULL::VARCHAR AS board,
                NULL::DATE AS list_date, NULL::DATE AS delist_date, NULL::VARCHAR AS status,
                NULL::VARCHAR AS sources WHERE false""")
        if L.calendar_path.exists():
            con.execute(f"CREATE OR REPLACE VIEW calendar AS SELECT * FROM read_parquet('{L.calendar_path}')")
        else:
            con.execute('CREATE OR REPLACE VIEW calendar AS SELECT NULL::DATE AS "date" WHERE false')
        # ASOF join picks the latest factor change point on or before each bar.
        con.execute("""
            CREATE OR REPLACE VIEW daily_hfq AS
            SELECT d.*, coalesce(f.adj_factor, 1.0) AS adj_factor
            FROM daily d ASOF LEFT JOIN adj_factor f
              ON d.symbol = f.symbol AND d.date >= f.date
        """)

    def sql(self, query: str, params: list | None = None) -> pd.DataFrame:
        with self._lock:
            return self._connect().cursor().execute(query, params or []).df()

    def latest_date(self) -> date | None:
        """Most recent trading day with stored bars."""
        if not self.lake.has_daily():
            return None
        v = self.sql("SELECT max(date) AS d FROM daily")["d"].iloc[0]
        return None if pd.isna(v) else pd.Timestamp(v).date()

    # ---- high level API ---------------------------------------------------
    def daily(self, symbols: str | Iterable[str] | None = None, start=None, end=None,
              adjust: Adjust = "none", columns: list[str] | None = None) -> pd.DataFrame:
        """Daily bars. adjust: none | qfq (前复权, latest price unchanged) | hfq (后复权)."""
        if adjust not in ("none", "qfq", "hfq"):
            raise ValueError("adjust must be none|qfq|hfq")
        where, params = [], []
        if symbols is not None:
            syms = [symbols] if isinstance(symbols, str) else list(symbols)
            syms = [sym.normalize(s) for s in syms]
            where.append(f"d.symbol IN ({','.join('?' * len(syms))})")
            params += syms
        if _d(start):
            where.append("d.date >= ?"); params.append(_d(start))
        if _d(end):
            where.append("d.date <= ?"); params.append(_d(end))
        w = ("WHERE " + " AND ".join(where)) if where else ""

        if adjust == "none":
            q = f"SELECT d.symbol, d.date, d.open, d.high, d.low, d.close, d.volume, d.amount, d.turnover FROM daily d {w}"
        else:
            # hfq: raw * F(t).  qfq: raw * F(t) / F(latest change point) so the latest price is unchanged.
            scale = "d.adj_factor" if adjust == "hfq" else "d.adj_factor / coalesce(l.latest_factor, 1.0)"
            q = f"""
                WITH l AS (
                    SELECT symbol, arg_max(adj_factor, date) AS latest_factor FROM adj_factor GROUP BY symbol
                )
                SELECT d.symbol, d.date,
                       d.open * {scale} AS open, d.high * {scale} AS high,
                       d.low * {scale} AS low, d.close * {scale} AS close,
                       d.volume, d.amount, d.turnover, d.adj_factor
                FROM daily_hfq d LEFT JOIN l ON d.symbol = l.symbol
                {w}
            """
        q += " ORDER BY d.symbol, d.date"
        df = self.sql(q, params)
        if adjust != "none":
            for c in ("open", "high", "low", "close"):
                df[c] = df[c].round(4)
        if columns:
            df = df[[c for c in columns if c in df.columns]]
        return df

    def stocks(self, status: str | None = None, keyword: str | None = None) -> pd.DataFrame:
        where, params = [], []
        if status:
            where.append("status = ?"); params.append(status)
        if keyword:
            where.append("(symbol ILIKE ? OR name ILIKE ?)"); params += [f"%{keyword}%"] * 2
        w = ("WHERE " + " AND ".join(where)) if where else ""
        return self.sql(f"SELECT * FROM stocks {w} ORDER BY symbol", params)

    def adj_factors(self, symbol: str) -> pd.DataFrame:
        return self.sql("SELECT date, adj_factor, source FROM adj_factor WHERE symbol = ? ORDER BY date",
                        [sym.normalize(symbol)])

    def calendar(self, start=None, end=None) -> list[date]:
        df = self.sql("SELECT date FROM calendar WHERE (? IS NULL OR date >= ?) AND (? IS NULL OR date <= ?) ORDER BY date",
                      [_d(start), _d(start), _d(end), _d(end)])
        return [x.date() if hasattr(x, "date") else x for x in df["date"]]

    def cross_section(self, day, adjust: Adjust = "none") -> pd.DataFrame:
        """All symbols on one trading day."""
        return self.daily(None, start=day, end=day, adjust=adjust)

    def summary(self) -> dict:
        if not self.lake.has_daily():
            return {"rows": 0, "symbols": 0, "first_date": None, "last_date": None}
        r = self.sql('SELECT count(*) AS "rows", count(DISTINCT symbol) AS symbols, '
                     'min(date) AS first_date, max(date) AS last_date FROM daily').iloc[0]
        return {k: (v.date().isoformat() if hasattr(v, "date") else (int(v) if k in ("rows", "symbols") else v))
                for k, v in r.items()}

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None
