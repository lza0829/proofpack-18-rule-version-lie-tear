async function parseError(r) {
  const text = await r.text()
  try {
    const data = JSON.parse(text)
    const d = data.detail
    if (typeof d === 'string') return d
    if (d && typeof d === 'object') return d.message || JSON.stringify(d)
  } catch {
    /* 非 JSON，原样返回 */
  }
  return text
}

export async function getJSON(url) {
  const r = await fetch(url)
  if (!r.ok) throw new Error(await parseError(r))
  return r.json()
}

export async function postJSON(url, body = {}) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) throw new Error(await parseError(r))
  return r.json()
}
