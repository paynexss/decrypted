"""
ipatool 命令行封装。

三个关键事实(来自 ipatool 2.6.0 的实际行为,不是猜测):
1. 没有 `--storefront` / `--country` 之类的参数,商店由**登录账号所属的商店**决定。
   因此「下载哪个国家的包」不是本工具能选的,只能通过 `ipatool auth login`
   换账号来改变。UI 上的国家选择只影响**搜索与展示**(走公开 iTunes API,见 itunes.py),
   这一点在 README「已知限制」中有说明。
2. 全局 `--format json` 让所有输出变成一行行 zerolog JSON,可直接 json.loads。
3. 输出文件先写 `<目标>.tmp` 再改名,且默认文件名形如
   `<bundleId>_<appId>_<version>.ipa`,所以下载时必须显式传 `--output`。

登录(含 2FA)一律由用户在终端手动执行 `ipatool auth login` 完成。
本模块只读 `auth info`,绝不通过命令行参数传 Apple ID 密码 ——
进程参数对同机其他用户可见。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
from pathlib import Path

from .config import IPATOOL_BIN, IPATOOL_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

# ipatool 未登录时的报错特征(凭据项在 keyring 中不存在)。
# 这是「正常状态」而不是故障,需要与真正的错误区分开。
_NOT_LOGGED_IN_PATTERN = re.compile(
    r"could not be found in the keyring|specified item could not be found",
    re.IGNORECASE,
)


class IpatoolError(RuntimeError):
    """ipatool 调用失败。message 已整理为可直接展示给用户的文本。"""


# --------------------------------------------------------------------------
# 错误翻译
# --------------------------------------------------------------------------
# ipatool 的报错是面向开发者的短句(和 Apple 对话失败时的原始措辞),
# 有一大半没法让用户知道下一步该做什么。这里的映射不改变错误本身,
# 只补上「这意味着什么、你该做什么」。
#
# 之所以能精确匹配:这些错误都是 ipatool 源码里 errors.New 出来的固定字符串
# (pkg/appstore/appstore_lookup.go:14、appstore_download.go:20 等),
# 不是随 Apple 响应变化的文案。仍然用子串匹配,避免上游多加前缀就失配。
# 可以安全重试的错误特征。
#
# 为什么需要:ipatool **不支持断点续传** —— 一次中断整个下载就作废,而
# Apple 的 CDN 中途断流(报 `failed to write file: unexpected EOF`)是常态
# 而不是例外,大包尤其容易碰到。重试不是优化,是必需品。
#
# 只列传输层特征:许可缺失 / 账号不对 / 应用不存在这类,重试多少次都一样。
_TRANSIENT_DOWNLOAD_MARKERS: tuple[str, ...] = (
    "unexpected eof",
    "connection reset",
    "connection refused",
    "broken pipe",
    "i/o timeout",
    "timeout awaiting",
    "temporarily unavailable",
)

# 总共尝试次数与每次之间的等待(递增,给网络一点恢复时间)。
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_RETRY_DELAY_SECONDS = (3.0, 10.0)


def _is_transient(message: str) -> bool:
    """这条错误值不值得重试。"""
    lowered = message.lower()
    return any(marker in lowered for marker in _TRANSIENT_DOWNLOAD_MARKERS)


_ERROR_HINTS: dict[str, str] = {
    "app not found": (
        "App Store 在**当前登录账号所属的商店**里找不到这个应用。"
        "常见原因是应用只在其他地区上架 —— 例如抖音(com.ss.iphone.ugc.Aweme)只在中国大陆商店。"
        "换商店需要重新登录对应地区的 Apple ID:ipatool auth login -e <该地区账号> -p <密码>"
    ),
    "license is required": (
        "该 Apple ID 没有这个应用的许可(未在 App Store 获取过)。"
        "免费应用也要先「获取」一次才能下载 —— 请用同一账号在 iPhone 或 App Store 里"
        "点一次「获取」,然后回到这里重试。付费应用则需要先购买,"
        "本工具不会也不能替你完成付款。"
    ),
    "no usable authentication response": (
        "Apple 拒绝了这次请求。最常见的原因是账号开启了双重认证,"
        "而 ipatool 尝试用已保存的密码直接换会话时被拒。"
        "如果是在获取应用许可(IPATOOL_AUTO_PURCHASE=1)时发生,"
        "先关掉该选项;需要许可的应用请先在 App Store 里手动获取一次。"
        "其次检查网络出口(部分网络会被 Apple 判定为异常)。"
    ),
    "password token is expired": (
        "Apple ID 的登录会话已过期,且用已保存的密码自动重登失败。"
        "请在终端重新执行 ipatool auth login。"
    ),
    "invalid credentials": "Apple ID 或密码不正确。请在终端重新执行 ipatool auth login。",
    "device verification failed": (
        "Apple 要求对该账号做设备验证。在 iOS 设备上完成验证后重试,"
        "或改用一次性验证码重新登录。"
    ),
    "apple did not complete verification": (
        "2FA 验证没有完成。重新执行 ipatool auth login,并输入新的验证码。"
    ),
    "temporarily unavailable": "App Store 暂时不可用,稍后重试。",
    "sign in required": "Apple 要求重新登录。请在终端重新执行 ipatool auth login。",
    # 下面这两条是**传输层**失败,与账号/商店无关。顺序要紧:先具体后泛。
    # 实测误导过一次 —— `unexpected EOF` 被套上了「商店不一致」的兜底文案,
    # 结果把人往错误的方向引(他去查商店了,其实是连接断了)。
    "unexpected eof": (
        "下载到一半连接被中断 —— Apple 的 CDN 提前关掉了连接。"
        "**这不是商店或账号的问题**:ipatool 不支持断点续传,任何一次中断都会让整个下载作废。"
        "常见原因:网络不稳、走了代理/加速器、或 Apple 对当前出口限速。"
        "本工具会自动重试几次;仍然失败就换个网络环境,大包尤其容易碰到。"
    ),
    "failed to write file": (
        "写文件时出错。若同时出现 unexpected EOF,就是上面那条(下载被中断);"
        "否则检查目标磁盘是否写满、以及 storage/ 目录的权限。"
    ),
}


def explain(error: IpatoolError, *, country: str | None = None) -> IpatoolError:
    """
    给裸错误补一句「所以呢」。

    匹配不到时原样返回 —— 宁可给用户看 ipatool 的原文,
    也不要编一个可能不对的解释。
    """
    raw = str(error)
    lowered = raw.lower()

    for needle, hint in _ERROR_HINTS.items():
        if needle in lowered:
            # 传输层失败与「选了哪个商店」无关,别附上那句尾巴 ——
            # 前面刚说完「不是商店的问题」,后面又提商店,自相矛盾。
            suffix = ""
            if country and not _is_transient(raw):
                suffix = f"(你选择的是 {country} 商店的应用)"
            return IpatoolError(f"{hint}{suffix}\n\nipatool 原始报错:{raw}")

    # 匹配不到具体原因时**不要断言**是商店不一致 —— 那只是常见原因之一,
    # 而且这样说会把用户引到跟错误无关的方向去查(见上面 unexpected eof 的注释)。
    # 宁可把几种可能都列出来,让用户对着 ipatool 原文自己判断。
    if country:
        return IpatoolError(
            "下载失败。ipatool 没给出可识别的错误类型,常见原因有三类:\n"
            f"  · 网络中断(报错里带 EOF / reset / timeout 就是这类)\n"
            f"  · 该账号没有这个应用的许可(先在 App Store 里「获取」一次)\n"
            f"  · 应用不在所选 {country} 区商店,或账号所属商店与它不一致\n\n"
            f"ipatool 原始报错:{raw}"
        )

    return error


def _run(
    args: list[str],
    *,
    timeout: int,
    account_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """
    执行一次 ipatool 命令。

    account_env 非空时用于切换账号:补丁版 ipatool 会读
    IPATOOL_KEYCHAIN_SERVICE / IPATOOL_COOKIE_JAR,让每次调用落在
    指定账号的钥匙串命名空间与 cookie 文件上。为 None 时完全继承当前
    环境,行为与不打补丁时一致。
    """
    import os

    env = {**os.environ, **account_env} if account_env else None

    try:
        return subprocess.run(
            [IPATOOL_BIN, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except FileNotFoundError as exc:
        raise IpatoolError(
            f"找不到 ipatool 可执行文件({IPATOOL_BIN})。"
            "请执行 brew install ipatool,或用环境变量 IPATOOL_BIN 指定完整路径。"
        ) from exc


def _extract_error(stdout: str, stderr: str, fallback: str) -> str:
    """
    从 ipatool 的输出里取出最有信息量的一行作为错误信息。

    ipatool 的错误分两路:文本日志走 stderr;zerolog 结构化错误走 stdout。
    签名不匹配之类的错误还会附带整段 Apple 返回的 JSON,直接透传会让前端弹窗
    无法阅读,因此这里优先取 JSON 里的 error 字段并截断长度。
    """
    for stream in (stderr, stdout):
        for line in reversed(stream.splitlines()):
            line = line.strip()
            if not line:
                continue
            if line.startswith("{"):
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict) and payload.get("error"):
                    return str(payload["error"])[:800]
            else:
                return line[:800]

    return fallback


# --------------------------------------------------------------------------
# 账号
# --------------------------------------------------------------------------
def get_account(account_env: dict[str, str] | None = None) -> tuple[str, str] | None:
    """
    返回 (name, email);未登录返回 None。

    account_env 用于指定查询哪个账号(多账号模式);为 None 时查默认命名空间。
    非「未登录」类的失败会抛 IpatoolError —— 让用户看到真实原因,
    而不是被误导成「还没登录」。
    """
    result = _run(
        ["auth", "info", "--non-interactive", "--format", "json"],
        timeout=60,
        account_env=account_env,
    )

    if result.returncode == 0:
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and payload.get("success"):
                return str(payload.get("name", "")), str(payload.get("email", ""))

        raise IpatoolError("ipatool auth info 成功但输出无法解析,请升级 ipatool 后重试")

    if _NOT_LOGGED_IN_PATTERN.search(result.stderr) or _NOT_LOGGED_IN_PATTERN.search(result.stdout):
        return None

    raise IpatoolError(_extract_error(result.stdout, result.stderr, "读取 ipatool 账号信息失败"))


# --------------------------------------------------------------------------
# 下载
# --------------------------------------------------------------------------
def download(
    *,
    app_id: int,
    bundle_id: str,
    platform: str,
    output_path: Path,
    purchase: bool,
    on_progress=None,
    account_env: dict[str, str] | None = None,
) -> None:
    """
    下载加密 IPA 到 output_path,失败抛 IpatoolError。

    参数:
        app_id / bundle_id: 同时传入。ipatool 优先用 bundle_id 定位,
            定位不到时回退数字 ID —— 已下架的应用只能靠数字 ID 下载。
        purchase: 对应 `--purchase`,自动为免费应用获取许可。
            付费应用是否购买由用户决定,见 download.py 的说明。
        on_progress: 可选回调 (downloaded_bytes, total_bytes | None)。
            ipatool 不输出机器可读的进度,这里靠轮询目标文件大小估算,
            只用于给用户一个「还在动」的反馈,不追求精确。
        account_env: 多账号模式下指定用哪个账号下载(见 accounts.Account.environment)。
            None 表示用 ipatool 的默认命名空间。

    **会自动重试**:ipatool 不支持断点续传,一次中断整个下载就作废,
    而 CDN 中途断流(报 unexpected EOF)是常态而不是例外。只重试传输层
    错误 —— 许可/账号/应用不存在这类重试多少次都一样。
    """
    last_error: IpatoolError | None = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            _download_once(
                app_id=app_id,
                bundle_id=bundle_id,
                platform=platform,
                output_path=output_path,
                purchase=purchase,
                on_progress=on_progress,
                account_env=account_env,
            )
            return
        except IpatoolError as exc:
            last_error = exc
            if attempt >= DOWNLOAD_ATTEMPTS or not _is_transient(str(exc)):
                raise

            delay = DOWNLOAD_RETRY_DELAY_SECONDS[
                min(attempt - 1, len(DOWNLOAD_RETRY_DELAY_SECONDS) - 1)
            ]
            logger.warning(
                "ipatool 下载第 %d/%d 次失败(传输层,可重试): %s —— %.0f 秒后重试",
                attempt,
                DOWNLOAD_ATTEMPTS,
                str(exc).splitlines()[0][:120],
                delay,
            )

            # 清掉半成品再重来:否则 ipatool 可能直接往旧文件上续,
            # 而进度回调也会从上一轮的大小开始跳。
            for leftover in (output_path, Path(f"{output_path}.tmp")):
                leftover.unlink(missing_ok=True)
            if on_progress is not None:
                on_progress(0, None)

            time.sleep(delay)

    # 循环里已经 return 或 raise,走到这里说明逻辑有问题 —— 但保留兜底。
    raise last_error or IpatoolError("下载失败")


def _download_once(
    *,
    app_id: int,
    bundle_id: str,
    platform: str,
    output_path: Path,
    purchase: bool,
    on_progress=None,
    account_env: dict[str, str] | None = None,
) -> None:
    args = [
        "download",
        "--app-id",
        str(app_id),
        "--bundle-identifier",
        bundle_id,
        "--platform",
        platform,
        "--output",
        str(output_path),
        "--non-interactive",
        "--format",
        "json",
    ]
    if purchase:
        args.append("--purchase")

    logger.info(
        "ipatool 下载开始: %s -> %s%s",
        bundle_id,
        output_path,
        f"(账号 {account_env['IPATOOL_KEYCHAIN_SERVICE']})" if account_env else "",
    )
    started = time.monotonic()

    import os

    process = subprocess.Popen(
        [IPATOOL_BIN, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, **account_env} if account_env else None,
    )

    # ipatool 先写 <目标>.tmp 再改名,进度检测需要同时看这两个路径。
    candidates = (Path(f"{output_path}.tmp"), output_path)
    last_bytes = 0

    try:
        while process.poll() is None:
            if time.monotonic() - started > IPATOOL_TIMEOUT_SECONDS:
                process.kill()
                process.wait()
                raise IpatoolError(f"下载超时(超过 {IPATOOL_TIMEOUT_SECONDS} 秒),已终止 ipatool")

            size = 0
            for candidate in candidates:
                if candidate.exists():
                    size = max(size, candidate.stat().st_size)

            if size != last_bytes:
                last_bytes = size
                if on_progress is not None:
                    on_progress(size, None)

            time.sleep(0.5)

        stdout, stderr = process.communicate()

        if process.returncode != 0:
            raise IpatoolError(
                _extract_error(stdout, stderr, f"ipatool 下载失败(退出码 {process.returncode})")
            )

        if not output_path.exists():
            raise IpatoolError(
                _extract_error(stdout, stderr, f"ipatool 报告成功,但未找到输出文件: {output_path}")
            )

        size = output_path.stat().st_size
        if size == 0:
            raise IpatoolError(f"下载得到 0 字节文件: {output_path}")

        # 进度条结束后文件大小不再变化,补发一次最终进度,让前端跳到 100%。
        if on_progress is not None:
            on_progress(size, size)

        logger.info("ipatool 下载完成: %s (%.1f MB)", output_path.name, size / 1024 / 1024)

    finally:
        # 无论成功失败都清掉 ipatool 可能残留的 .tmp,避免污染存储目录。
        leftover = Path(f"{output_path}.tmp")
        if leftover.exists():
            leftover.unlink(missing_ok=True)


def is_available() -> bool:
    """ipatool 是否能在 PATH 中解析到。"""
    return shutil.which(IPATOOL_BIN) is not None
