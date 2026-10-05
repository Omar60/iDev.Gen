const SQLITE_VERSION_MAX = '9223372036854775807'

export function versionText(value) {
  const text = typeof value === 'string'
    ? value
    : typeof value === 'number' && Number.isSafeInteger(value) ? String(value) : ''
  if (!/^[1-9]\d*$/.test(text)) return null
  if (text.length > SQLITE_VERSION_MAX.length
      || (text.length === SQLITE_VERSION_MAX.length && text > SQLITE_VERSION_MAX)) return null
  return text
}

export function parseVersionJson(source) {
  let index = 0
  const output = []
  const whitespace = () => {
    while (index < source.length && /\s/.test(source[index])) output.push(source[index++])
  }
  const stringToken = () => {
    const start = index
    if (source[index++] !== '"') throw new SyntaxError('Invalid JSON string.')
    while (index < source.length) {
      const char = source[index++]
      if (char === '"') return source.slice(start, index)
      if (char === '\\') index += 1
      else if (char.charCodeAt(0) < 0x20) throw new SyntaxError('Invalid JSON string.')
    }
    throw new SyntaxError('Invalid JSON string.')
  }
  const numberToken = (preserveVersion) => {
    const start = index
    if (source[index] === '-') index += 1
    if (source[index] === '0') index += 1
    else if (/[1-9]/.test(source[index] || '')) {
      index += 1
      while (/\d/.test(source[index] || '')) index += 1
    } else throw new SyntaxError('Invalid JSON number.')
    if (source[index] === '.') {
      index += 1
      if (!/\d/.test(source[index] || '')) throw new SyntaxError('Invalid JSON number.')
      while (/\d/.test(source[index] || '')) index += 1
    }
    if (source[index] === 'e' || source[index] === 'E') {
      index += 1
      if (source[index] === '+' || source[index] === '-') index += 1
      if (!/\d/.test(source[index] || '')) throw new SyntaxError('Invalid JSON number.')
      while (/\d/.test(source[index] || '')) index += 1
    }
    const token = source.slice(start, index)
    const integerToken = /^-?(?:0|[1-9]\d*)$/.test(token)
    if (preserveVersion && !integerToken) {
      // Keep invalid numeric spellings out of Number conversion; versionText rejects them.
      output.push(JSON.stringify(token))
    } else if (preserveVersion && !Number.isSafeInteger(Number(token))) {
      output.push(JSON.stringify(token))
    } else output.push(token)
  }
  const value = (preserveVersion = false) => {
    whitespace()
    const char = source[index]
    if (char === '{') {
      output.push(source[index++])
      whitespace()
      if (source[index] === '}') { output.push(source[index++]); return }
      while (index < source.length) {
        whitespace()
        const keyToken = stringToken()
        output.push(keyToken)
        const key = JSON.parse(keyToken)
        whitespace()
        if (source[index++] !== ':') throw new SyntaxError('Invalid JSON object.')
        output.push(':')
        value(key === 'version' || key === 'expected_version')
        whitespace()
        const separator = source[index++]
        output.push(separator)
        if (separator === '}') return
        if (separator !== ',') throw new SyntaxError('Invalid JSON object.')
      }
      throw new SyntaxError('Invalid JSON object.')
    }
    if (char === '[') {
      output.push(source[index++])
      whitespace()
      if (source[index] === ']') { output.push(source[index++]); return }
      while (index < source.length) {
        value()
        whitespace()
        const separator = source[index++]
        output.push(separator)
        if (separator === ']') return
        if (separator !== ',') throw new SyntaxError('Invalid JSON array.')
      }
      throw new SyntaxError('Invalid JSON array.')
    }
    if (char === '"') { output.push(stringToken()); return }
    if (char === '-' || /\d/.test(char || '')) { numberToken(preserveVersion); return }
    for (const literal of ['true', 'false', 'null']) {
      if (source.startsWith(literal, index)) {
        output.push(literal)
        index += literal.length
        return
      }
    }
    throw new SyntaxError('Invalid JSON value.')
  }

  if (typeof source !== 'string') throw new TypeError('JSON source must be text.')
  value()
  whitespace()
  if (index !== source.length) throw new SyntaxError('Unexpected JSON content.')
  return JSON.parse(output.join(''))
}

function versionedBody(body, integerFields) {
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    throw new TypeError('Versioned JSON requests require an object body.')
  }
  const fields = new Set(integerFields)
  const seen = new Set()
  const serialize = (value, path) => {
    if (fields.has(path)) {
      const rawValue = versionText(value)
      if (rawValue === null) throw new TypeError(`Invalid ${path} version.`)
      seen.add(path)
      return rawValue
    }
    if (Array.isArray(value)) {
      return `[${value.map((item, index) => serialize(item, path ? `${path}.${index}` : String(index)) ?? 'null').join(',')}]`
    }
    if (value && typeof value === 'object') {
      return `{${Object.entries(value)
        .filter(([, item]) => item !== undefined && typeof item !== 'function' && typeof item !== 'symbol')
        .map(([key, item]) => `${JSON.stringify(key)}:${serialize(item, path ? `${path}.${key}` : key)}`)
        .join(',')}}`
    }
    return JSON.stringify(value)
  }
  const encoded = serialize(body, '')
  for (const path of fields) {
    if (!seen.has(path)) throw new TypeError(`Missing ${path} version.`)
  }
  return encoded
}

async function request(method, path, body, options = {}) {
  const { losslessVersions = false, integerFields = [] } = options
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? (integerFields.length ? versionedBody(body, integerFields) : JSON.stringify(body)) : undefined,
  })
  if (!res.ok) {
    let detail = res.statusText
    let hasStableErrorBody = false
    try {
      detail = (await res.json()).detail ?? detail
      const keys = detail && typeof detail === 'object' ? Object.keys(detail).sort() : []
      hasStableErrorBody = keys.length === 2 && keys[0] === 'code' && keys[1] === 'message'
        && typeof detail.code === 'string' && Boolean(detail.code)
        && typeof detail.message === 'string' && Boolean(detail.message)
    } catch { /* response was not JSON */ }
    const err = new Error(typeof detail === 'string' ? detail : (detail?.message || JSON.stringify(detail)))
    err.detail = detail
    err.status = res.status
    err.hasStableErrorBody = hasStableErrorBody
    throw err
  }
  let data = null
  if (res.status !== 204) {
    if (losslessVersions) data = parseVersionJson(await res.text())
    else data = await res.json()
  }
  return { status: res.status, data }
}

const req = async (method, path, body, options) => (await request(method, path, body, options)).data

// The File goes as the raw body, not as multipart: the server reads the bytes
// and sniffs the format from them, so there is nothing a form part would add.
async function upload(path, file) {
  const res = await fetch(path + `?label=${encodeURIComponent(file.name.replace(/\.[^.]+$/, ''))}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream' },
    body: file,
  })
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail ?? detail } catch { /* response was not JSON */ }
    const err = new Error(typeof detail === 'string' ? detail : (detail?.message || JSON.stringify(detail)))
    err.detail = detail
    err.status = res.status
    throw err
  }
  return res.json()
}

async function uploadMultipart(path, formData) {
  const res = await fetch(path, {
    method: 'POST',
    body: formData,
  })
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail ?? detail } catch { /* response was not JSON */ }
    const err = new Error(typeof detail === 'string' ? detail : (detail?.message || JSON.stringify(detail)))
    err.detail = detail
    err.status = res.status
    throw err
  }
  return res.status === 204 ? null : res.json()
}

export const api = {
  get: (p, options) => req('GET', p, undefined, options),
  post: (p, b, options) => req('POST', p, b, options),
  getVersioned: (p) => api.get(p, { losslessVersions: true }),
  postVersioned: (p, b, integerFields = []) => api.post(p, b, { losslessVersions: true, integerFields }),
  postWithStatus: (p, b) => request('POST', p, b),
  patch: (p, b) => req('PATCH', p, b),
  del: (p) => req('DELETE', p),
  upload,
  uploadMultipart,
}

export const shotImage = (id) => `/api/shots/${id}/image`
export const loraPreview = (name) => `/api/loras/preview?name=${encodeURIComponent(name)}`
