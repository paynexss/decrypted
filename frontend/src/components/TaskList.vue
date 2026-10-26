<script setup>
/**
 * 下载任务列表。仅在有任务或刚有任务结束时显示。
 *
 * 进度是估算值:后端按 iTunes 报告的体积算百分比,实际包大小可能不同,
 * 因此界面上把「已下载字节数」和进度条一起显示,后者不作为精确承诺。
 */
import { formatBytes } from '../format'

defineProps({
  tasks: { type: Array, required: true },
})

const STAGE_LABEL = {
  queued: '排队中',
  downloading: '下载中',
  installing: '装到设备',
  dumping: '砸壳中',
  decrypting: '离线解密',
  archiving: '归档中',
  finished: '已完成',
}

function stageLabel(task) {
  if (task.status === 'failed') return '失败'
  if (task.status === 'success') return '完成'
  return STAGE_LABEL[task.stage] ?? task.stage
}
</script>

<template>
  <section v-if="tasks.length" class="card tasks">
    <header>
      <h2>下载任务</h2>
      <span class="muted">{{ tasks.length }} 条</span>
    </header>

    <ul>
      <li v-for="task in tasks" :key="task.task_id" class="task" :class="`is-${task.status}`">
        <div class="row-1">
          <span class="name">{{ task.name }}</span>
          <span class="stage">{{ stageLabel(task) }}</span>
        </div>

        <div v-if="task.status === 'running'" class="bar">
          <div class="fill" :style="{ width: `${task.percent ?? 0}%` }" />
        </div>

        <div class="row-2 muted">
          <span>{{ task.message }}</span>
          <!-- 字节数只对下载阶段有意义:离线解密的进度单位是页,已在 message 里。 -->
          <span v-if="task.downloaded_bytes && task.stage !== 'decrypting'" class="bytes">
            {{ formatBytes(task.downloaded_bytes) }}
          </span>
        </div>

        <p v-if="task.error" class="error">{{ task.error }}</p>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.tasks {
  padding: 14px 16px;
}

header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 10px;
}

header h2 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
}

header span {
  font-size: 12.5px;
}

ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.task {
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--surface-sunken);
}

.task.is-failed {
  border-color: #f0c2c2;
  background: var(--danger-soft);
}

.task.is-success {
  border-color: #cbe6d1;
  background: var(--accent-soft);
}

.row-1 {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.name {
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stage {
  flex-shrink: 0;
  font-size: 12.5px;
  color: var(--text-muted);
}

.bar {
  margin: 8px 0 6px;
  height: 5px;
  border-radius: 3px;
  background: var(--border);
  overflow: hidden;
}

.fill {
  height: 100%;
  background: var(--accent);
  transition: width 0.3s ease;
}

.row-2 {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  font-size: 12.5px;
}

.bytes {
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}

.error {
  margin: 6px 0 0;
  font-size: 12.5px;
  color: var(--danger);
  word-break: break-word;
}
</style>
