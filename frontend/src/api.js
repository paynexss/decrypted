/**
 * 后端 API 客户端。
 *
 * 所有请求统一走 /api 前缀:开发模式下由 Vite 代理到后端,
 * 生产模式下由 FastAPI 同源托管,前端代码无需区分环境。
 */

const BASE = '/api'

/** 携带后端 detail 字段的请求错误。 */
export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })

  const text = await response.text()
  const payload = text ? safeParse(text) : null

  if (!response.ok) {
    // FastAPI 的错误体统一是 {"detail": ...};解析失败时退回 HTTP 状态码,
    // 至少让用户知道发生了什么,而不是一个空消息。
    const detail = payload && typeof payload.detail === 'string' ? payload.detail : `请求失败(HTTP ${response.status})`
    throw new ApiError(detail, response.status)
  }

  return payload
}

function safeParse(text) {
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

// ---------------------------------------------------------------------------
// 查询
// ---------------------------------------------------------------------------
export function fetchOptions() {
  return request('/meta/options')
}

export function fetchAccount() {
  return request('/account')
}

export function fetchDeviceStatus() {
  return request('/device/status')
}

export function searchApps({ q, mode, country, platform, limit }) {
  const params = new URLSearchParams({ q, mode, country, platform, limit: String(limit) })
  return request(`/search?${params}`)
}

export function fetchLibrary() {
  return request('/library')
}

export function fetchTasks() {
  return request('/tasks')
}

export function fetchTask(taskId) {
  return request(`/tasks/${taskId}`)
}

// ---------------------------------------------------------------------------
// 变更
// ---------------------------------------------------------------------------
export function startEncryptedDownload({ appId, country, platform }) {
  return request('/download/encrypted', {
    method: 'POST',
    body: JSON.stringify({ app_id: appId, country, platform }),
  })
}

export function startDecryptedDownload({ appId, country, platform }) {
  return request('/download/decrypted', {
    method: 'POST',
    body: JSON.stringify({ app_id: appId, country, platform }),
  })
}

export function deleteLibraryItem(id) {
  return request(`/library/${id}`, { method: 'DELETE' })
}

/** 文件下载走浏览器原生下载,不经过 fetch —— 大文件不应进内存。 */
export function fileDownloadUrl(id, type) {
  return `${BASE}/library/${id}/file?type=${type}`
}
