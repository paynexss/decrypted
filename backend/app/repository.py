"""
SQLite 读写。

所有 SQL 集中在这里,业务代码只调用函数,不直接写语句,
这样表结构变化时只需改动本文件。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .db import connect
from .models import LibraryItem, Platform


def utc_now() -> str:
    """当前时间的 ISO-8601 UTC 字符串(秒级精度)。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------
# 查询
# --------------------------------------------------------------------------
def find_one(bundle_id: str, country: str, platform: Platform) -> sqlite3.Row | None:
    """按 (bundle_id, country, platform) 查一条记录;不存在返回 None。"""
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM apps WHERE bundle_id = ? AND country = ? AND platform = ?",
            (bundle_id, country, platform.value),
        ).fetchone()


def list_items() -> list[LibraryItem]:
    """文件库列表,按最近更新倒序。"""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM apps ORDER BY updated_at DESC").fetchall()

    return [_to_item(row) for row in rows]


# --------------------------------------------------------------------------
# 写入
# --------------------------------------------------------------------------
def upsert_encrypted(
    *,
    app_id: int,
    name: str,
    bundle_id: str,
    version: str | None,
    country: str,
    platform: Platform,
    icon_url: str | None,
    encrypted_path: str,
    encrypted_size: int,
) -> int:
    """
    写入 / 更新一条记录,返回记录 id。

    已存在同 (bundle_id, country, platform) 的记录时,只更新加密包相关字段,
    保留 decrypted_path / decrypted_size —— 用户之前砸好的解密包不能被覆盖掉。
    其他元数据(版本号、图标)以最新一次下载为准。
    """
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO apps (
                app_id, name, bundle_id, version, country, platform, icon_url,
                encrypted_path, encrypted_size, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (bundle_id, country, platform) DO UPDATE SET
                app_id         = excluded.app_id,
                name           = excluded.name,
                version        = excluded.version,
                icon_url       = excluded.icon_url,
                encrypted_path = excluded.encrypted_path,
                encrypted_size = excluded.encrypted_size,
                updated_at     = excluded.updated_at
            """,
            (
                app_id,
                name,
                bundle_id,
                version,
                country,
                platform.value,
                icon_url,
                encrypted_path,
                encrypted_size,
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT id FROM apps WHERE bundle_id = ? AND country = ? AND platform = ?",
            (bundle_id, country, platform.value),
        ).fetchone()

    if row is None:
        # 上面的 INSERT ... ON CONFLICT 必然产生一行,查不到说明数据库异常。
        raise RuntimeError(f"写入后未能读回记录: {bundle_id} / {country} / {platform.value}")

    return int(row["id"])


def upsert_decrypted(
    *,
    app_id: int,
    name: str,
    bundle_id: str,
    version: str | None,
    country: str,
    platform: Platform,
    icon_url: str | None,
    decrypted_path: str,
    decrypted_size: int,
) -> int:
    """
    写入 / 更新解密包字段,返回记录 id。

    解密流程不会新建记录 —— 它一定是在一个已有加密包的条目上进行的
    (下载前已经检查过)。

    但这里仍然用 INSERT ... ON CONFLICT:如果用户在砸壳过程中删掉了记录,
    静默失败会让产出成为孤儿文件。宁可重建一条记录。
    """
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO apps (
                app_id, name, bundle_id, version, country, platform, icon_url,
                decrypted_path, decrypted_size, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (bundle_id, country, platform) DO UPDATE SET
                decrypted_path = excluded.decrypted_path,
                decrypted_size = excluded.decrypted_size,
                updated_at     = excluded.updated_at
            """,
            (
                app_id, name, bundle_id, version, country, platform.value,
                icon_url, decrypted_path, decrypted_size, now, now,
            ),
        )
        row = conn.execute(
            "SELECT id FROM apps WHERE bundle_id = ? AND country = ? AND platform = ?",
            (bundle_id, country, platform.value),
        ).fetchone()

    if row is None:
        raise RuntimeError(f"写入后未能读回记录: {bundle_id} / {country} / {platform.value}")

    return int(row["id"])


def delete(item_id: int) -> None:
    """删除一条记录(文件由调用方负责删除)。"""
    with connect() as conn:
        conn.execute("DELETE FROM apps WHERE id = ?", (item_id,))


def clear_column(item_id: int, column: str) -> None:
    """把某个路径字段置空,用于记录文件已从磁盘消失的情况。"""
    if column not in ("encrypted_path", "decrypted_path"):
        raise ValueError(f"不允许清空的列: {column}")

    with connect() as conn:
        conn.execute(
            f"UPDATE apps SET {column} = NULL, updated_at = ? WHERE id = ?",  # noqa: S608 - 列名已白名单校验
            (utc_now(), item_id),
        )


# --------------------------------------------------------------------------
# 行 -> 模型
# --------------------------------------------------------------------------
def _to_item(row: sqlite3.Row) -> LibraryItem:
    encrypted_path = row["encrypted_path"]
    decrypted_path = row["decrypted_path"]

    return LibraryItem(
        id=int(row["id"]),
        app_id=row["app_id"],
        name=row["name"],
        bundle_id=row["bundle_id"],
        version=row["version"],
        country=row["country"],
        platform=Platform(row["platform"]),
        icon_url=row["icon_url"],
        encrypted_path=encrypted_path,
        decrypted_path=decrypted_path,
        encrypted_size=row["encrypted_size"],
        decrypted_size=row["decrypted_size"],
        has_encrypted=bool(encrypted_path),
        has_decrypted=bool(decrypted_path),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
