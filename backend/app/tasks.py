"""
下载任务管理。

为什么需要任务表:
一次 IPA 下载可能持续几分钟到几十分钟,HTTP 请求不可能一直挂着。
因此下载放到后台线程执行,前端拿 task_id 轮询进度。

并发上限设为 2:单个包可能有数 GB,同时跑太多会互相抢带宽和磁盘;
同时也降低同一 Apple ID 被判定为异常并发的风险。
为什么**串行**而不是并行:
ipatool 把登录 cookie 持久化在状态目录里的单个文件(`~/.ipatool/cookies`),
每个请求都会读写它。两个 ipatool 进程同时跑会争抢这个文件,可能把会话写坏,
表现为随机的「未登录」错误。这个风险远大于并行下载带来的收益,因此
worker 数固定为 1:排队等待,但不互相破坏。
"""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .models import TaskInfo, TaskStage, TaskStatus

logger = logging.getLogger(__name__)

# 见模块开头:固定的 1,不是可调参数。
MAX_CONCURRENT_DOWNLOADS = 1

# 已完成任务的保留上限。本阶段不做持久化:进程重启后任务列表清空,
# 但文件库(SQLite + 磁盘)不受影响,已完成的结果不会丢。
MAX_RETAINED_TASKS = 200


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Task:
    """
    单个下载任务的可变状态。

    所有写操作都加锁,因为进度回调来自下载线程,而读取来自 HTTP 请求线程。
    """

    def __init__(
        self,
        *,
        kind: str,
        app_id: int,
        bundle_id: str,
        name: str,
        total_bytes: int | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self.task_id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.app_id = app_id
        self.bundle_id = bundle_id
        self.name = name
        self.status = TaskStatus.QUEUED
        self.stage = TaskStage.QUEUED
        self.message = "已加入队列,等待开始"
        # total_bytes 来自 iTunes 报告的包体积,只是估算值:
        # App Store 实际下发的包可能因设备/版本差异略有出入,因此进度上限
        # 在完成前压到 99%,成功后才置为 100%。
        self.total_bytes = total_bytes or None
        self.downloaded_bytes = 0
        # 阶段自带进度(目前只有离线解密用:页数)。None 表示这一阶段没有
        # 自己的进度,快照里退回按字节数估算。
        self.stage_percent: float | None = None
        self.library_id: int | None = None
        self.error: str | None = None
        self.created_at = _now()
        self.updated_at = self.created_at

    # -- 状态变更(下载线程调用) ------------------------------------------
    def started(self, stage: TaskStage, message: str) -> None:
        with self._lock:
            self.status = TaskStatus.RUNNING
            self.stage = stage
            # 换阶段就把上一阶段的进度清掉,否则进度条会停在旧阶段的百分比上。
            self.stage_percent = None
            self.message = message
            self.updated_at = _now()

    def stage_progress(self, percent: float | None, message: str) -> None:
        """
        更新当前阶段自己的进度与文案。

        与 progress() 分开:后者跟下载字节绑定(前端会把字节数列出来),
        而离线解密的进度单位是页,两回事。
        """
        with self._lock:
            self.stage_percent = percent
            self.message = message
            self.updated_at = _now()

    def note(self, message: str) -> None:
        """更新提示文案但不改变阶段。"""
        with self._lock:
            self.message = message
            self.updated_at = _now()

    def progress(self, downloaded_bytes: int, total_bytes: int | None = None) -> None:
        with self._lock:
            self.downloaded_bytes = downloaded_bytes
            if total_bytes is not None:
                self.total_bytes = total_bytes or None
            self.updated_at = _now()

    def succeed(self, *, library_id: int, message: str) -> None:
        with self._lock:
            self.status = TaskStatus.SUCCESS
            self.stage = TaskStage.FINISHED
            self.message = message
            self.library_id = library_id
            self.error = None
            if self.total_bytes:
                self.downloaded_bytes = self.total_bytes
            self.updated_at = _now()

    def fail(self, error: str) -> None:
        with self._lock:
            self.status = TaskStatus.FAILED
            self.message = "任务失败"
            self.error = error
            self.updated_at = _now()

    # -- 读取(HTTP 线程调用) ----------------------------------------------
    def snapshot(self) -> TaskInfo:
        with self._lock:
            percent: float | None = None
            if self.status is TaskStatus.SUCCESS:
                percent = 100.0
            elif self.stage_percent is not None:
                # 阶段进度同样封顶 99%:没成功就不宣布 100%。
                percent = min(max(self.stage_percent, 0.0), 99.0)
            elif self.total_bytes and self.downloaded_bytes:
                # 估算体积可能小于实际包,进度封顶 99%,避免出现 100% 却还在下载。
                percent = min(self.downloaded_bytes / self.total_bytes * 100.0, 99.0)

            return TaskInfo(
                task_id=self.task_id,
                kind=self.kind,
                status=self.status,
                stage=self.stage,
                message=self.message,
                app_id=self.app_id,
                bundle_id=self.bundle_id,
                name=self.name,
                percent=round(percent, 1) if percent is not None else None,
                downloaded_bytes=self.downloaded_bytes,
                total_bytes=self.total_bytes,
                library_id=self.library_id,
                error=self.error,
                created_at=self.created_at,
                updated_at=self.updated_at,
            )


class TaskRegistry:
    """任务容器 + 线程池。整个进程共用一个实例。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, Task] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=MAX_CONCURRENT_DOWNLOADS,
            thread_name_prefix="ipa-download",
        )

    def create(self, **kwargs) -> Task:
        task = Task(**kwargs)
        with self._lock:
            self._tasks[task.task_id] = task
            self._evict_finished_locked()
        return task

    def submit(self, task: Task, work) -> None:
        """
        把 `work(task)` 丢进线程池。

        work 抛出的任何异常都会转成任务失败状态 —— 后台线程里未捕获的异常
        只会打到日志里,前端将永远看不到结果。
        """
        self._executor.submit(self._guarded, task, work)

    def _guarded(self, task: Task, work) -> None:
        try:
            work(task)
        except Exception as exc:  # noqa: BLE001 - 后台线程的兜底边界
            logger.exception("任务 %s 执行失败", task.task_id)
            task.fail(str(exc) or exc.__class__.__name__)

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self, limit: int = 50) -> list[Task]:
        with self._lock:
            tasks = list(self._tasks.values())
        return tasks[-limit:]

    def _evict_finished_locked(self) -> None:
        """
        超出保留上限时,从最旧的**已结束**任务开始丢弃。

        只丢已结束的:正在跑的任务被丢掉会让前端轮询到 404。
        """
        if len(self._tasks) <= MAX_RETAINED_TASKS:
            return

        finished = [
            task
            for task in self._tasks.values()
            if task.status in (TaskStatus.SUCCESS, TaskStatus.FAILED)
        ]
        finished.sort(key=lambda task: task.created_at)

        for task in finished[: len(self._tasks) - MAX_RETAINED_TASKS]:
            self._tasks.pop(task.task_id, None)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


# 进程级单例。
registry = TaskRegistry()
