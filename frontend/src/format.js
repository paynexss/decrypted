/**
 * 展示层格式化工具。
 *
 * 放在单独文件里,避免每个组件各写一份字节 / 时间格式化。
 */

/** 字节数转可读字符串。null / undefined 显示为 "—"。 */
export function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return '—'
  if (bytes === 0) return '0 B'

  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const value = bytes / 1024 ** exponent

  // 小于 10 时保留一位小数,否则取整 —— 避免出现 "1023.4 MB" 这种信息量很低的精度。
  const digits = exponent === 0 ? 0 : value < 10 ? 1 : 0
  return `${value.toFixed(digits)} ${units[exponent]}`
}

/** ISO-8601 UTC 字符串转本地时间显示。 */
export function formatTime(iso) {
  if (!iso) return '—'

  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso

  const pad = (n) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 价格:0 显示「免费」。 */
export function formatPrice(price) {
  if (price === null || price === undefined) return ''
  return price === 0 ? '免费' : `$${price.toFixed(2)}`
}
