"""
文件库业务逻辑:列表、删除、文件定位。

这一层要处理的问题是「数据库记录」与「磁盘文件」可能不一致:

- 记录存在但文件被手动删掉(用户在访达里删了 storage 目录里的文件);
- 文件存在但没有记录(理论上不该发生,异常退出可能留下,由启动时的
  staging 清理与 storage 目录的命名规则兜住)。

对第一种情况:接口如实报告 `has_encrypted = False`,**不自动改数据库** ——
自动删记录会在「文件只是临时被移走」时造成误删。用户看到后可以自行删除条目。

路径安全:数据库里存的是**文件名**而不是完整路径,目录由本模块决定。
`storage.encrypted_path()` / `decrypted_path()` 会再次校验拼接结果确实落在
对应目录内,因此即使数据库被手动改过也读不到存储目录之外的文件。
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import repository, storage
from .models import LibraryItem, LibraryResponse

logger = logging.getLogger(__name__)


def list_library() -> LibraryResponse:
    items = repository.list_items()

    encrypted_count = sum(1 for item in items if item.has_encrypted)
    decrypted_count = sum(1 for item in items if item.has_decrypted)
    total_size = sum((item.encrypted_size or 0) + (item.decrypted_size or 0) for item in items)

    return LibraryResponse(
        count=len(items),
        encrypted_count=encrypted_count,
        decrypted_count=decrypted_count,
        total_size=total_size,
        items=items,
    )


def resolve_file(item_id: int, *, kind: str) -> tuple[Path, str] | None:
    """
    定位条目对应的磁盘文件,返回 (绝对路径, 下载时展示的文件名)。

    记录不存在、记录里没有该类型的路径、或文件已从磁盘消失时返回 None,
    由路由层统一转成 404。
    """
    item = _get_item(item_id)
    if item is None:
        return None

    path = _disk_path(item, kind)
    if path is None:
        return None

    if not path.exists():
        logger.warning("记录 %s 的 %s 文件在磁盘上不存在: %s", item_id, kind, path)
        return None

    return path, path.name


def delete_item(item_id: int) -> dict | None:
    """
    删除记录及其磁盘文件,返回被删除的文件名列表;记录不存在时返回 None。

    加密包与解密包一并删除:文件库条目按应用身份唯一,留下没有记录的
    孤儿文件既占空间又无法在界面上管理。
    """
    item = _get_item(item_id)
    if item is None:
        return None

    deleted_files: list[str] = []

    for kind in ("encrypted", "decrypted"):
        path = _disk_path(item, kind)
        if path is None:
            continue

        storage.remove(path)
        deleted_files.append(path.name)

    repository.delete(item_id)
    logger.info("已删除文件库条目 %s(文件: %s)", item_id, deleted_files or "无")

    return {"id": item_id, "files": deleted_files}


# --------------------------------------------------------------------------
# 内部
# --------------------------------------------------------------------------
def _get_item(item_id: int) -> LibraryItem | None:
    """
    按 id 取条目。

    数据量是个人级文件库(几十到几百条),直接遍历列表即可,
    不值得为这一次查询再开一条 SQL 语句和对应的仓储函数。
    """
    for item in repository.list_items():
        if item.id == item_id:
            return item
    return None


def _disk_path(item: LibraryItem, kind: str) -> Path | None:
    """
    取条目在磁盘上的路径。记录里没有该类型的文件名时返回 None。

    归一化用字典而不是 if/else:两种类型只有取哪个字段、进哪个目录的区别,
    写成映射后新增类型(例如将来加个 `_patched`)时改动最小。
    """
    filename = item.encrypted_path if kind == "encrypted" else item.decrypted_path
    if not filename:
        return None

    if kind == "encrypted":
        return storage.encrypted_path(filename)

    return storage.decrypted_path(filename)
