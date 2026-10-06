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
  kind?: 'update' | 'repair'
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
  mode: 'full' | 'incremental' | 'repair'
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
  job: MaintenanceJob
  locked: boolean
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
  daily: (symbol: string, adjust: Adjust, start?: string, end?: string) =>
    get<{ symbol: string; adjust: Adjust; count: number; items: Bar[] }>(
      `/api/v1/daily/${encodeURIComponent(symbol)}`, { adjust, start, end }),

  // data management
  status: () => get<DataStatus>('/api/v1/data/status'),
  check: () => get<QualityReport>('/api/v1/data/check'),
  sources: () => get<SourceProbe>('/api/v1/data/sources'),
  startUpdate: (body: { kind: 'update' | 'repair'; symbols?: string[] | null; refresh_meta?: boolean }) =>
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
