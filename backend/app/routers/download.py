"""
/api/download/* —— 发起砸壳、查询任务、检查账号与设备状态。

任务模型:POST 立即返回 task_id(HTTP 202),前端轮询 GET /api/tasks/{id}。
砸壳可能要几分钟,不能挂在一次 HTTP 请求上。

只有一条下载类接口:砸壳。界面上的「下载解密」是唯一入口 ——
设备上没装应用时,后端会在砸壳流程内部先取回安装包,用户不需要关心。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, status

from .. import accounts, decrypt, device, ipatool, tasks
from ..models import (
    AccountInfo,
    AccountsOverview,
    DeviceStatus,
    EncryptedDownloadRequest,
    TaskInfo,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["download"])


@router.post(
    "/download/decrypted",
    response_model=TaskInfo,
    status_code=status.HTTP_202_ACCEPTED,
)
async def download_decrypted(request: EncryptedDownloadRequest) -> TaskInfo:
    """
    发起砸壳。

    前置条件(设备在线、能找到应用)在这里同步校验,不满足直接 400 ——
    让用户立刻知道问题在哪,而不是等一个必然失败的后台任务。
    """
    try:
        task = await decrypt.start_decrypted_download(request)
    except decrypt.DecryptRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return task.snapshot()


@router.get("/tasks/{task_id}", response_model=TaskInfo)
async def get_task(task_id: str) -> TaskInfo:
    task = tasks.registry.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在或已被清理: {task_id}")

    return task.snapshot()


@router.get("/tasks", response_model=list[TaskInfo])
async def list_tasks(limit: int = 50) -> list[TaskInfo]:
    """最近的任务列表,用于前端「下载中」区域的初始化。"""
    return [task.snapshot() for task in tasks.registry.list_tasks(limit=limit)]


@router.get("/account", response_model=AccountsOverview)
async def get_account(refresh: bool = False) -> AccountsOverview:
    """
    账号登录状态(只读)。

    前端在下载前用它提示「请先登录」,并显示每个账号属于哪个区域 ——
    顾客看到「美国区 / 中国区」比看到「已登录」有用得多。

    同时给出 offline_regions:已备好离线模型的区域。前端据此允许在
    设备不在线时也点「下载解密」—— 后端会走离线解密,不需要设备。

    登录动作本身不在这里做:`ipatool auth login` 需要交互式输入 2FA 码,
    且密码不应经过本服务的进程参数(同机其他用户可见 `ps` 输出)。

    refresh=true 会重新探测所有账号(会真的调用 ipatool,稍慢),
    默认走缓存。
    """
    configured = accounts.load()

    if not configured:
        # 单账号模式:沿用补丁前的行为,用 ipatool 的默认命名空间。
        try:
            login = ipatool.get_account()
        except ipatool.IpatoolError as exc:
            return AccountsOverview(
                multi_account=False,
                accounts=[AccountInfo(logged_in=False, error=str(exc))],
                regions=[],
            )

        if login is None:
            return AccountsOverview(
                multi_account=False,
                accounts=[AccountInfo(logged_in=False)],
                regions=[],
            )

        name, email = login
        return AccountsOverview(
            multi_account=False,
            accounts=[AccountInfo(logged_in=True, name=name, email=email)],
            regions=[],
        )

    # 多账号模式:每个账号并行探测,单个失败不影响其余。
    if refresh:
        await asyncio.to_thread(accounts.refresh_regions)
    else:
        await asyncio.to_thread(_probe_missing)

    snapshot = [
        AccountInfo(
            id=account.id,
            label=account.label,
            logged_in=account.logged_in,
            name=account.name,
            email=account.email,
            region=account.region,
            error=account.error,
            offline_ready=accounts.model_path(account) is not None,
        )
        for account in accounts.load()
    ]

    regions = sorted({item.region for item in snapshot if item.logged_in and item.region})

    return AccountsOverview(
        multi_account=True,
        accounts=snapshot,
        regions=regions,
        offline_regions=accounts.offline_regions(),
    )


def _probe_missing() -> None:
    """探测那些还没成功拿到登录状态的账号。已探测成功的不重复调用 ipatool。"""
    for account in accounts.load():
        if account.error is None and account.logged_in:
            continue
        accounts.probe(account)


@router.get("/device/status", response_model=DeviceStatus)
async def device_status() -> DeviceStatus:
    """
    砸壳设备的状态。

    会真的去连一次 SSH —— 这个接口只在用户点「刷新状态」和页面加载时调用,
    不在轮询路径上,可以接受。
    """
    return await asyncio.to_thread(device.get_status)
