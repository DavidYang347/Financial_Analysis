# Financial_Analysis

基本面研究笔记（`个股基本面研究/`、`行业研究/`、`_prompt_template/` 等）+ A 股量化研究平台。

## 快速开始

需要 [uv](https://docs.astral.sh/uv/)（`brew install uv` 或 `curl -LsSf https://astral.sh/uv/install.sh | sh`）和 Node.js 20.19+。Python 不用自己装，uv 会按 `pyproject.toml` 的要求（3.10 到 3.13）找到或下载合适的版本。

```bash
cp .env.example .env   # 可选：填 TUSHARE_TOKEN
make dev               # 首次会自动 uv sync + npm ci，然后同时启动后端和前端，并打开浏览器
```

也可以分步做：`make install`（`uv sync` 创建 `.venv` 并装好 Python 依赖，`npm ci` 装前端依赖）。

- 页面：http://127.0.0.1:5173
- 接口文档：http://127.0.0.1:8000/docs

`make dev` 是开发模式（后端 `--reload`、前端 Vite 热更新）。`make start` 先构建前端，再用 `frontend/server.js` 提供静态页面并转发 `/api`。两种模式都由 `scripts/start.sh` 启动，Ctrl+C 同时停止前后端。端口可以用 `API_PORT`、`WEB_PORT` 改，不想自动打开浏览器就加 `NO_OPEN=1`。

### Python 依赖（uv）

依赖写在 `pyproject.toml`，版本固定，`uv.lock` 锁定完整依赖树，提交到 git。

```bash
uv add <包名>          # 加依赖
uv remove <包名>       # 删依赖
uv run pytest          # 在项目环境里跑命令
uv lock                # 手动改了 pyproject.toml 后重新锁定
```

`pyproject.toml` 里默认走清华镜像；在国外或镜像不可用时，删掉 `[[tool.uv.index]]` 那一段，再 `uv lock`。uv 下载 Python 本体慢的话，可以设环境变量 `UV_PYTHON_INSTALL_MIRROR`。

`mootdx` 数据源用的是通达信协议库 `tdxpy`，没有安装 `mootdx` 包本身（它依赖的 `py_mini_racer` 经常装不上）。`pytdx` 依赖 `cryptography`，Intel 芯片的 Mac 没有预编译包，需要本机有 Rust 编译器。

## 结构

```
backend/app/            FastAPI 后端
  routers/market.py     行情、股票、复权因子、交易日历
  routers/data_admin.py 数据状态、质量检查、数据源、更新任务、更新历史与日志
  routers/screening.py  筛选方法、运行、筛选记录、导出 CSV
  routers/strategies.py 策略列表、回测任务、回测报告、成交 / 持仓、导出 CSV
data/                   数据模块
  sources/              tencent · mootdx · eastmoney · baostock · akshare · tushare（按此顺序降级）
  maintenance/          数据维护：全量下载 / 增量更新 / 修复 / 质量检查 / 运行记录
  store.py  query.py    Parquet 存储（按年分区）、DuckDB 查询（不复权 / 前复权 / 后复权）
  lake/                 数据目录（git 忽略）
screening/              股票筛选框架
  screeners/            筛选方法：一个 .py 文件 = 一个方法
strategy/               策略与回测
  strategies/           策略：一个 .py 文件 = 一个策略
  engine.py rules.py    回测引擎、A 股交易规则（T+1、整手、涨跌停、费用）
  metrics.py runner.py  绩效指标、回测记录存储（data/lake/backtests/）
frontend/               Vue 3 + Vite + Element Plus + ECharts
  src/modules/          页面模块：data（数据管理）、screening（股票筛选）、strategies（策略管理）、backtest（策略回测）
scripts/                start.sh、update_daily.sh
tests/
```

## 页面

**数据管理**

- K线预览：搜索任意股票（含退市），前复权 / 后复权 / 不复权，均线和成交量，滚轮缩放。
- 数据更新：增量更新全部或指定股票、修复指定股票，实时进度和日志；数据现状、质量检查、数据源连通性测试。
- 更新日志与历史：每次更新的统计、失败明细和完整日志。命令行运行的更新也会出现在这里。

**股票筛选**

- 筛选方法：左侧列出 `screening/screeners/` 下的所有脚本。每个方法有“运行”（参数表单、结果表、点行看 K线、导出 CSV）、“说明”（方法描述和参数表）、“源码”、“运行记录”。
- 筛选记录：所有方法的历史运行，参数和结果都保存在本地。

**策略管理**

- 左侧列出 `strategy/strategies/` 下的所有策略脚本。每个策略有“说明”（描述、参数表、默认回测设置）、“源码”、“回测记录”，右上角“回测这个策略”直接带到回测页。

**策略回测**

- 新建回测：选策略，填策略参数和回测设置（区间、初始资金、基准、调仓频率、成交价、佣金 / 印花税 / 滑点），后台运行并显示进度，可取消，完成后自动打开报告。
- 回测报告：核心指标、净值与基准对比、回撤曲线、风险 / 相对基准 / 交易统计、月度收益表、成交明细（含未成交原因）、每日持仓、策略日志、参数与设置；“调整参数再跑”会带上这次的全部设置。
- 回测记录：所有回测的历史，勾选多条可以叠加净值曲线、并排对比指标。

## 添加筛选方法

在 `screening/screeners/` 新建一个 `.py` 文件，页面上点“刷新”就会出现，删除文件即下线，不需要重启后端。格式见 [`screening/screeners/README.md`](screening/screeners/README.md)，现有的四个方法（均线多头排列、N日新高突破、超跌反弹、连续涨停）可以直接当模板。

筛选脚本在后端进程里执行，拥有和后端一样的权限，只放你自己写的或审阅过的代码。

## 添加策略

在 `strategy/strategies/` 新建一个 `.py` 文件，定义 `META`、可选的 `PARAMS` 和 `rebalance(ctx, params)`，返回 `{股票代码: 目标权重}`，页面上点“刷新”就会出现，删除文件即下线。格式和撮合规则见 [`strategy/strategies/README.md`](strategy/strategies/README.md)，现有的三个策略（动量轮动、低波动组合、双均线择时）可以直接当模板。

回测规则：信号用当天收盘数据，默认次日开盘成交；T+1、整手、涨停买不进 / 跌停卖不出、停牌不成交；佣金（最低 5 元）、卖出印花税、滑点；除权除息按复权因子调整持股；退市股票按最后收盘价清算。股票池包含当时还没退市的股票，没有幸存者偏差。

## 高赔率低频基本面策略（v3）

`materials/高赔率低频基本面交易策略_v3执行版.md` 的可回测实现，分三层：

```
fundlab/     基本面与公告数据：fetch.py 下载（东财 / 新浪 / baostock，可断点续传），events.py 公告标题 → 事件类型，
             build.py 生成时点表 data/lake/fundlab/（fin 财报 + TTM、forecast 业绩预告、events 公告事件、holder 增减持、
             repurchase 回购、unlock 解禁、pledge 质押、valuation 月末 PB/PE 历史）
oddsbook/    策略本体：config.py 全部参数（默认值 = 附录 G）、features.py 时点截面、veto.py 一票否决、channels.py 六条通道、
             odds.py 赔率卡 8 步法、score.py 评分卡 + 入场检查清单、book.py 四级池子 / 仓位 / 退出 / 熔断、journal.py 观察表
strategy/strategies/high_odds_v3.py   接入回测引擎
```

```bash
make fundlab        # = python -m fundlab fetch && python -m fundlab build；首次约 1~2 小时，东财接口限流，可中断后重跑
```

然后在“策略回测”里选“高赔率低频基本面 v3”（默认从 2025-01-02 开始）。回测结束后，“高赔率观察”页显示池子规模、按通道 / 母题的胜率与期望、决策日志（每个动作及原因）、赔率卡、月度审计、逐笔往返和期末弹药池。

时点规则：只用 `ann_date <= 当天` 的财报和公告（收盘后复盘、次日开盘成交）；财报发布日优先取预约披露表的实际披露日。所有价格（目标价、证伪价、买入线）按后复权保存，跨除权除息可比。

引擎新增了可选的 `finalize(ctx, params)` 钩子：策略返回 `{名字: DataFrame}`，保存为回测目录下的 `x_<名字>.parquet`，接口 `/api/v1/backtests/{run_id}/extras`。

已知简化：没有一致预期和备考利润（事件成功价 = 事件前价格 × 倍数）；有息负债用总负债扣应付和预收近似；质押是公司层面比例；财报是最新口径；公告只按标题分类。

## 添加页面模块

在 `frontend/src/modules/<名字>/module.ts` 默认导出一个模块定义（标题、图标、排序、路由），左侧导航会自动出现。参考 `modules/screening/module.ts`。

## 数据维护（命令行）

```bash
make update                                    # 增量更新到最近一个已收盘交易日
make status / make check                       # 数据概况 / 质量检查
python -m data.maintenance sources             # 探测各数据源
python -m data.maintenance repair --symbols 600000.SH
python -m data.maintenance init                # 全量重建（可断点续传）
make names                                     # 下载历史简称和 ST 区间（可断点续传）
```

页面和命令行共用一把锁，同一时间只会有一个更新在写数据。

## 历史简称与 ST

`make names`（或页面“数据更新”里选“简称 / ST 历史”）把每只股票历史上的简称和 ST / *ST 区间下载到 `meta/names.parquet`：

- 深市：深交所“证券简称变更”全表，带日期和简称，一次下载。
- 沪市：先用东方财富曾用名筛出从未 ST 的股票，其余逐只查询 BaoStock 日线 ST 标记（只有 ST 与否，没有简称）。配置了 `TUSHARE_TOKEN` 时优先用 tushare namechange，带简称。
- 北交所：暂无带日期的免费来源，按当前名称判断。

首次约 10~20 分钟，可以中断后重跑继续；下载过一次后，增量更新会顺带更新有改名的股票。回测的涨跌停、`exclude_st` 股票池过滤、连续涨停筛选都按当天的 ST 状态判断；2026-07-06 起沪深主板 ST 涨跌幅为 10%。

```python
md.name_history("600234.SH")     # 简称 / ST 区间
md.st_stocks("2015-06-01")       # 当天的 ST 股票及当时简称
md.sql("SELECT * FROM names WHERE is_st AND start >= '2024-01-01'")
```

## 存储约定

日线存不复权价格，复权因子单独存（后复权累计因子，只存变化点）：后复权 = 原始价 × F(t)，前复权 = 原始价 × F(t) / F(最新)。单位：价格 元，成交量 股，成交额 元，换手率 %。

```python
from data.query import MarketData
md = MarketData()
md.daily("600000.SH", start="2024-01-01", adjust="qfq")
md.sql("SELECT date, count(*) FROM daily GROUP BY 1 ORDER BY 1 DESC LIMIT 5")
```

## 安全

后端没有登录。默认只监听 127.0.0.1，写接口（触发更新、运行筛选、启动回测）只接受本机请求；设置 `ADMIN_TOKEN` 后触发更新要求 `X-Admin-Token` 头。不要把服务直接暴露到局域网或公网。

## 已知限制

- 复权因子由通达信除权除息记录计算，2016 年后与 baostock 抽查约 97% 的事件误差 < 0.1%；2006 年前的早期记录一致性较差。
- 1990 年代少量周六交易日的行情存在，但交易日历中没有，属正常。
- 部分网络环境下东方财富 K 线接口不可达，降级链会自动跳过。
- 历史 ST：北交所 5 只曾经 ST 的股票没有带日期的来源，按当前名称判断；沪市 ST 区间来自 BaoStock，没有当时的简称。回测不模拟成交量限制。
- 同一时间只运行一个回测。全市场轮动策略回测 10 年约 20 秒，内存约 1.5 GB。
