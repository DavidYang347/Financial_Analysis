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
data/                   数据模块
  sources/              tencent · mootdx · eastmoney · baostock · akshare · tushare（按此顺序降级）
  maintenance/          数据维护：全量下载 / 增量更新 / 修复 / 质量检查 / 运行记录
  store.py  query.py    Parquet 存储（按年分区）、DuckDB 查询（不复权 / 前复权 / 后复权）
  lake/                 数据目录（git 忽略）
screening/              股票筛选框架
  screeners/            筛选方法：一个 .py 文件 = 一个方法
frontend/               Vue 3 + Vite + Element Plus + ECharts
  src/modules/          页面模块：data（数据管理）、screening（股票筛选）
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

## 添加筛选方法

在 `screening/screeners/` 新建一个 `.py` 文件，页面上点“刷新”就会出现，删除文件即下线，不需要重启后端。格式见 [`screening/screeners/README.md`](screening/screeners/README.md)，现有的四个方法（均线多头排列、N日新高突破、超跌反弹、连续涨停）可以直接当模板。

筛选脚本在后端进程里执行，拥有和后端一样的权限，只放你自己写的或审阅过的代码。

## 添加页面模块

在 `frontend/src/modules/<名字>/module.ts` 默认导出一个模块定义（标题、图标、排序、路由），左侧导航会自动出现。参考 `modules/screening/module.ts`。

## 数据维护（命令行）

```bash
make update                                    # 增量更新到最近一个已收盘交易日
make status / make check                       # 数据概况 / 质量检查
python -m data.maintenance sources             # 探测各数据源
python -m data.maintenance repair --symbols 600000.SH
python -m data.maintenance init                # 全量重建（可断点续传）
```

页面和命令行共用一把锁，同一时间只会有一个更新在写数据。

## 存储约定

日线存不复权价格，复权因子单独存（后复权累计因子，只存变化点）：后复权 = 原始价 × F(t)，前复权 = 原始价 × F(t) / F(最新)。单位：价格 元，成交量 股，成交额 元，换手率 %。

```python
from data.query import MarketData
md = MarketData()
md.daily("600000.SH", start="2024-01-01", adjust="qfq")
md.sql("SELECT date, count(*) FROM daily GROUP BY 1 ORDER BY 1 DESC LIMIT 5")
```

## 安全

后端没有登录。默认只监听 127.0.0.1，写接口（触发更新、运行筛选）只接受本机请求；设置 `ADMIN_TOKEN` 后触发更新要求 `X-Admin-Token` 头。不要把服务直接暴露到局域网或公网。

## 已知限制

- 复权因子由通达信除权除息记录计算，2016 年后与 baostock 抽查约 97% 的事件误差 < 0.1%；2006 年前的早期记录一致性较差。
- 1990 年代少量周六交易日的行情存在，但交易日历中没有，属正常。
- 部分网络环境下东方财富 K 线接口不可达，降级链会自动跳过。
