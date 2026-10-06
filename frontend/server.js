// Production server for the built frontend (no dependencies).
// Serves dist/ with SPA fallback and proxies /api/* and /health to FastAPI,
// so the browser only ever talks to one origin.
//
//   npm run build && npm start        # http://127.0.0.1:5173
import http from 'node:http'
import { createReadStream } from 'node:fs'
import { stat } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const PORT = Number(process.env.PORT || 5173)
const HOST = process.env.HOST || '127.0.0.1'
const API = new URL(process.env.API_BASE || 'http://127.0.0.1:8000')
const DIST = path.join(path.dirname(fileURLToPath(import.meta.url)), 'dist')

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.json': 'application/json',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.map': 'application/json',
}

function proxy(req, res) {
  const upstream = http.request(
    { hostname: API.hostname, port: API.port, path: req.url, method: req.method, headers: { ...req.headers, host: API.host } },
    (up) => {
      res.writeHead(up.statusCode || 502, up.headers)
      up.pipe(res)
    },
  )
  upstream.on('error', (err) => {
    if (res.headersSent) return res.end()
    res.writeHead(502, { 'content-type': 'application/json' })
    res.end(JSON.stringify({ detail: `backend unreachable at ${API.origin}: ${err.message}` }))
  })
  req.pipe(upstream)
}

async function serveFile(res, file, cache) {
  const ext = path.extname(file)
  res.writeHead(200, {
    'content-type': TYPES[ext] || 'application/octet-stream',
    // Hashed assets never change; index.html must always be revalidated.
    'cache-control': cache ? 'public, max-age=31536000, immutable' : 'no-cache',
  })
  createReadStream(file).pipe(res)
}

async function serveStatic(req, res) {
  const urlPath = decodeURIComponent(new URL(req.url, 'http://x').pathname)
  const file = path.normalize(path.join(DIST, urlPath))
  if (!file.startsWith(DIST)) {
    res.writeHead(400)
    return res.end()
  }
  try {
    const s = await stat(file)
    if (s.isFile()) return serveFile(res, file, urlPath.startsWith('/assets/'))
  } catch {
    /* fall through to SPA index */
  }
  if (path.extname(urlPath)) {
    res.writeHead(404, { 'content-type': 'text/plain' })
    return res.end('not found')
  }
  try {
    await stat(path.join(DIST, 'index.html'))
  } catch {
    res.writeHead(503, { 'content-type': 'text/plain; charset=utf-8' })
    return res.end('frontend not built: run `npm run build` in frontend/ (or use `make dev` for the dev server)')
  }
  return serveFile(res, path.join(DIST, 'index.html'), false)
}

const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/') || req.url === '/health') return proxy(req, res)
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    res.writeHead(405)
    return res.end()
  }
  return serveStatic(req, res)
})

server.listen(PORT, HOST, () => {
  console.log(`frontend on http://${HOST}:${PORT} (API -> ${API.origin})`)
})
