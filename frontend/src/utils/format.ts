export function fmtNum(v: unknown, digits = 2): string {
  if (v === null || v === undefined || v === '') return '–'
  const n = Number(v)
  if (!Number.isFinite(n)) return String(v)
  return n.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function fmtInt(v: unknown): string {
  if (v === null || v === undefined) return '–'
  const n = Number(v)
  return Number.isFinite(n) ? Math.round(n).toLocaleString('zh-CN') : String(v)
}

/** 12345678 -> "1234.57万", 1.2e9 -> "12.00亿" */
export function fmtBig(v: unknown): string {
  if (v === null || v === undefined) return '–'
  const n = Number(v)
  if (!Number.isFinite(n)) return '–'
  const a = Math.abs(n)
  if (a >= 1e8) return `${(n / 1e8).toFixed(2)}亿`
  if (a >= 1e4) return `${(n / 1e4).toFixed(2)}万`
  return n.toFixed(0)
}

export function fmtPct(v: unknown, digits = 2, signed = true): string {
  if (v === null || v === undefined) return '–'
  const n = Number(v)
  if (!Number.isFinite(n)) return '–'
  return `${signed && n > 0 ? '+' : ''}${n.toFixed(digits)}%`
}

export function dirClass(v: unknown): string {
  const n = Number(v)
  if (!Number.isFinite(n) || n === 0) return ''
  return n > 0 ? 'up' : 'down'
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '–'
  return iso.replace('T', ' ').slice(0, 19)
}

export function fmtDuration(sec: number | null | undefined): string {
  if (sec === null || sec === undefined) return '–'
  if (sec < 60) return `${sec.toFixed(1)} 秒`
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return m < 60 ? `${m} 分 ${s} 秒` : `${Math.floor(m / 60)} 小时 ${m % 60} 分`
}

export const RUN_MODE_LABEL: Record<string, string> = {
  full: '全量下载',
  incremental: '增量更新',
  repair: '修复',
  names: '简称 / ST 历史',
}

export const NAME_METHOD_LABEL: Record<string, string> = {
  szse: '深交所简称变更',
  tushare: 'Tushare',
  baostock: 'BaoStock 日线 ST 标记',
  never_st: '从未 ST（东方财富曾用名）',
  undated: '曾经 ST，尚无日期（按当前名称）',
  missing: '无记录（按当前名称）',
}

export const SOURCE_LABEL: Record<string, string> = {
  tencent: '腾讯',
  mootdx: '通达信',
  eastmoney: '东方财富',
  baostock: 'BaoStock',
  akshare: 'AKShare',
  tushare: 'Tushare',
}
