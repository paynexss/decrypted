<script setup>
/**
 * 本地文件库。
 *
 * 每个条目展示加密包 / 解密包两个状态,并给出对应操作:
 *   - 下载解密:文件已存在时是下载链接,不存在时点按发起砸壳;
 *   - 删除:同时移除记录与磁盘文件(由后端完成)。
 *
 * 砸壳有两条路(离线解密 / 设备砸壳),能不能点取决于「设备在线」或
 * 「该区域已备离线材料」—— 具体走哪条由后端决定。
 */
import { computed, ref } from 'vue'
import { fileDownloadUrl } from '../api'
import { formatBytes, formatTime } from '../format'

const props = defineProps({
  library: { type: Object, required: true },
  refreshing: { type: Boolean, default: false },
  deviceOnline: { type: Boolean, default: false },
  /** 已备好离线材料的区域。命中的条目不需要设备就能解密。 */
  offlineRegions: { type: Array, default: () => [] },
})

const emit = defineEmits(['refresh', 'delete', 'decrypt'])

const confirmingId = ref(null)

const summary = computed(() => {
  const { count, decrypted_count: decrypted, total_size: size } = props.library
  return { count, decrypted, size }
})

function confirmDelete(item) {
  confirmingId.value = item.id
}

function cancelDelete() {
  confirmingId.value = null
}

function doDelete(item) {
  confirmingId.value = null
  emit('delete', item)
}

/**
 * 「下载解密」按钮的禁用原因;可点则返回 null。
 *
 * 两条路任一条可用就能点:目标区域的离线材料已就位(离线解密)或砸壳设备在线。
 * 另外的阻断原因(加密包能不能拿到)交给后端 —— 它知道设备上到底有没有
 * 这个应用、账号登没登录,给出的提示比前端猜的准确。
 */
function decryptHint(item) {
  if (props.offlineRegions.includes(item.country)) return null
  if (props.deviceOnline) return null
  return '砸壳设备不在线,且这个区域没有离线材料(连上 iPhone,或先跑 manage_unfairplay.py collect)'
}
</script>

<template>
  <section class="card library">
    <header>
      <div>
        <h2>已保存的 IPA</h2>
        <p class="muted summary">
          {{ summary.count }} 个应用 · 已解密 {{ summary.decrypted }} ·
          共 {{ formatBytes(summary.size) }}
        </p>
      </div>
      <button class="btn btn-sm" :disabled="refreshing" @click="emit('refresh')">
        {{ refreshing ? '刷新中…' : '刷新' }}
      </button>
    </header>

    <p v-if="!library.items.length" class="empty muted">
      文件库还是空的。在上方搜索应用并点击「下载解密」,完成后会出现在这里。
    </p>

    <ul v-else>
      <li v-for="item in library.items" :key="item.id" class="item">
        <div class="icon">
          <img v-if="item.icon_url" :src="item.icon_url" :alt="item.name" loading="lazy" />
          <div v-else class="icon-placeholder">{{ item.name.slice(0, 1) }}</div>
        </div>

        <div class="info">
          <div class="line-1">
            <h3>{{ item.name }}</h3>
            <span v-if="item.version" class="version muted">v{{ item.version }}</span>
          </div>

          <p class="mono muted bundle">{{ item.bundle_id }}</p>

          <div class="badges">
            <span class="badge" :class="item.has_decrypted ? 'is-yes' : 'is-no'">
              解密
              <template v-if="item.has_decrypted">· {{ formatBytes(item.decrypted_size) }}</template>
              <template v-else>· 未砸壳</template>
            </span>
            <span class="badge is-plain">{{ item.country }}</span>
            <span class="badge is-plain">{{ item.platform === 'ipad' ? 'iPadOS' : 'iOS' }}</span>
          </div>

          <p class="time muted">下载时间:{{ formatTime(item.updated_at) }}</p>
        </div>

        <div class="actions">
          <template v-if="confirmingId === item.id">
            <span class="confirm-text">确定删除记录和文件?</span>
            <button class="btn btn-danger btn-sm" @click="doDelete(item)">确定删除</button>
            <button class="btn btn-sm" @click="cancelDelete">取消</button>
          </template>

          <template v-else>
            <a
              v-if="item.has_decrypted"
              class="btn btn-sm"
              :href="fileDownloadUrl(item.id, 'decrypted')"
              download
            >
              下载解密
            </a>
            <button
              v-else
              class="btn btn-primary btn-sm"
              :disabled="decryptHint(item) !== null"
              :title="decryptHint(item) ?? (offlineRegions.includes(item.country) ? '在本地离线解密(不需要设备)' : '在越狱设备上砸壳,直接得到解密后的 IPA')"
              @click="emit('decrypt', item)"
            >
              下载解密
            </button>

            <button class="btn btn-danger btn-sm" @click="confirmDelete(item)">删除</button>
          </template>
        </div>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.library {
  padding: 16px;
}

header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

header h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.summary {
  margin: 4px 0 0;
  font-size: 12.5px;
}

.empty {
  margin: 0;
  padding: 26px 0;
  text-align: center;
  font-size: 13px;
}

ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.item {
  display: flex;
  gap: 13px;
  align-items: flex-start;
  padding: 13px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
}

.icon {
  flex-shrink: 0;
  width: 48px;
  height: 48px;
}

.icon img {
  width: 100%;
  height: 100%;
  border-radius: 10px;
  border: 1px solid var(--border);
  object-fit: cover;
  display: block;
}

.icon-placeholder {
  width: 100%;
  height: 100%;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--surface-sunken);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 19px;
  font-weight: 600;
  color: var(--text-faint);
}

.info {
  flex: 1;
  min-width: 0;
}

.line-1 {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.line-1 h3 {
  margin: 0;
  font-size: 14.5px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.version {
  font-size: 12.5px;
  flex-shrink: 0;
}

.bundle {
  margin: 3px 0 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.badges {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 7px;
}

.badge {
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  border: 1px solid var(--border);
  background: var(--surface-sunken);
  color: var(--text-muted);
}

.badge.is-yes {
  border-color: #cbe6d1;
  background: var(--accent-soft);
  color: var(--accent-hover);
}

.badge.is-no {
  color: var(--text-faint);
}

.badge.is-plain {
  background: transparent;
}

.time {
  margin: 6px 0 0;
  font-size: 12px;
}

.actions {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  justify-content: flex-end;
  max-width: 260px;
}

.confirm-text {
  font-size: 12.5px;
  color: var(--danger);
}

a.btn {
  text-decoration: none;
}
</style>
