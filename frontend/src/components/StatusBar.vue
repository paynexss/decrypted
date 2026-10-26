<script setup>
/**
 * 顶部状态条:账号(单账号或多账号)+ 手机端在线状态。
 *
 * 登录动作不在页面里做 —— `ipatool auth login` 需要交互式输入 2FA,
 * 这里只展示状态并给出该执行的命令。
 */
import { computed, ref } from 'vue'

const props = defineProps({
  /** 单账号模式下的账号对象;多账号模式为 null。 */
  account: { type: Object, default: null },
  /** 多账号模式下的账号列表。 */
  accounts: { type: Array, default: () => [] },
  regions: { type: Array, default: () => [] },
  /** 已备好离线材料的区域(UnfairPlay 模型 / 越狱机凭证)。 */
  offlineRegions: { type: Array, default: () => [] },
  device: { type: Object, required: true },
  checking: { type: Boolean, default: false },
})

const emit = defineEmits(['refresh'])

const showHelp = ref(false)
const showAccounts = ref(false)

const isMulti = computed(() => props.accounts.length > 0)
const anyLoggedIn = computed(() =>
  isMulti.value ? props.accounts.some((item) => item.logged_in) : Boolean(props.account?.logged_in),
)

/** 区域码 -> 中文短名。列表里只列常用地区,其余直接显示代码。 */
const REGION_LABELS = {
  CN: '中国大陆', US: '美国', HK: '中国香港', TW: '中国台湾',
  JP: '日本', KR: '韩国', SG: '新加坡', GB: '英国',
  DE: '德国', FR: '法国', CA: '加拿大', AU: '澳大利亚', IN: '印度',
}

function regionLabel(code) {
  return REGION_LABELS[code] ?? code
}
</script>

<template>
  <div class="statusbar">
    <!-- 单账号模式:一行登录状态 -->
    <div v-if="!isMulti" class="status" :class="anyLoggedIn ? 'is-ok' : 'is-warn'">
      <span class="dot" />
      <span v-if="anyLoggedIn">
        ipatool 已登录:<strong>{{ account.name || account.email }}</strong>
      </span>
      <span v-else>ipatool 未登录 —— 可以搜索,但无法下载</span>
    </div>

    <!-- 多账号模式:区域芯片 + 可展开的账号列表 -->
    <template v-else>
      <button class="status is-toggle" :class="anyLoggedIn ? 'is-ok' : 'is-warn'" @click="showAccounts = !showAccounts">
        <span class="dot" />
        <span v-if="regions.length">
          可下载区域:<strong>{{ regions.map(regionLabel).join(' / ') }}</strong>
          <span class="muted">({{ accounts.filter((a) => a.logged_in).length }}/{{ accounts.length }} 个账号)</span>
        </span>
        <span v-else>没有已登录的账号 —— 可以搜索,但无法下载</span>
        <span class="chevron">{{ showAccounts ? '收起' : '展开' }}</span>
      </button>
    </template>

    <div class="status" :class="device.online ? 'is-ok' : 'is-idle'">
      <span class="dot" />
      <span v-if="device.online">手机端在线({{ device.base_url }})</span>
      <span v-else-if="!device.configured">手机端未配置(第二阶段)</span>
      <span v-else>手机端离线</span>
    </div>

    <!-- 离线解密就绪的区域。这两条路是「或」的关系:没有设备也能砸壳。 -->
    <div v-if="offlineRegions.length" class="status is-ok" title="这些区域的账号已备好离线材料,解密不需要设备">
      <span class="dot" />
      <span>离线解密:<strong>{{ offlineRegions.map(regionLabel).join(' / ') }}</strong></span>
    </div>

    <div class="spacer" />

    <button class="btn btn-sm" :disabled="checking" @click="emit('refresh')">
      {{ checking ? '检测中…' : '刷新状态' }}
    </button>
    <button class="btn btn-sm" @click="showHelp = !showHelp">登录说明</button>
  </div>

  <!-- 多账号明细 -->
  <div v-if="isMulti && showAccounts" class="panel">
    <table>
      <thead>
        <tr>
          <th>区域</th>
          <th>账号</th>
          <th>状态</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in accounts" :key="item.id">
          <td>
            <span class="region-chip">{{ item.region ? regionLabel(item.region) : '未探测' }}</span>
          </td>
          <td>
            <div class="email">{{ item.email || item.label || item.id }}</div>
            <div v-if="item.name" class="muted small">{{ item.name }}</div>
          </td>
          <td>
            <span v-if="item.logged_in" class="ok">已登录</span>
            <span v-else class="bad" :title="item.error || ''">未登录</span>
            <div v-if="item.offline_ready" class="muted small">离线解密就绪</div>
          </td>
        </tr>
      </tbody>
    </table>
    <p class="muted small hint">
      下载时按搜索选择的<strong>国家</strong>自动挑选对应区域的账号。没有匹配账号时会直接拒绝,
      不会用错误的账号去尝试(那样只会得到误导性的 “app not found”)。
    </p>
  </div>

  <div v-if="showHelp" class="panel">
    <p>下载功能依赖 <code>ipatool</code> 的登录会话。登录必须在终端里手动完成:</p>
    <pre><code>ipatool auth login -e &lt;Apple ID&gt; -p '&lt;密码&gt;'</code></pre>
    <p class="muted">
      首次登录可能提示需要 2FA 验证码,按提示补充即可(非交互模式下改用
      <code>--auth-code</code> 参数)。登录凭据由 ipatool 自己加密保存在系统钥匙串里,
      本服务不接触、也不存储你的 Apple ID 密码。
    </p>
    <p v-if="isMulti" class="muted">
      多账号模式下,每个账号要带上自己的命名空间前缀(见 backend/accounts.json
      里对应账号的 <code>keychain_service</code> 与 <code>cookie_jar</code>):
    </p>
    <pre v-if="isMulti"><code>IPATOOL_KEYCHAIN_SERVICE=&lt;keychain_service&gt; \
IPATOOL_COOKIE_JAR=&lt;cookie_jar&gt; \
ipatool auth login -e &lt;Apple ID&gt; -p '&lt;密码&gt;'</code></pre>
  </div>
</template>

<style scoped>
.statusbar {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  padding: 10px 14px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.status {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 13px;
}

.is-toggle {
  border: none;
  background: transparent;
  padding: 0;
  color: inherit;
  cursor: pointer;
  font-family: inherit;
}

.chevron {
  color: var(--text-faint);
  font-size: 12px;
  text-decoration: underline dotted;
}

.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--text-faint);
}

.is-ok .dot {
  background: var(--accent);
}

.is-warn .dot {
  background: var(--warning);
}

.is-idle .dot {
  background: var(--text-faint);
}

.spacer {
  flex: 1;
}

.panel {
  margin-top: 10px;
  padding: 12px 14px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 13px;
}

.panel p {
  margin: 0 0 8px;
}

.panel p:last-child {
  margin-bottom: 0;
}

.panel pre {
  margin: 0 0 8px;
  padding: 10px 12px;
  background: var(--surface-sunken);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  margin-bottom: 10px;
}

th {
  text-align: left;
  font-weight: 500;
  color: var(--text-muted);
  font-size: 12.5px;
  padding: 0 10px 6px 0;
}

td {
  padding: 7px 10px 7px 0;
  border-top: 1px solid var(--border);
  vertical-align: top;
}

.region-chip {
  display: inline-block;
  padding: 2px 9px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent-hover);
  font-size: 12.5px;
  white-space: nowrap;
}

.email {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
  word-break: break-all;
}

.ok {
  color: var(--accent-hover);
  font-size: 12.5px;
}

.bad {
  color: var(--warning);
  font-size: 12.5px;
}

.small {
  font-size: 12px;
}

.hint {
  margin-top: 4px;
}

code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12.5px;
}
</style>
