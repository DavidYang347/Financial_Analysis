NPM_REGISTRY ?= https://registry.npmmirror.com

.PHONY: help install dev start build test typecheck update names status check lock clean

help:
	@echo "make install    uv sync 安装 Python 依赖（创建 .venv），npm ci 安装前端依赖"
	@echo "make dev        开发模式启动后端 + 前端并打开页面（改代码自动刷新）"
	@echo "make start      生产模式启动（先构建前端）"
	@echo "make build      构建前端"
	@echo "make test       后端测试 + 前端类型检查"
	@echo "make update     增量更新行情数据"
	@echo "make names      下载 / 更新历史简称和 ST 记录（可断点续传）"
	@echo "make status     查看数据概况"
	@echo "make check      数据质量检查"
	@echo "make lock       依赖改动后重新生成 uv.lock"

install:
	uv sync
	cd frontend && npm ci --registry=$(NPM_REGISTRY) --no-audit --no-fund

dev:
	scripts/start.sh dev

start:
	scripts/start.sh prod

build:
	cd frontend && npm run build

test:
	uv run pytest -q
	cd frontend && npm run typecheck

typecheck:
	cd frontend && npm run typecheck

update:
	uv run python -m data.maintenance update

names:
	uv run python -m data.maintenance names

status:
	uv run python -m data.maintenance status

check:
	uv run python -m data.maintenance check

lock:
	uv lock

clean:
	rm -rf frontend/dist .pytest_cache
	find . -name __pycache__ -not -path './.git/*' -not -path './frontend/node_modules/*' -not -path './.venv/*' -prune -exec rm -rf {} +
