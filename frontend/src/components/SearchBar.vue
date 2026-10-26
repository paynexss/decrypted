<script setup>
/**
 * 搜索区:搜索方式、平台、国家、关键词。
 *
 * 组件本身不发起请求,只负责收集条件并向上抛出 search 事件 ——
 * 请求状态与结果列表由父组件统一持有。
 */
import { computed, ref } from 'vue'

const props = defineProps({
  countries: { type: Array, required: true },
  platforms: { type: Array, required: true },
  loading: { type: Boolean, default: false },
  initial: { type: Object, default: () => ({}) },
  /** 已登录账号覆盖的区域。为空表示没有可用账号(或未配置多账号)。 */
  regions: { type: Array, default: () => [] },
  /** 是否处于多账号模式。 */
  multiAccount: { type: Boolean, default: false },
})

const emit = defineEmits(['search'])

const mode = ref(props.initial.mode ?? 'name')
const country = ref(props.initial.country ?? 'US')
const platform = ref(props.initial.platform ?? 'iphone')
const query = ref(props.initial.query ?? '')

const PLACEHOLDERS = {
  name: '例如:微信 / Procreate',
  bundle_id: '例如:com.tencent.xin',
  url: '粘贴 App Store 应用链接,例如 https://apps.apple.com/cn/app/id1142110895',
}

const placeholder = computed(() => PLACEHOLDERS[mode.value] ?? '')

/**
 * 链接模式下的即时校验。
 *
 * 提前拦一道:粘错了(比如粘了网页版首页)能在点搜索之前就看出问题,
 * 而不是等后端返回 400。校验规则与后端 parse_app_store_url 保持一致。
 */
const urlInvalid = computed(() => {
  if (mode.value !== 'url') return false
  const text = query.value.trim()
  if (!text) return false
  return !/\/id\d+/.test(text)
})

function submit() {
  const trimmed = query.value.trim()
  if (!trimmed || props.loading) return

  emit('search', {
    q: trimmed,
    mode: mode.value,
    country: country.value,
    platform: platform.value,
  })
}

/**
 * 当前选中的国家是否有可用账号。
 *
 * 多账号模式下,选一个没有账号的国家再点下载会被后端拒绝 ——
 * 提前在界面上标出来,比让用户点了才知道要好。
 * regions 为空(未配置多账号)时不显示任何提示,保持原有行为。
 */
const regionHint = computed(() => {
  // 链接模式下地区由链接决定,界面上选的国家不参与,提示会误导人
  if (mode.value === 'url') return null
  if (!props.multiAccount || props.regions.length === 0) return null
  if (props.regions.includes(country.value)) return null
  return `当前没有 ${country.value} 区域的登录账号,搜索结果可以浏览,但下载会被拒绝。`
})
</script>

<template>
  <section class="card searchbar">
    <header>
      <h1>查找应用</h1>
      <p class="muted">从 App Store 搜索并保存 IPA 到本地文件库</p>
    </header>

    <div class="controls">
      <div class="segmented">
        <button :class="{ active: mode === 'name' }" @click="mode = 'name'">应用名称</button>
        <button :class="{ active: mode === 'bundle_id' }" @click="mode = 'bundle_id'">Bundle ID</button>
        <button :class="{ active: mode === 'url' }" @click="mode = 'url'">App Store 链接</button>
      </div>

      <select v-model="platform" class="field">
        <option v-for="item in platforms" :key="item.value" :value="item.value">
          {{ item.name }}
        </option>
      </select>

      <select v-model="country" class="field">
        <option v-for="item in countries" :key="item.code" :value="item.code">
          {{ item.name }} ({{ item.code }})
        </option>
      </select>

      <input
        v-model="query"
        class="field query"
        type="text"
        :placeholder="placeholder"
        @keyup.enter="submit"
      />

      <button
        class="btn btn-primary"
        :disabled="loading || !query.trim() || urlInvalid"
        @click="submit"
      >
        {{ loading ? '搜索中…' : '搜索' }}
      </button>
    </div>

    <p v-if="urlInvalid" class="hint warn">
      这不像 App Store 应用链接。需要包含应用 ID,形如
      <code>https://apps.apple.com/cn/app/id1142110895</code>
    </p>
    <p v-else-if="regionHint" class="hint warn">{{ regionHint }}</p>
    <p v-else class="hint muted">
      国家选项影响<strong>搜索结果</strong>;下载时按它自动挑选对应区域的账号。
    </p>
  </section>
</template>

<style scoped>
.searchbar {
  padding: 18px;
}

header h1 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}

header p {
  margin: 4px 0 16px;
  font-size: 13px;
}

.controls {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.segmented {
  display: inline-flex;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: var(--surface);
}

.segmented button {
  padding: 7px 14px;
  border: none;
  background: transparent;
  color: var(--text-muted);
}

.segmented button.active {
  background: var(--accent-soft);
  color: var(--accent-hover);
  font-weight: 500;
}

.query {
  flex: 1;
  min-width: 220px;
}

.hint {
  margin: 12px 0 0;
  font-size: 12.5px;
}

.warn {
  color: var(--warning);
}

code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  padding: 1px 5px;
  border-radius: 4px;
  background: var(--surface-sunken);
}
</style>
