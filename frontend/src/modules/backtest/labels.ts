import type { BacktestSettings } from '@/api'

export const REBALANCE_LABEL: Record<string, string> = {
  daily: '每日',
  weekly: '每周末',
  monthly: '每月末',
  every_n: '每 N 个交易日',
}

export const FILL_LABEL: Record<string, string> = {
  next_open: '次日开盘价',
  close: '当日收盘价',
}

export function benchmarkLabel(b: string | undefined | null): string {
  if (!b || b === 'none') return '无'
  if (b === 'equal') return '全市场等权'
  return b
}

/** Fraction -> "12.34%" (signed). */
export function pct(v: number | null | undefined, digits = 2, signed = true): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '–'
  const n = v * 100
  return `${signed && n > 0 ? '+' : ''}${n.toFixed(digits)}%`
}

export function num(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '–'
  return v.toFixed(digits)
}

export function sign(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v) || v === 0) return ''
  return v > 0 ? 'up' : 'down'
}

export function settingsText(s: Partial<BacktestSettings>): string {
  const parts = [
    s.start && s.end ? `${s.start} ~ ${s.end}` : '',
    s.rebalance ? `调仓 ${s.rebalance === 'every_n' ? `每 ${s.every_n} 日` : REBALANCE_LABEL[s.rebalance]}` : '',
    s.fill ? `${FILL_LABEL[s.fill]}成交` : '',
    s.benchmark ? `基准 ${benchmarkLabel(s.benchmark)}` : '',
  ]
  return parts.filter(Boolean).join('，')
}

export function paramText(p: Record<string, unknown>): string {
  return Object.entries(p).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join('/') : v}`).join('  ')
}

export const SERIES_COLORS = ['#2b4c7e', '#c58a14', '#8a4fb8', '#1e9e5a', '#d9363e', '#5b7083']
