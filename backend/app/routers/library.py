"""
/api/library —— 本地文件库。

文件库的「真相来源」有三个:数据库记录、磁盘文件、以及两者的交集。
本模块的职责就是让这三者保持一致(见 library.py)。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import library
from ..models import LibraryResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["library"])


@router.get("/library", response_model=LibraryResponse)
async def get_library() -> LibraryResponse:
    """文件库列表 + 统计(数量、已解密数量、占用空间)。"""
    return library.list_library()


@router.get("/library/{item_id}/file")
async def download_file(item_id: int, type: str = "encrypted") -> FileResponse:
    """
    下载文件库里某个条目的文件。

    type 只接受 encrypted / decrypted,其余值一律拒绝 ——
    该参数直接决定读哪个目录,不能由客户端自由拼路径。
    """
    if type not in ("encrypted", "decrypted"):
        raise HTTPException(status_code=400, detail="type 只能是 encrypted 或 decrypted")

    resolved = library.resolve_file(item_id, kind=type)
    if resolved is None:
        raise HTTPException(status_code=404, detail="记录不存在,或该类型的文件尚未下载")

    path, filename = resolved
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=filename,
    )


@router.delete("/library/{item_id}")
async def delete_item(item_id: int) -> dict:
    """
    删除一条记录及其磁盘文件。

    加密包与解密包会被一并删除:文件库条目是按应用身份唯一的,
    留下没有记录的孤儿文件既占空间又无法在 UI 上管理。
    """
    deleted = library.delete_item(item_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail=f"记录不存在: {item_id}")

    return {"deleted": deleted}
