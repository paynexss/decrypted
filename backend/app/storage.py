"""
存储管理:文件命名、归档、删除,以及与应用身份无关的磁盘操作。

一条记录在磁盘上最多有两个文件:
  storage/encrypted/<bundle_id>_<version>_<country>_<platform>.ipa
  storage/decrypted/<bundle_id>_<version>_<country>_<platform>.ipa

命名里带国家/平台的原因:同一个 bundle_id 在不同国家商店可能是两个不同的
App Store 条目,记录本身也是按 (bundle_id, country, platform) 唯一区分的,
文件名必须与之一一对应,否则会互相覆盖。
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from .config import DECRYPTED_DIR, ENCRYPTED_DIR, STAGING_DIR
from .models import Platform

logger = logging.getLogger(__name__)

# bundle_id 允许字母数字和 . - _,不含路径分隔符;版本号可能带 + 等字符。
# 这个替换不是为了「安全」而是为了文件名在各平台都好用,
# 真正的路径穿越防护由下面 _require_within 完成。
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._+-]")


def build_filename(
    *,
    bundle_id: str,
    version: str | None,
    country: str,
    platform: Platform,
    suffix: str = "",
) -> str:
    """
    生成归档文件名。

    suffix 用于区分同一身份下的不同产物(例如第二阶段解密包加 `_decrypted`)。
    """
    version_part = _UNSAFE_FILENAME_CHARS.sub("_", version) if version else "unknown"
    bundle_part = _UNSAFE_FILENAME_CHARS.sub("_", bundle_id)

    return f"{bundle_part}_{version_part}_{country}_{platform.value}{suffix}.ipa"


def staging_path(task_id: str) -> Path:
    """
    下载中间文件的落盘位置。

    放在 staging 目录而不是直接写目标目录:下载失败会留下半个文件,
    放到专门目录里就不会污染文件库,启动时统一清理即可。
    """
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    return STAGING_DIR / f"{task_id}.ipa"


def encrypted_path(filename: str) -> Path:
    ENCRYPTED_DIR.mkdir(parents=True, exist_ok=True)
    return _require_within(ENCRYPTED_DIR, filename)


def decrypted_path(filename: str) -> Path:
    DECRYPTED_DIR.mkdir(parents=True, exist_ok=True)
    return _require_within(DECRYPTED_DIR, filename)


def archive(source: Path, destination: Path) -> int:
    """
    把下载完成的文件从 staging 移到目标位置,返回字节数。

    同一路径重复下载时直接覆盖 —— 记录是按身份唯一的,文件也必须如此,
    否则磁盘上会留下无法对应到任何记录的孤儿文件。
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    return destination.stat().st_size


def remove(path: Path) -> None:
    """删除文件。文件不存在视为成功(用户可能已在访达里手动删过)。"""
    path.unlink(missing_ok=True)


def cleanup_staging() -> None:
    """
    清空 staging 目录。进程启动时调用。

    里面的文件必然是上次异常退出留下的半成品:正常流程结束时
    download.py 会把文件移走或删除。
    """
    if not STAGING_DIR.exists():
        return

    removed = 0
    for entry in STAGING_DIR.iterdir():
        if entry.is_file():
            entry.unlink(missing_ok=True)
            removed += 1

    if removed:
        logger.info("启动清理: 删除 %d 个未完成的下载临时文件", removed)


def _require_within(directory: Path, filename: str) -> Path:
    """
    拼出路径并确认它确实落在目标目录内。

    文件名来自数据库和 App 元数据,理论上已被 _UNSAFE_FILENAME_CHARS 清洗过,
    但路径拼接是安全边界,这里做一次显式校验而不是依赖上游的清洗。
    """
    candidate = (directory / filename).resolve()
    root = directory.resolve()

    if candidate.parent != root:
        raise ValueError(f"文件名越出了存储目录: {filename!r}")

    return candidate
