<script setup>
/**
 * 应用外壳:组织三个区块、持有跨区块共享的状态。
 *
 * 页面结构:
 *   1. StatusBar   —— ipatool / 手机端状态
 *   2. SearchBar + AppCard 列表 —— 搜索与发起下载
 *   3. TaskList    —— 进行中的下载任务
 *   4. Library     —— 本地文件库
 *
 * 搜索请求、任务轮询、文件库刷新三个异步流都在这里协调,
 * 子组件只负责渲染和抛事件。
 */
import { ref } from 'vue'
import AppCard from './components/AppCard.vue'
import Library from './components/Library.vue'
import SearchBar from './components/SearchBar.vue'
import StatusBar from './components/StatusBar.vue'
import TaskList from './components/TaskList.vue'
import {
  deleteLibraryItem,
  fetchLibrary,
  searchApps,
  startDecryptedDownload,
} from './api'
import { useMeta } from './composables/useMeta'
import { useTaskPolling } from './composables/useTaskPolling'

const {
  countries,
  platforms,
  account,
  accounts,
  regions,
  offlineRegions,
  device,
  ready,
  refreshAccount,
  refreshDevice,
} = useMeta()

// ---- 搜索 ----------------------------------------------------------------
const results = ref([])
const searchState = ref({ q: '', mode: 'name', country: 'US', platform: 'iphone' })
const searching = ref(false)
const searchError = ref('')
const searchNotice = ref('')
/** 已加入队列的 app_id,用于把按钮置为「已加入队列」。 */
const queuedAppIds = ref(new Set())

async function onSearch(params) {
  searching.value = true
  searchError.value = ''
  searchNotice.value = ''

  try {
    // 结果条数上限。搜索结果是个「挑一个」的列表,一屏能看完最好;
    // 给多了反而要在十几个相似条目里找。调大直接改这个数即可
    // (后端 SEARCH_LIMIT_MAX 是硬上限,默认 25)。
    const data = await searchApps({ ...params, limit: 5 })
    results.value = data.results

    // URL 模式下地区由链接决定。链接带了地区且与界面选的不同时,
    // 明确告诉用户实际用的是哪个地区 —— 否则「我选的美国却搜出抖音」
    // 会让人以为程序出错了。
    if (data.mode === 'url' && data.detected_country && data.detected_country !== params.country) {
      searchNotice.value = `已按链接改用 ${data.detected_country} 区的商店(界面上选的是 ${params.country})`
    }

    searchState.value = {
      q: params.q,
      mode: params.mode,
      country: params.country,
      platform: params.platform,
    }
  } catch (error) {
    results.value = []
    searchError.value = error.message
  } finally {
    searching.value = false
  }
}

// ---- 文件库 --------------------------------------------------------------
const library = ref({ count: 0, encrypted_count: 0, decrypted_count: 0, total_size: 0, items: [] })
const libraryRefreshing = ref(false)
const libraryError = ref('')

async function refreshLibrary() {
  libraryRefreshing.value = true
  try {
    library.value = await fetchLibrary()
    libraryError.value = ''
  } catch (error) {
    libraryError.value = error.message
  } finally {
    libraryRefreshing.value = false
  }
}

async function onDelete(item) {
  try {
    await deleteLibraryItem(item.id)
    await refreshLibrary()
  } catch (error) {
    libraryError.value = error.message
  }
}

// ---- 任务 ----------------------------------------------------------------
// 任务结束时刷新文件库:新下载的文件要立刻出现在列表里。
const { tasks, activeTasks, track, sync } = useTaskPolling(() => refreshLibrary())

const notifyError = ref('')

/**
 * 对文件库里的条目发起砸壳。
 *
 * 与搜索页的下载一样,立即返回任务并进入轮询;区别是这里能拿到
 * app_id / country,直接复用同一条任务通道。
 */
async function onDecrypt(item) {
  notifyError.value = ''

  try {
    const task = await startDecryptedDownload({
      appId: item.app_id,
      country: item.country,
      platform: item.platform,
    })
    track(task)
  } catch (error) {
    notifyError.value = `${item.name}: ${error.message}`
  }
}

/**
 * 对**搜索结果**发起砸壳。
 *
 * 与文件库的 onDecrypt 分开:搜索结果里还没有 library 记录,
 * 传的是 iTuens 返回的 app 对象(字段名不同),不能共用一个函数。
 */
async function onDecryptSearchResult(app) {
  notifyError.value = ''

  try {
    const task = await startDecryptedDownload({
      appId: app.app_id,
      country: app.country,
      platform: app.platform,
    })
    track(task)
    queuedAppIds.value = new Set([...queuedAppIds.value, app.app_id])
  } catch (error) {
    notifyError.value = `${app.name}: ${error.message}`
  }
}

async function onRefreshStatus() {
  await Promise.all([refreshAccount(), refreshDevice()])
}

// ---- 初始化 --------------------------------------------------------------
// 首次进入时并行拉取任务列表与文件库;两者互不依赖。
sync().catch(() => {})
refreshLibrary()
</script>

<template>
  <div class="page">
    <header class="page-header">
      <h1>IPA 下载与砸壳管理</h1>
      <p class="muted">本地自建 · 搜索 · 砸壳解密 · 本地文件库</p>
    </header>

    <StatusBar
      :account="account"
      :accounts="accounts"
      :regions="regions"
      :offline-regions="offlineRegions"
      :device="device"
      :checking="!ready"
      @refresh="onRefreshStatus"
    />

    <SearchBar
      :countries="countries"
      :platforms="platforms"
      :loading="searching"
      :initial="searchState"
      :regions="regions"
      :multi-account="accounts.length > 0"
      @search="onSearch"
    />

    <p v-if="searchNotice" class="alert alert-info">{{ searchNotice }}</p>
    <p v-if="searchError" class="alert alert-error">{{ searchError }}</p>
    <p v-if="notifyError" class="alert alert-error">{{ notifyError }}</p>
    <p v-if="libraryError" class="alert alert-error">{{ libraryError }}</p>

    <section v-if="results.length" class="results">
      <div class="results-header">
        <h2>搜索结果</h2>
        <span class="muted">{{ results.length }} 个应用</span>
      </div>

      <AppCard
        v-for="app in results"
        :key="app.app_id"
        :app="app"
        :device-online="device.online"
        :offline-ready="offlineRegions.includes(app.country)"
        :busy="queuedAppIds.has(app.app_id)"
        @decrypt="onDecryptSearchResult"
      />
    </section>

    <p v-else-if="searchState.q && !searching" class="alert alert-info">
      没有找到「{{ searchState.q }}」。换个关键词,或切换国家 / 平台后再试。
    </p>

    <TaskList :tasks="tasks" />

    <Library
      :library="library"
      :refreshing="libraryRefreshing"
      :device-online="device.online"
      :offline-regions="offlineRegions"
      @refresh="refreshLibrary"
      @delete="onDelete"
      @decrypt="onDecrypt"
    />
  </div>
</template>

<style scoped>
.page {
  max-width: 960px;
  margin: 0 auto;
  padding: 24px 20px 60px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.page-header h1 {
  margin: 0;
  font-size: 21px;
  font-weight: 600;
}

.page-header p {
  margin: 4px 0 0;
  font-size: 13px;
}

.results {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.results-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}

.results-header h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

.results-header span {
  font-size: 12.5px;
}

.alert {
  margin: 0;
  padding: 10px 14px;
  border-radius: var(--radius);
  font-size: 13px;
  border: 1px solid var(--border);
  background: var(--surface);
  /* 后端的错误信息常是多行(带「怎么办」的步骤),需要保留换行 */
  white-space: pre-wrap;
}

.alert-error {
  border-color: #f0c2c2;
  background: var(--danger-soft);
  color: var(--danger);
}

.alert-info {
  color: var(--text-muted);
  text-align: center;
  padding: 24px 14px;
}

/* 有内容时的提示条(与「空结果」那种占位不同,不居中、不占高) */
.alert-info:not(:empty) {
  text-align: left;
  padding: 10px 14px;
  border-color: var(--accent);
  background: var(--accent-soft);
  color: var(--accent-hover);
}
</style>
