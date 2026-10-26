"""
FastAPI 入口。

启动方式(在 backend 目录下):
    uvicorn app.main:app --reload --port 8000

职责只有三件:初始化资源、挂载路由、托管前端静态文件。
任何业务逻辑都放在 app/ 下的其他模块里。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import accounts, storage, tasks
from .config import FRONTEND_DIST, PROJECT_ROOT
from .db import init_db
from .routers import download, library, search

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """
    应用生命周期。

    启动:建表、清理上次异常退出留下的下载临时文件、校验外部依赖。
    关闭:停掉下载线程池,避免进程退出时被挂起的下载拖住。
    """
    init_db()
    storage.cleanup_staging()
    _warn_about_environment()

    if accounts.load():
        # 区域是账号的稳定属性,启动时探测一次即可;之后走缓存,
        # 不会让每个请求都去调用 ipatool。
        refreshed = await asyncio.to_thread(accounts.refresh_regions)
        for account in refreshed:
            state = "已登录" if account.logged_in else "未登录"
            logger.info(
                "账号 %s(%s): %s, 区域 %s",
                account.id,
                account.label,
                state,
                account.region or "未探测",
            )

    logger.info("服务已启动。前端构建产物: %s", FRONTEND_DIST if FRONTEND_DIST.exists() else "(未构建)")
    yield

    tasks.registry.shutdown()


def _warn_about_environment() -> None:
    """
    启动时把「能用但会缺功能」的情况打进日志。

    这些都不是致命错误,所以只警告不退出 —— 例如用户可能只想先用搜索功能,
    还没打算登录 ipatool。真正的强制校验发生在用户点击解密时(decrypt.py)。
    """
    from . import ipatool

    if not ipatool.is_available():
        logger.warning("未找到 ipatool 可执行文件,下载功能不可用。安装方式见 README。")
        return

    try:
        account = ipatool.get_account()
    except ipatool.IpatoolError as exc:
        logger.warning("读取 ipatool 账号信息失败: %s", exc)
        return

    if account is None:
        logger.warning("ipatool 尚未登录,下载功能不可用。请在终端执行 `ipatool auth login`。")
    else:
        logger.info("ipatool 已登录: %s <%s>", account[0], account[1])


app = FastAPI(
    title="IPA 下载与砸壳管理系统",
    description="本地自建:App Store 搜索、加密 IPA 下载、本地文件库管理。手机端砸壳为第二阶段功能。",
    version="0.1.0",
    lifespan=lifespan,
)

# 开发模式下前端跑在 Vite 的 5173 端口,需要允许跨域。
# 生产模式由本服务直接托管构建产物,同源访问,不经过这里。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 所有业务接口统一挂在 /api 下,避免与前端路由、静态资源路径冲突。
app.include_router(search.router, prefix="/api")
app.include_router(download.router, prefix="/api")
app.include_router(library.router, prefix="/api")


@app.get("/api/health")
async def health() -> dict:
    """健康检查。前端启动时用它确认后端已就绪。"""
    return {"status": "ok"}


# --------------------------------------------------------------------------
# 前端静态文件
# --------------------------------------------------------------------------
# 前端未构建时(还没跑过 npm run build)不挂载静态目录,
# 访问根路径给出明确提示,而不是一个看不懂的 404。
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    async def serve_index() -> FileResponse:
        return FileResponse(FRONTEND_DIST / "index.html")
else:

    @app.get("/", include_in_schema=False)
    async def missing_frontend() -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "前端尚未构建。请执行: cd frontend && npm install && npm run build。"
                "开发模式可改用 `npm run dev`(默认 http://localhost:5173)。",
                "project_root": str(PROJECT_ROOT),
            },
        )
