"""Lake status, data-quality checks and source probing."""
from __future__ import annotations

import time
from datetime import date, timedelta

from data import config
from data.query import MarketData
from data.store import Lake


def _records(df) -> list[dict]:
    """JSON-safe records: dates as YYYY-MM-DD, missing values as None."""
    import pandas as pd
    df = df.copy()
    for c in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[c]):
            df[c] = df[c].dt.strftime("%Y-%m-%d")
    return df.astype(object).where(df.notna(), None).to_dict("records")


def status(lake: Lake | None = None) -> dict:
    lake = lake or Lake()
    md = MarketData(lake)
    out = {"lake_dir": str(lake.root), **md.summary()}
    if lake.stocks_path.exists():
        out["universe"] = md.sql("SELECT status, count(*) n FROM stocks GROUP BY 1 ORDER BY 1").to_dict("records")
    if lake.has_daily():
        out["by_source"] = md.sql("SELECT source, count(*) n FROM daily GROUP BY 1 ORDER BY 2 DESC").to_dict("records")
        out["latest_day_symbols"] = int(md.sql("SELECT count(*) FROM daily WHERE date = (SELECT max(date) FROM daily)").iloc[0, 0])
        size = sum(p.stat().st_size for p in lake.root.rglob("*.parquet"))
        out["disk_mb"] = round(size / 1e6, 1)
    out["adj_factor_symbols"] = int(md.sql("SELECT count(DISTINCT symbol) FROM adj_factor").iloc[0, 0])
    cov = md.sql("SELECT method, count(*) AS n FROM name_coverage GROUP BY 1 ORDER BY 2 DESC")
    out["name_history"] = {
        "methods": {r.method: int(r.n) for r in cov.itertuples()},
        "st_segments": int(md.sql("SELECT count(*) FROM names WHERE is_st").iloc[0, 0]),
        "st_today": int(md.sql("""SELECT count(*) FROM names n JOIN stocks s USING (symbol)
                                   WHERE n.is_st AND n."end" IS NULL AND s.status = 'listed'""").iloc[0, 0]),
    }
    out["last_runs"] = [{k: r.get(k) for k in ("mode", "finished_at", "target_date", "symbols_updated",
                                                  "symbols_failed", "rows_written", "source_usage")}
                        for r in lake.read_log(5)]
    md.close()
    return out


def quality_report(lake: Lake | None = None) -> dict:
    lake = lake or Lake()
    md = MarketData(lake)
    r: dict = {}
    r["duplicates"] = int(md.sql("SELECT count(*) FROM (SELECT symbol, date FROM daily GROUP BY 1,2 HAVING count(*)>1)").iloc[0, 0])
    r["bad_ohlc"] = int(md.sql("""SELECT count(*) FROM daily WHERE high < greatest(open, close) - 1e-6
                                  OR low > least(open, close) + 1e-6 OR low <= 0""").iloc[0, 0])
    # Informational: pre-1996 Saturday sessions exist in the bars but not in the Sina calendar.
    r["non_trading_day_rows"] = int(md.sql("""SELECT count(*) FROM daily d ANTI JOIN calendar c USING (date)""").iloc[0, 0])
    # Listed symbols with no bar on the latest stored date (suspended or missing).
    r["listed_missing_latest"] = int(md.sql("""
        SELECT count(*) FROM stocks s WHERE s.status='listed' AND s.symbol NOT IN
          (SELECT symbol FROM daily WHERE date = (SELECT max(date) FROM daily))""").iloc[0, 0])
    r["listed_without_any_bars"] = _records(md.sql("""
        SELECT s.symbol, s.name, s.list_date FROM stocks s WHERE s.status='listed'
        AND s.symbol NOT IN (SELECT DISTINCT symbol FROM daily) ORDER BY 1""").head(50))
    # Adjusted day-over-day moves beyond any A-share price limit. Gaps after long
    # suspensions (> 30 calendar days, e.g. restructurings) are legitimate and excluded.
    jumps = md.sql("""
        WITH x AS (
          SELECT symbol, date, close*adj_factor c,
                 lag(close*adj_factor) OVER w pc, lag(date) OVER w pd,
                 row_number() OVER w rn
          FROM daily_hfq WINDOW w AS (PARTITION BY symbol ORDER BY date))
        SELECT symbol, pd AS prev_date, date, round(c/pc-1, 4) ret FROM x
        WHERE rn > 5 AND pc > 0 AND abs(c/pc-1) > 0.45 AND date >= DATE '2006-01-01'
          AND date_diff('day', pd, date) <= 30
        ORDER BY abs(c/pc-1) DESC""")
    r["suspicious_jumps_hfq_count"] = len(jumps)
    r["suspicious_jumps_hfq"] = _records(jumps.head(30))
    # bad_ohlc rows are upstream errors in a few early-1990s bars; reported, not fatal.
    r["ok"] = r["duplicates"] == 0
    md.close()
    return r


def probe_sources() -> dict:
    from data.sources import REGISTRY
    end = date.today()
    start = end - timedelta(days=20)
    out = {}
    for name, cls in REGISTRY.items():
        t = time.time()
        src = cls()
        try:
            src.check()
            df = src.fetch_daily("600000.SH", start, end)
            out[name] = {"ok": not df.empty, "rows": len(df), "last": str(df["date"].max()) if len(df) else None,
                         "sec": round(time.time() - t, 2)}
        except Exception as e:
            out[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:200], "sec": round(time.time() - t, 2)}
        finally:
            src.close()
    out["_order"] = config.DAILY_SOURCE_ORDER
    return out
