"""行情复盘：月度 / 周度复盘的数据统计（materials/复盘方法/ 下的两份 SOP）。

    python -m review fetch                    # 指数、申万行业、涨跌停池、两融、宏观等外部数据（可断点续传）
    python -m review monthly 2026-09          # 计算一个月的统计，写入 data/lake/reviews/monthly/2026-09.json
    python -m review weekly 2026-09-30        # 计算包含该日的那一周
    python -m review list

统计全部来自本地行情库（前复权口径 = 后复权收盘价之比，与前复权区间涨幅完全一致）和
fundlab 公告库；外部数据（指数、申万行业、涨停池、两融、宏观）由 review.fetch 下载缓存。
报告正文（Markdown）放在 materials/复盘报告/{月度复盘,周度复盘}/，由人或大模型按 SOP 撰写，
前端“行情复盘”页把报告和统计表并排展示。
"""
