<script setup>
/**
 * 单条搜索结果。
 *
 * 只有一个动作:砸壳解密。设备不在线时禁用并说明原因。
 *
 * 砸壳是直接读设备上已装应用的内存。设备上没有该应用时,
 * 后端会自动下载安装包并装上去,界面上不需要额外操作。
 */
import { computed, ref } from 'vue'
import { formatBytes, formatPrice } from '../format'

const props = defineProps({
  app: { type: Object, required: true },
  /** 砸壳设备是否在线。不在线时只有「离线解密」可用。 */
  deviceOnline: { type: Boolean, default: false },
  /** 这个应用所属区域是否已备好离线材料(有的话不需要设备)。 */
  offlineReady: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
})

const emit = defineEmits(['decrypt'])

const iconFailed = ref(false)

const showIcon = computed(() => Boolean(props.app.icon_url) && !iconFailed.value)

/** 两条路任一条可用就能点:离线解密不需要设备。 */
const canDecrypt = computed(() => props.deviceOnline || props.offlineReady)

const decryptTitle = computed(() => {
  if (props.offlineReady) return '在本地离线解密(不需要设备)'
  if (props.deviceOnline) return '在越狱设备上砸壳,直接得到解密后的 IPA'
  return '离线材料未就绪,且砸壳设备不在线'
})

function onDecrypt() {
  emit('decrypt', props.app)
}
</script>

<template>
  <article class="card appcard">
    <div class="icon">
      <img v-if="showIcon" :src="app.icon_url" :alt="app.name" loading="lazy" @error="iconFailed = true" />
      <div v-else class="icon-placeholder">{{ app.name.slice(0, 1) }}</div>
    </div>

    <div class="info">
      <div class="line-1">
        <h3>{{ app.name }}</h3>
        <span v-if="app.price !== null && app.price !== undefined" class="price">
          {{ formatPrice(app.price) }}
        </span>
      </div>

      <p class="mono muted bundle">{{ app.bundle_id }}</p>

      <div class="meta muted">
        <span>{{ app.platform === 'ipad' ? 'iPadOS' : 'iOS' }}</span>
        <span class="sep">·</span>
        <span>{{ app.country }}</span>
        <template v-if="app.version">
          <span class="sep">·</span>
          <span>v{{ app.version }}</span>
        </template>
        <template v-if="app.file_size_bytes">
          <span class="sep">·</span>
          <span>{{ formatBytes(app.file_size_bytes) }}</span>
        </template>
        <template v-if="app.minimum_os_version">
          <span class="sep">·</span>
          <span>需 iOS {{ app.minimum_os_version }}+</span>
        </template>
      </div>

      <p v-if="app.seller_name" class="seller muted">{{ app.seller_name }}</p>
    </div>

    <div class="actions">
      <button
        class="btn btn-primary btn-sm"
        :disabled="!canDecrypt || busy"
        :title="decryptTitle"
        @click="onDecrypt"
      >
        {{ busy ? '已加入队列' : '下载解密' }}
      </button>

      <span v-if="!canDecrypt" class="locked">需设备在线,或该区域已备离线材料</span>
    </div>
  </article>
</template>

<style scoped>
.appcard {
  display: flex;
  gap: 14px;
  align-items: flex-start;
  padding: 14px;
}

.icon {
  flex-shrink: 0;
  width: 56px;
  height: 56px;
}

.icon img {
  width: 100%;
  height: 100%;
  border-radius: 12px;
  border: 1px solid var(--border);
  object-fit: cover;
  display: block;
}

.icon-placeholder {
  width: 100%;
  height: 100%;
  border-radius: 12px;
  border: 1px solid var(--border);
  background: var(--surface-sunken);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
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
  font-size: 15px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.price {
  font-size: 12.5px;
  color: var(--accent-hover);
  flex-shrink: 0;
}

.bundle {
  margin: 3px 0 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.meta,
.seller {
  margin: 5px 0 0;
  font-size: 12.5px;
}

.sep {
  margin: 0 5px;
  color: var(--text-faint);
}

.actions {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 5px;
}

.locked {
  font-size: 12px;
  color: var(--text-faint);
}
</style>
