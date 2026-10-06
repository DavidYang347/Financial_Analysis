export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

type Query = Record<string, string | number | boolean | undefined | null>

function buildUrl(path: string, query?: Query): string {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v))
  }
  const s = qs.toString()
  return s ? `${path}?${s}` : path
}

function detailMessage(body: unknown, status: number): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const d = (body as { detail: unknown }).detail
    if (typeof d === 'string') return d
    // FastAPI validation errors: [{loc, msg}, ...]
    if (Array.isArray(d)) return d.map((x) => (x && typeof x === 'object' && 'msg' in x ? String(x.msg) : String(x))).join('; ')
  }
  if (status === 502 || status === 504) return '后端没有响应，确认 FastAPI 已启动（make dev）'
  return `请求失败（HTTP ${status}）`
}

export async function request<T>(method: 'GET' | 'POST', path: string, opts: { query?: Query; body?: unknown } = {}): Promise<T> {
  let res: Response
  try {
    res = await fetch(buildUrl(path, opts.query), {
      method,
      headers: opts.body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    })
  } catch {
    throw new ApiError(0, '连不上后端，确认 FastAPI 已启动（make dev）')
  }
  const text = await res.text()
  let body: unknown = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = text
  }
  if (!res.ok) throw new ApiError(res.status, detailMessage(body, res.status))
  return body as T
}

export const get = <T>(path: string, query?: Query) => request<T>('GET', path, { query })
export const post = <T>(path: string, body?: unknown) => request<T>('POST', path, { body })

export function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}
