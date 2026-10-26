"""
SQLite 访问层:建表 + 连接管理。具体读写语句见 repository.py。

为什么用标准库 sqlite3 而不是 ORM:
本阶段只有一张表、少量固定语句,引入 ORM 只会增加依赖和一层间接。
连接采用「每次操作新建 + 全局 WAL 模式」:既避免跨线程共享连接的隐患,
又让读写不互相阻塞。本地工具场景下建连开销可以忽略。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from .config import DATABASE_PATH, ensure_directories

# --------------------------------------------------------------------------
# 表结构
# --------------------------------------------------------------------------
# 相对原方案的两处补充(其余字段完全一致):
#   1. app_id —— App Store 数字 ID。部分被下架的应用无法用 bundle_id 查到,
#      必须用数字 ID 下载,因此需要持久化。
#   2. 唯一索引 (bundle_id, country, platform) —— 「文件库」语义是「一个应用一条记录」,
#      重复下载应更新原记录而不是堆积新行;同一应用在不同国家商店视为不同记录。
#
# created_at / updated_at 存储为 ISO-8601 UTC 字符串。SQLite 没有原生 datetime 类型,
# TEXT 存储 ISO-8601 是官方推荐做法,且可直接按字典序比较。
_SCHEMA = """
CREATE TABLE IF NOT EXISTS apps (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id         INTEGER,
    name           TEXT    NOT NULL,
    bundle_id      TEXT    NOT NULL,
    version        TEXT,
    country        TEXT    NOT NULL,
    platform       TEXT    NOT NULL,
    icon_url       TEXT,
    encrypted_path TEXT,
    decrypted_path TEXT,
    encrypted_size INTEGER,
    decrypted_size INTEGER,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_apps_identity
    ON apps (bundle_id, country, platform);
"""


def init_db() -> None:
    """建表并启用 WAL。应用启动时调用一次,幂等。"""
    ensure_directories()
    with connect() as conn:
        # WAL 是数据库级持久属性,只需设置一次,之后每个连接自动继承。
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_SCHEMA)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """
    打开一个数据库连接,自动提交 / 回滚,并在结束时关闭。

    异常时回滚后原样抛出 —— 数据库故障不能被静默吞掉。
    """
    conn = sqlite3.connect(DATABASE_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
