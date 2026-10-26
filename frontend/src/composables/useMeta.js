/**
 * 服务端上下文:下拉选项、ipatool 登录状态、手机端在线状态。
 *
 * 这三项都是「影响用户能不能点下载」的前置条件,放在一起统一刷新,
 * 避免三个组件各自请求、各自维护 loading 状态。
 */

import { computed, onMounted, ref } from 'vue'
import { fetchAccount, fetchDeviceStatus, fetchOptions } from '../api'

export function useMeta() {
  const countries = ref([])
  const platforms = ref([])
  // 账号总览:{ multi_account, accounts: [...], regions: [...] }
  const accountsOverview = ref({ multi_account: false, accounts: [], regions: [] })
  const device = ref({ configured: false, online: false, detail: '正在检测…' })
  const ready = ref(false)

  /** 单账号模式下的那个账号(多账号模式返回 null)。 */
  const account = computed(() => (accountsOverview.value.multi_account ? null : accountsOverview.value.accounts[0] ?? null))

  /** 多账号模式下的账号列表。 */
  const accounts = computed(() => (accountsOverview.value.multi_account ? accountsOverview.value.accounts : []))

  /** 已登录账号覆盖的区域,用于提示哪些国家可以直接下载。 */
  const regions = computed(() => accountsOverview.value.regions)

  /**
   * 已备好离线材料的区域(UnfairPlay 模型 / 越狱机凭证)。
   *
   * 命中的区域不需要设备也能解密:后端会走离线解密。
   * 界面上的「下载解密」按钮据此允许点击。
   */
  const offlineRegions = computed(() => accountsOverview.value.offline_regions ?? [])

  async function loadOptions() {
    const data = await fetchOptions()
    countries.value = data.countries
    platforms.value = data.platforms
  }

  async function refreshAccount() {
    accountsOverview.value = await fetchAccount()
  }

  async function refreshDevice() {
    try {
      device.value = await fetchDeviceStatus()
    } catch (error) {
      // 设备检测失败不应该让整个页面挂掉,转成一条可读状态即可。
      device.value = { configured: true, online: false, detail: error.message }
    }
  }

  /** 并行拉取全部上下文。下拉选项失败是致命错误,由调用方处理。 */
  async function refresh() {
    await Promise.all([loadOptions(), refreshAccount(), refreshDevice()])
  }

  onMounted(async () => {
    try {
      await refresh()
    } finally {
      ready.value = true
    }
  })

  return {
    countries,
    platforms,
    accountsOverview,
    account,
    accounts,
    regions,
    offlineRegions,
    device,
    ready,
    refreshAccount,
    refreshDevice,
    refresh,
  }
}
