import { chromium } from '@playwright/test'
import { createHash } from 'node:crypto'

const media = process.argv[2]
if (!['screen', 'print'].includes(media)) throw new Error('Choose screen or print media.')
const chunks = []
for await (const chunk of process.stdin) chunks.push(chunk)
const source = Buffer.concat(chunks)
const browser = await chromium.launch({ channel: 'chrome', headless: true })
try {
  const viewport = { width: 1280, height: 900 }
  const page = await browser.newPage({ viewport, javaScriptEnabled: false, serviceWorkers: 'block' })
  const origin = 'https://artifact.invalid/'
  const csp = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; form-action 'none'; base-uri 'none'"
  let loaded = false
  await page.route('**/*', route => {
    if (!loaded && route.request().url() === origin && route.request().isNavigationRequest()) {
      loaded = true
      return route.fulfill({ body: source, headers: { 'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': csp } })
    }
    return route.abort()
  })
  await page.emulateMedia({ media })
  await page.goto(origin)
  const text = await page.evaluate(() => {
    if (document.querySelector('link[rel~="stylesheet"],iframe,frame,object,embed,meta[http-equiv="refresh" i],script:not([type="application/json"])')
        || [...document.querySelectorAll('style')].some(style => /@import\b/i.test(style.textContent))) {
      throw new Error('This review requires self-contained static HTML with inline styles.')
    }
    const body = document.body
    if (!body || !body.getClientRects().length) return ''
    return body.innerText
  })
  process.stdout.write(JSON.stringify({ text, media, viewport, browser: browser.version(),
    source_sha256: createHash('sha256').update(source).digest('hex') }))
} finally {
  await browser.close()
}
