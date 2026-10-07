import { get, post } from './http'

export type Adjust = 'none' | 'qfq' | 'hfq'

export interface Stock {
  symbol: string
  code: string
  exchange: string
  name: string
  board: string
  list_date: string | null
  delist_date: string | null
  status: 'listed' | 'delisted'
  sources: string
}

export interface Bar {
  date: string
  open: number
  high: number
  low: number
  close: number
  volume: number | null
  amount: number | null
  turnover: number | null
  adj_factor?: number
}

export interface MaintenanceJob {
  status: 'idle' | 'running' | 'finished' | 'failed'
  run_id?: string
  kind?: 'update' | 'repair' | 'names'
  symbols?: string[] | null
  started_at?: string
  finished_at?: string
  phase?: string
  phase_label?: string
  done?: number
  total?: number
  elapsed_sec?: number
  error?: string
  report?: RunSummary
}

export interface RunSummary {
  run_id: string
  mode: 'full' | 'incremental' | 'repair' | 'names'
  started_at: string
  finished_at: string | null
  target_date: string | null
  symbols_total: number
  symbols_updated: number
  symbols_failed: number
  symbols_empty: number
  rows_written: number
  factors_recomputed: number
  source_usage: Record<string, number>
  disabled_sources: Record<string, string>
  failed?: { symbol: string; errors: Record<string, string> }[]
  complete?: boolean
  elapsed_sec: number
  error?: string | null
  has_log?: boolean
}

export interface DataStatus {
  lake_dir: string
  rows: number
  symbols: number
  first_date: string | null
  last_date: string | null
  universe?: { status: string; n: number }[]
  by_source?: { source: string; n: number }[]
  latest_day_symbols?: number
  disk_mb?: number
  adj_factor_symbols: number
  name_history?: { methods: Record<string, number>; st_segments: number; st_today: number }
  job: MaintenanceJob
  locked: boolean
}

export interface NameSegment {
  start: string
  end: string | null
  name: string | null
  is_st: boolean
  source: string
}

export interface NameHistory {
  symbol: string
  coverage: { method: string; former_names: string; updated_at: string; error: string | null } | null
  items: NameSegment[]
}

export interface QualityReport {
  ok: boolean
  duplicates: number
  bad_ohlc: number
  non_trading_day_rows: number
  listed_missing_latest: number
  listed_without_any_bars: { symbol: string; name: string; list_date: string | null }[]
  suspicious_jumps_hfq_count: number
  suspicious_jumps_hfq: { symbol: string; prev_date: string; date: string; ret: number }[]
}

export type SourceProbe = Record<string, { ok: boolean; rows?: number; last?: string | null; error?: string; sec: number }>

export interface ParamDef {
  key: string
  label: string
  type: 'int' | 'float' | 'bool' | 'select' | 'multiselect' | 'date' | 'str'
  default: unknown
  help: string
  min: number | null
  max: number | null
  step: number | null
  options: { value: string | number; label: string }[] | null
  unit: string
  group: string
}

export interface ScreenerSummary {
  id: string
  name: string
  tags: string[]
  author: string
  version: string
  file: string
  ok: boolean
  error: string | null
  param_count: number
  brief: string
}

export interface ScreenerDetail extends ScreenerSummary {
  description: string
  columns: Record<string, string>
  params: ParamDef[]
}

export interface ScreenRecord {
  run_id: string
  screener_id: string
  screener_name: string
  screener_version: string
  params: Record<string, unknown>
  started_at: string
  as_of?: string
  status: 'ok' | 'failed'
  count?: number
  error?: string
  trace?: string
  elapsed_sec: number
}

export interface ScreenResult extends ScreenRecord {
  columns: { key: string; label: string }[]
  items: Record<string, unknown>[]
}

export const api = {
  // market
  searchStocks: (keyword: string, limit = 20) =>
    get<{ count: number; items: Stock[] }>('/api/v1/stocks', { keyword, limit }),
  stock: (symbol: string) => get<Stock>(`/api/v1/stocks/${encodeURIComponent(symbol)}`),
  names: (symbol: string) => get<NameHistory>(`/api/v1/stocks/${encodeURIComponent(symbol)}/names`),
  stList: (date?: string) =>
    get<{ date: string; count: number; items: { symbol: string; name: string; name_then: string | null; covered: boolean }[] }>(
      '/api/v1/st', { date }),
  daily: (symbol: string, adjust: Adjust, start?: string, end?: string) =>
    get<{ symbol: string; adjust: Adjust; count: number; items: Bar[] }>(
      `/api/v1/daily/${encodeURIComponent(symbol)}`, { adjust, start, end }),

  // data management
  status: () => get<DataStatus>('/api/v1/data/status'),
  check: () => get<QualityReport>('/api/v1/data/check'),
  sources: () => get<SourceProbe>('/api/v1/data/sources'),
  startUpdate: (body: { kind: 'update' | 'repair' | 'names'; symbols?: string[] | null; refresh_meta?: boolean }) =>
    post<MaintenanceJob>('/api/v1/data/update', body),
  job: () => get<MaintenanceJob>('/api/v1/data/update'),
  runs: (limit = 50, offset = 0) => get<{ total: number; items: RunSummary[] }>('/api/v1/data/runs', { limit, offset }),
  run: (id: string) => get<RunSummary>(`/api/v1/data/runs/${encodeURIComponent(id)}`),
  runLog: (id: string, offset = 0) =>
    get<{ run_id: string; text: string; next_offset: number; running: boolean }>(
      `/api/v1/data/runs/${encodeURIComponent(id)}/log`, { offset }),

  // screening
  screeners: () => get<{ count: number; directory: string; items: ScreenerSummary[] }>('/api/v1/screeners'),
  screener: (id: string) => get<ScreenerDetail>(`/api/v1/screeners/${encodeURIComponent(id)}`),
  screenerSource: (id: string) =>
    get<{ id: string; file: string; source: string }>(`/api/v1/screeners/${encodeURIComponent(id)}/source`),
  runScreener: (id: string, params: Record<string, unknown>, as_of?: string | null) =>
    post<ScreenResult>(`/api/v1/screeners/${encodeURIComponent(id)}/run`, { params, as_of: as_of || null }),
  screenerHistory: (id?: string, limit = 50) =>
    get<{ items: ScreenRecord[] }>(id ? `/api/v1/screeners/${encodeURIComponent(id)}/history` : '/api/v1/screeners/history', { limit }),
  screenRun: (runId: string) => get<ScreenResult>(`/api/v1/screeners/runs/${encodeURIComponent(runId)}`),
  screenCsvUrl: (runId: string) => `/api/v1/screeners/runs/${encodeURIComponent(runId)}/csv`,
}

// ---- strategies & backtests --------------------------------------------------

export interface StrategySummary extends ScreenerSummary {}

export interface BacktestSettings {
  start: string
  end: string
  initial_cash: number
  rebalance: 'daily' | 'weekly' | 'monthly' | 'every_n'
  every_n: number
  fill: 'next_open' | 'close'
  commission: number
  min_commission: number
  stamp_tax: number
  slippage: number
  tolerance: number
  benchmark: string
  risk_free: number
  lookback: number
}

export interface StrategyDetail extends StrategySummary {
  description: string
  params: ParamDef[]
  defaults: Partial<BacktestSettings>
}

export interface BacktestMetrics {
  start: string | null
  end: string | null
  trading_days: number
  final_equity: number | null
  total_return: number | null
  annual_return: number | null
  annual_volatility: number | null
  sharpe: number | null
  sortino: number | null
  calmar: number | null
  max_drawdown: number | null
  max_dd_peak: string | null
  max_dd_trough: string | null
  max_dd_recovery: string | null
  win_days: number | null
  best_day: number | null
  worst_day: number | null
  avg_positions: number | null
  avg_exposure: number | null
  benchmark_return?: number | null
  benchmark_annual_return?: number | null
  excess_return?: number | null
  benchmark_max_drawdown?: number | null
  alpha?: number | null
  beta?: number | null
  tracking_error?: number | null
  information_ratio?: number | null
  win_days_vs_benchmark?: number | null
  trades: number
  buys: number
  sells: number
  blocked_orders: number
  win_rate: number | null
  profit_factor: number | null
  avg_win: number | null
  avg_loss: number | null
  total_fees: number | null
  annual_turnover: number | null
}

export interface BacktestRecord {
  run_id: string
  strategy_id: string
  strategy_name: string
  strategy_version: string
  params: Record<string, unknown>
  settings: BacktestSettings
  started_at: string
  status: 'ok' | 'failed' | 'cancelled'
  error?: string
  trace?: string
  elapsed_sec: number
  metrics?: Pick<BacktestMetrics, 'total_return' | 'annual_return' | 'max_drawdown' | 'sharpe' | 'excess_return' | 'trades' | 'final_equity'>
}

export interface EquityPoint {
  date: string
  equity: number
  cash: number
  market_value: number
  positions: number
  nav: number
  drawdown: number
  benchmark?: number
  benchmark_nav?: number
  benchmark_drawdown?: number
}

export interface BacktestReport extends Omit<BacktestRecord, 'metrics'> {
  metrics: BacktestMetrics
  monthly: { year: number; month: number; ret: number | null }[]
  logs: string[]
  signals: number
  equity: EquityPoint[]
}

export interface Trade {
  date: string
  symbol: string
  name: string
  side: 'buy' | 'sell'
  shares: number
  price: number
  amount: number
  commission: number
  tax: number
  pnl: number | null
  status: 'filled' | 'blocked'
  reason: string
}

export interface Holding {
  date: string
  symbol: string
  name: string | null
  shares: number
  price: number
  value: number
  weight: number
  pnl_pct: number
}

export interface BacktestJob {
  status: 'idle' | 'running' | 'ok' | 'failed' | 'cancelled'
  run_id?: string
  strategy_id?: string
  strategy_name?: string
  done?: number
  total?: number
  started_at?: string
  finished_at?: string
  elapsed_sec?: number
  error?: string | null
}

export const strategyApi = {
  list: () => get<{ count: number; directory: string; items: StrategySummary[] }>('/api/v1/strategies'),
  detail: (id: string) => get<StrategyDetail>(`/api/v1/strategies/${encodeURIComponent(id)}`),
  source: (id: string) =>
    get<{ id: string; file: string; source: string }>(`/api/v1/strategies/${encodeURIComponent(id)}/source`),

  start: (strategy: string, params: Record<string, unknown>, settings: Partial<BacktestSettings>) =>
    post<BacktestJob>('/api/v1/backtests', { strategy, params, settings }),
  job: () => get<BacktestJob>('/api/v1/backtests/job'),
  cancel: () => post<BacktestJob>('/api/v1/backtests/job/cancel'),
  history: (strategy?: string, limit = 200) =>
    get<{ items: BacktestRecord[] }>('/api/v1/backtests', { strategy, limit }),
  report: (runId: string) => get<BacktestReport>(`/api/v1/backtests/${encodeURIComponent(runId)}`),
  trades: (runId: string, q: { status?: string; symbol?: string; offset?: number; limit?: number } = {}) =>
    get<{ total: number; items: Trade[] }>(`/api/v1/backtests/${encodeURIComponent(runId)}/trades`, q),
  holdings: (runId: string, date?: string) =>
    get<{ date: string | null; days: string[]; items: Holding[]; cash: number | null; equity: number | null }>(
      `/api/v1/backtests/${encodeURIComponent(runId)}/holdings`, { date }),
  tradesCsvUrl: (runId: string) => `/api/v1/backtests/${encodeURIComponent(runId)}/trades.csv`,
  extras: (runId: string) =>
    get<{ items: ExtraTableInfo[] }>(`/api/v1/backtests/${encodeURIComponent(runId)}/extras`),
  extra: (runId: string, name: string,
          q: { symbol?: string; search?: string; sort?: string; desc?: boolean; offset?: number; limit?: number } = {}) =>
    get<ExtraTable>(`/api/v1/backtests/${encodeURIComponent(runId)}/extras/${encodeURIComponent(name)}`, q),
}

/** A table a strategy exported from its finalize() hook (x_<name>.parquet). */
export interface ExtraTableInfo {
  name: string
  rows: number
  columns: string[]
}

export interface ExtraTable {
  total: number
  columns: string[]
  items: Record<string, unknown>[]
}
