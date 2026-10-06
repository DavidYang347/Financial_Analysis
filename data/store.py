"""Parquet storage layout (the "lake").

    lake/
      daily/year=YYYY/part.parquet   unadjusted daily bars, sorted by (symbol, date)
      adj_factor/adj_factor.parquet  hfq factor change points for all symbols
      meta/stocks.parquet            stock universe (listed + delisted)
      meta/calendar.parquet          trading days
      meta/adj_events.parquet        raw corporate actions (used to detect new ex-dates)
      meta/sync_state.parquet        per-symbol bookkeeping
      meta/update_log.jsonl          one line per maintenance run
      _staging/daily/<symbol>.parquet  per-symbol files written during the initial download

Writes go to a temp file and are moved into place with ``os.replace``, so a
crash never leaves a half-written partition behind.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import uuid
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from data import config
from data.schema import ADJ_SCHEMA, DAILY_COLUMNS, DAILY_SCHEMA, STOCK_SCHEMA, to_arrow_daily

log = logging.getLogger(__name__)

COMPRESSION = "zstd"


class Lake:
    def __init__(self, root: Path | str | None = None, staging: Path | str | None = None) -> None:
        self.root = Path(root or config.LAKE_DIR)
        self.staging = Path(staging or (self.root / "_staging" if root else config.STAGING_DIR))
        self.daily_dir = self.root / "daily"
        self.adj_dir = self.root / "adj_factor"
        self.meta_dir = self.root / "meta"
        self._write_lock = threading.Lock()

    # ---- paths ----------------------------------------------------------
    @property
    def adj_path(self) -> Path:
        return self.adj_dir / "adj_factor.parquet"

    @property
    def stocks_path(self) -> Path:
        return self.meta_dir / "stocks.parquet"

    @property
    def calendar_path(self) -> Path:
        return self.meta_dir / "calendar.parquet"

    @property
    def events_path(self) -> Path:
        return self.meta_dir / "adj_events.parquet"

    @property
    def state_path(self) -> Path:
        return self.meta_dir / "sync_state.parquet"

    @property
    def log_path(self) -> Path:
        return self.meta_dir / "update_log.jsonl"

    def year_path(self, year: int) -> Path:
        return self.daily_dir / f"year={year}" / "part.parquet"

    def staging_daily_dir(self) -> Path:
        return self.staging / "daily"

    def ensure_dirs(self) -> None:
        for d in (self.daily_dir, self.adj_dir, self.meta_dir):
            d.mkdir(parents=True, exist_ok=True)

    def has_daily(self) -> bool:
        return any(self.daily_dir.glob("year=*/part.parquet"))

    def daily_glob(self) -> str:
        return str(self.daily_dir / "year=*" / "*.parquet")

    # ---- atomic write -----------------------------------------------------
    @staticmethod
    def _atomic_write(table: pa.Table, path: Path, row_group_size: int = 256_000) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            pq.write_table(table, tmp, compression=COMPRESSION, row_group_size=row_group_size)
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()

    # ---- meta tables ------------------------------------------------------
    def write_stocks(self, df: pd.DataFrame) -> None:
        df = df.copy()
        for c in ("list_date", "delist_date"):
            df[c] = pd.to_datetime(df[c], errors="coerce").dt.date
        df = df.sort_values("symbol").reset_index(drop=True)
        self._atomic_write(pa.Table.from_pandas(df[STOCK_SCHEMA.names], schema=STOCK_SCHEMA, preserve_index=False),
                           self.stocks_path)

    def read_stocks(self) -> pd.DataFrame:
        if not self.stocks_path.exists():
            return pd.DataFrame(columns=STOCK_SCHEMA.names)
        return pq.read_table(self.stocks_path).to_pandas()

    def write_calendar(self, days: list[date]) -> None:
        t = pa.table({"date": pa.array(sorted(set(days)), type=pa.date32())})
        self._atomic_write(t, self.calendar_path)

    def read_calendar(self) -> list[date]:
        if not self.calendar_path.exists():
            return []
        return list(pq.read_table(self.calendar_path).column("date").to_pylist())

    def write_events(self, df: pd.DataFrame) -> None:
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.sort_values(["symbol", "date"]).reset_index(drop=True)
        self._atomic_write(pa.Table.from_pandas(df, preserve_index=False), self.events_path)

    def read_events(self) -> pd.DataFrame:
        if not self.events_path.exists():
            return pd.DataFrame(columns=["symbol", "date", "cash", "bonus", "rights", "rights_price", "source"])
        return pq.read_table(self.events_path).to_pandas()

    def write_state(self, df: pd.DataFrame) -> None:
        df = df.sort_values("symbol").reset_index(drop=True)
        self._atomic_write(pa.Table.from_pandas(df, preserve_index=False), self.state_path)

    def read_state(self) -> pd.DataFrame:
        if not self.state_path.exists():
            return pd.DataFrame(columns=["symbol", "first_date", "last_date", "rows", "last_source",
                                         "factor_source", "updated_at", "last_error"])
        return pq.read_table(self.state_path).to_pandas()

    def append_log(self, record: dict) -> None:
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def read_log(self, limit: int = 20) -> list[dict]:
        if not self.log_path.exists():
            return []
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        return [json.loads(x) for x in lines[-limit:] if x.strip()]

    # ---- adjustment factors ---------------------------------------------
    def write_adj_factors(self, df: pd.DataFrame) -> None:
        df = df[ADJ_SCHEMA.names].copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df.sort_values(["symbol", "date"]).drop_duplicates(["symbol", "date"], keep="last")
        self._atomic_write(pa.Table.from_pandas(df, schema=ADJ_SCHEMA, preserve_index=False), self.adj_path)

    def read_adj_factors(self) -> pd.DataFrame:
        if not self.adj_path.exists():
            return ADJ_SCHEMA.empty_table().to_pandas()
        return pq.read_table(self.adj_path).to_pandas()

    def upsert_adj_factors(self, new: pd.DataFrame, replace_symbols: set[str]) -> None:
        """Replace all factor rows for ``replace_symbols`` with ``new``."""
        with self._write_lock:
            cur = self.read_adj_factors()
            cur = cur[~cur["symbol"].isin(replace_symbols)]
            self.write_adj_factors(pd.concat([cur, new], ignore_index=True))

    # ---- staging (initial download) ----------------------------------------
    def staging_path(self, symbol: str) -> Path:
        return self.staging_daily_dir() / f"{symbol}.parquet"

    def write_staging(self, symbol: str, df: pd.DataFrame) -> None:
        self._atomic_write(to_arrow_daily(df), self.staging_path(symbol))

    def staged_symbols(self) -> set[str]:
        d = self.staging_daily_dir()
        if not d.exists():
            return set()
        return {p.stem for p in d.glob("*.parquet")}

    def clear_staging(self) -> None:
        if self.staging.exists():
            shutil.rmtree(self.staging)

    # ---- daily partitions ---------------------------------------------------
    def read_year(self, year: int) -> pa.Table | None:
        p = self.year_path(year)
        return pq.read_table(p, schema=DAILY_SCHEMA) if p.exists() else None

    def write_year(self, year: int, table: pa.Table) -> None:
        self._atomic_write(table, self.year_path(year))

    def upsert_daily(self, df: pd.DataFrame) -> dict[int, int]:
        """Merge bars into year partitions. New rows win over existing (symbol, date) rows.

        Only partitions that receive data are rewritten. Returns {year: rows_written}.
        """
        if df is None or df.empty:
            return {}
        import duckdb

        df = df[DAILY_COLUMNS].copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df["year"] = pd.to_datetime(df["date"]).dt.year
        written: dict[int, int] = {}
        with self._write_lock:
            con = duckdb.connect()
            try:
                for year, part in df.groupby("year"):
                    new_t = to_arrow_daily(part.drop(columns="year"))
                    old_t = self.read_year(int(year))
                    con.register("new_t", new_t)
                    if old_t is not None:
                        con.register("old_t", old_t)
                        sql = """
                            SELECT * FROM new_t
                            UNION ALL
                            SELECT o.* FROM old_t o ANTI JOIN new_t n USING (symbol, date)
                            ORDER BY symbol, date
                        """
                    else:
                        sql = "SELECT * FROM new_t ORDER BY symbol, date"
                    # .arrow() returns a RecordBatchReader since DuckDB 1.5; read it fully.
                    merged = con.execute(sql).arrow().read_all().cast(DAILY_SCHEMA)
                    self.write_year(int(year), merged)
                    written[int(year)] = len(part)
                    con.unregister("new_t")
                    if old_t is not None:
                        con.unregister("old_t")
            finally:
                con.close()
        return written

    def delete_symbols(self, symbols: set[str]) -> None:
        """Remove all bars for ``symbols`` (used by `repair` before a re-download)."""
        if not symbols:
            return
        with self._write_lock:
            for p in sorted(self.daily_dir.glob("year=*/part.parquet")):
                t = pq.read_table(p, schema=DAILY_SCHEMA)
                mask = pa.compute.is_in(t.column("symbol"), value_set=pa.array(sorted(symbols)))
                if pa.compute.any(mask).as_py():
                    self._atomic_write(t.filter(pa.compute.invert(mask)), p)

    def consolidate_staging(self, memory_limit: str | None = None) -> int:
        """Build year partitions from per-symbol staging files. Returns the row count."""
        import duckdb

        src = self.staging_daily_dir()
        files = sorted(src.glob("*.parquet"))
        if not files:
            return 0
        out_tmp = self.root / f".daily_build_{uuid.uuid4().hex}"
        con = duckdb.connect()
        try:
            con.execute(f"SET memory_limit='{memory_limit or config.DUCKDB_MEMORY_LIMIT}'")
            con.execute(f"SET threads={config.DUCKDB_THREADS}")
            con.execute("SET preserve_insertion_order=false")
            tmp_dir = self.root / ".duckdb_tmp"
            con.execute(f"SET temp_directory='{tmp_dir}'")
            glob = str(src / "*.parquet")
            years = [r[0] for r in con.execute(
                f"SELECT DISTINCT year(date) FROM read_parquet('{glob}') ORDER BY 1").fetchall()]
            total = 0
            for y in years:
                p = out_tmp / f"year={y}" / "part.parquet"
                p.parent.mkdir(parents=True, exist_ok=True)
                con.execute(f"""
                    COPY (
                        SELECT * FROM read_parquet('{glob}')
                        WHERE date >= DATE '{y}-01-01' AND date < DATE '{y + 1}-01-01'
                        ORDER BY symbol, date
                    ) TO '{p}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 256000)
                """)
                total += con.execute(f"SELECT count(*) FROM read_parquet('{p}')").fetchone()[0]
            with self._write_lock:
                old = self.root / f".daily_old_{uuid.uuid4().hex}"
                if self.daily_dir.exists():
                    os.replace(self.daily_dir, old)
                os.replace(out_tmp, self.daily_dir)
                if old.exists():
                    shutil.rmtree(old)
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
            return total
        finally:
            con.close()
            if out_tmp.exists():
                shutil.rmtree(out_tmp, ignore_errors=True)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
