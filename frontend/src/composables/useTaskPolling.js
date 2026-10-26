/**
 * 下载任务轮询。
 *
 * 任务列表是「后端拥有的状态」:组件卸载后任务仍在后台跑,
 * 因此这里不做清理,只在组件挂载时同步一次,之后按 1.5 秒轮询。
 *
 * 轮询只在「存在未结束任务」时进行 —— 空闲时不该有定时器一直跑。
 */

import { computed, onUnmounted, ref } from 'vue'
import { fetchTask, fetchTasks } from '../api'

const POLL_INTERVAL_MS = 1500

const ACTIVE_STATUSES = ['queued', 'running']

export function useTaskPolling(onFinished) {
  const tasks = ref([])
  let timer = null

  const activeTasks = computed(() => tasks.value.filter((task) => ACTIVE_STATUSES.includes(task.status)))

  function upsert(task) {
    const index = tasks.value.findIndex((item) => item.task_id === task.task_id)
    if (index === -1) {
      tasks.value = [task, ...tasks.value]
    } else {
      const next = [...tasks.value]
      next[index] = task
      tasks.value = next
    }
  }

  async function tick() {
    for (const task of activeTasks.value) {
      try {
        const fresh = await fetchTask(task.task_id)
        const wasActive = ACTIVE_STATUSES.includes(task.status)
        const isActive = ACTIVE_STATUSES.includes(fresh.status)
        upsert(fresh)

        // 刚结束的任务才通知外部:重复轮询到同一个已完成任务不该反复触发刷新。
        if (wasActive && !isActive && onFinished) {
          onFinished(fresh)
        }
      } catch {
        // 任务被后端清理掉(404)时,把本地这条状态标为失败即可,
        // 不中断其他任务的轮询。
        upsert({ ...task, status: 'failed', message: '任务状态已失效', error: '后端已清理该任务记录' })
      }
    }

    schedule()
  }

  function schedule() {
    clearTimeout(timer)
    if (activeTasks.value.length > 0) {
      timer = setTimeout(tick, POLL_INTERVAL_MS)
    }
  }

  /** 外部创建任务后调用,把新任务并入列表并启动轮询。 */
  function track(task) {
    upsert(task)
    schedule()
  }

  async function sync() {
    const list = await fetchTasks()
    tasks.value = list
    schedule()
  }

  onUnmounted(() => clearTimeout(timer))

  return { tasks, activeTasks, track, sync }
}
