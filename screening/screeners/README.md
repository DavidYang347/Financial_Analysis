# 筛选方法

这个目录下的每个 `.py` 文件就是一个筛选方法，页面里“股票筛选”会自动列出来。

- 新增方法：复制一个现有文件，改文件名和内容。文件名只能用小写字母、数字和下划线。
- 删除方法：删掉对应文件。
- 以 `_` 开头的文件不会被当作方法，可以放公共函数。
- 修改后不用重启后端，刷新页面即可。

## 脚本格式

```python
import pandas as pd
from screening.context import last_rows, sma
from screening.params import COMMON_FILTERS, Param

META = {
    "name": "方法名称",                  # 必填，列表里显示
    "description": "Markdown 描述",      # 必填，详情页显示
    "tags": ["趋势"],                    # 可选
    "version": "1.0",                    # 可选
    "columns": {"ma20": "MA20"},         # 可选，结果表列名
}

PARAMS = [                               # 可选，页面会生成表单
    Param("n", "周期", "int", 20, min=2, max=250, unit="日"),
    *COMMON_FILTERS,                     # 排除 ST、板块、上市天数、成交额
]

def screen(ctx, params) -> pd.DataFrame:
    pool = ctx.universe(params)                     # 应用通用过滤后的股票池
    bars = ctx.bars(lookback=60, symbols=pool)      # 最近 60 个交易日前复权日线
    ...
    return df[["symbol", ...]]                      # 必须有 symbol 列，一行一只股票
```

参数类型：`int`、`float`、`bool`、`select`、`multiselect`、`date`、`str`。

`ctx` 提供：

- `ctx.as_of`：筛选日期（默认最新交易日）
- `ctx.universe(params)`：股票池
- `ctx.bars(lookback, symbols, adjust="qfq")`：日线，列为 symbol, date, open, high, low, close, volume, amount, turnover
- `ctx.trading_days(n)`、`ctx.stocks()`
- `ctx.md`：底层 `MarketData`，可以直接 `ctx.md.sql("...")`

脚本在后端进程里执行，拥有和后端一样的权限，只放你自己写的或审阅过的代码。
