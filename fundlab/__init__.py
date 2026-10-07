"""fundlab: point-in-time fundamentals, announcements and valuation for fundamental strategies.

    python -m fundlab fetch      # download what is missing (resumable, --budget seconds per run)
    python -m fundlab build      # rebuild the point-in-time tables in data/lake/fundlab/
    python -m fundlab status

Tables (all keyed by the date the information became public, ``ann_date``):

    fin.parquet         quarterly financials (YTD values + derived TTM), one row per symbol/period
    forecast.parquet    业绩预告
    events.parquet      announcements classified into event types (see fundlab.events)
    holder.parquet      major-holder / insider increases and decreases
    repurchase.parquet  buyback plans
    unlock.parquet      lock-up expiries
    pledge.parquet      company-level pledge ratio (weekly)
    valuation.parquet   month-end PB / PE / market cap history per symbol (for percentiles)
"""
