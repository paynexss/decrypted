"""
UnfairPlay 后端:用从一台 Mac 回收的 enrollment 模型离线解密 IPA。

材料是**账号级**的:哪个 Apple ID 下载的包,就用哪个账号的模型解。
模型本身很小(实测 4 KB),但只能在**收它的那台 Mac** 上重算出来 ——
换机器或换 Apple ID 都要重新 collect。

上游:https://github.com/DrWhax/unfairplay(MIT)。

为什么把子进程包一层,而不是重新实现算法
----------------------------------------
上游那 30 万行 Go 里绝大部分是**从 fairplayd 里抽出来的数据表**
(`internal/recovery/compact_profile_*.go`,单文件 2~6.7 MB),加一个
`compact_kernel.go` 去跑它们 —— 本质是把 fairplayd 的混淆算法离线重实现。
重新实现一遍没有意义,直接调它的 CLI 最稳。

调用契约(实测)
--------------
* `decrypt --input X.ipa --model M --output Y.ipa --report R.json`
  成功时 stdout 是一段 JSON,键是 **PascalCase**(`DecryptedPages` 等)。
* `verify  --input X.ipa --model M`
  成功时 stdout 是 JSON,键是 **snake_case**(`decrypted_pages` 等)。
  它只在内存里验证、不落盘明文。
* 退出码非 0 时 stdout/stderr 里带原因,原样往上抛。

进度
----
`decrypt` 不输出增量进度(结果 JSON 在结束时才打印),所以这个后端
**没有按页进度**。好在它很快:实测 4720 页 / 0.45 秒(约 1 万页每秒),
一个 435 MB 的主二进制也就十几秒 —— 阶段文案足够,不需要进度条。
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import BACKEND_DIR, UNFAIRPLAY_BIN

logger = logging.getLogger(__name__)

# 解密/验证的硬超时。上游是纯计算,没有网络等待;给足余量即可。
# 一个 1 GB 的包按 1 万页/秒算约 25 秒,1200 秒对超大包也够。
DECRYPT_TIMEOUT_SECONDS = 1200
# collect 要遍历几个系统目录 + 复制 17 MB 的 fairplayd,给宽一点。
COLLECT_TIMEOUT_SECONDS = 600

# 内置的查找顺序。vendor/ 下的那份由 tools/build_unfairplay.sh 编译而来。
_VENDOR_BINARY = BACKEND_DIR / "vendor" / "unfairplay" / "unfairplay"


class UnfairplayError(RuntimeError):
    """UnfairPlay 不可用或执行失败。message 直接面向用户。"""


class ModelMismatch(UnfairplayError):
    """模型解不开这个包 —— 通常是「用错了账号的模型」。"""


@dataclass(frozen=True)
class DecryptResult:
    """一次离线解密的结果。"""

    pages: int
    images: int
    sha256: str

    def summary(self) -> str:
        return f"{self.images} 个二进制 / {self.pages} 页"


# --------------------------------------------------------------------------
# 可执行文件
# --------------------------------------------------------------------------
def binary() -> Path:
    """
    找到 unfairplay 可执行文件。

    顺序:UNFAIRPLAY_BIN → vendor/unfairplay/unfairplay → PATH。
    找不到时给出**具体**的编译命令,而不是一句「未安装」。
    """
    if UNFAIRPLAY_BIN:
        configured = Path(UNFAIRPLAY_BIN).expanduser()
        if not configured.is_file():
            raise UnfairplayError(
                f"UNFAIRPLAY_BIN 指向的文件不存在: {configured}"
            )
        return configured

    if _VENDOR_BINARY.is_file():
        return _VENDOR_BINARY

    found = shutil.which("unfairplay")
    if found:
        return Path(found)

    raise UnfairplayError(
        "找不到 unfairplay 可执行文件。它是一条不需要越狱设备的离线解密路径,\n"
        "编译方式(需要 Go):\n"
        "  bash tools/build_unfairplay.sh\n"
        "或者用 UNFAIRPLAY_BIN 指向已经编好的二进制。"
    )


def available() -> bool:
    """能不能用(不抛异常,给状态展示用)。"""
    try:
        binary()
    except UnfairplayError:
        return False

    return True


# --------------------------------------------------------------------------
# 模型
# --------------------------------------------------------------------------
def read_model(path: Path) -> bytes:
    """读模型文件并做基本校验。"""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise UnfairplayError(f"读不到模型文件 {path}: {exc}") from exc

    if len(raw) < 1024:
        raise UnfairplayError(
            f"模型文件 {path.name} 只有 {len(raw)} 字节,不像是 enrollment 模型。\n"
            "它应该是 `unfairplay recover` 产出的 fairplay-enrollment.bin。"
        )

    return raw


def model_sidecar(model: Path) -> dict:
    """
    读模型旁边的元数据(同名的 .json)。

    模型本身是二进制、不带可读元信息;装模型时顺手写一个 sidecar,
    界面上才能显示「这个模型属于哪个账号 / 什么时候收的」。
    读不到就返回空字典 —— 元数据缺失不影响解密。
    """
    sidecar = model.with_suffix(".json")
    if not sidecar.is_file():
        return {}

    try:
        payload = json.loads(sidecar.read_text())
    except (OSError, json.JSONDecodeError):
        return {}

    return payload if isinstance(payload, dict) else {}


# --------------------------------------------------------------------------
# 解密 / 验证
# --------------------------------------------------------------------------
def decrypt_ipa(
    source: Path,
    destination: Path,
    model: Path,
    *,
    on_stage=None,
) -> DecryptResult:
    """
    用模型把加密 IPA 解成明文 IPA。

    上游会在临时文件里写完再原子改名,失败时不会留下半个产物。
    on_stage(text) 用于把阶段文案透给任务列表。
    """
    executable = binary()
    read_model(model)

    if not source.is_file():
        raise UnfairplayError(f"输入 IPA 不存在: {source}")
    if destination.exists():
        raise UnfairplayError(f"输出已存在,先删掉: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    report = destination.with_suffix(".json")

    if on_stage:
        on_stage(f"正在离线解密({source.name},不需要设备)")

    logger.info("unfairplay decrypt: %s (model=%s)", source.name, model.name)
    result = _run(
        [
            str(executable),
            "decrypt",
            "--input",
            str(source),
            "--model",
            str(model),
            "--output",
            str(destination),
            "--report",
            str(report),
        ],
        timeout=DECRYPT_TIMEOUT_SECONDS,
        what="解密",
    )

    payload = _parse_json(result.stdout)
    if payload is None:
        raise UnfairplayError(
            "unfairplay decrypt 没有返回可解析的结果:\n" + _tail(result.stdout, result.stderr)
        )

    if not payload.get("OutputPath") or not destination.is_file():
        raise UnfairplayError(
            "unfairplay decrypt 报告成功但没有产出文件:\n"
            + _tail(result.stdout, result.stderr)
        )

    return DecryptResult(
        pages=int(payload.get("DecryptedPages") or 0),
        images=int(payload.get("EncryptedImages") or 0),
        sha256=str(payload.get("OutputSHA256") or ""),
    )


def verify_model(source: Path, model: Path) -> dict:
    """
    在内存里验证「这个模型能解开这个包」,不落盘明文。

    这是装完模型后最值得跑的一步:它能区分两类失败 ——
    「模型是别的账号的」和「模型没问题但包不受支持」。
    """
    executable = binary()
    read_model(model)

    if not source.is_file():
        raise UnfairplayError(f"输入 IPA 不存在: {source}")

    result = _run(
        [
            str(executable),
            "verify",
            "--input",
            str(source),
            "--model",
            str(model),
        ],
        timeout=DECRYPT_TIMEOUT_SECONDS,
        what="验证",
    )

    payload = _parse_json(result.stdout)
    if payload is None:
        raise UnfairplayError(
            "unfairplay verify 没有返回可解析的结果:\n" + _tail(result.stdout, result.stderr)
        )

    return payload


# --------------------------------------------------------------------------
# collect / recover(给管理 CLI 用)
# --------------------------------------------------------------------------
def collect(
    *,
    app: Path,
    ipa: Path,
    output: Path,
    use_sudo: bool = True,
    on_stage=None,
) -> dict:
    """
    抓取一次 Mac 的 enrollment 状态。

    需要 root:要读 `/private/var/db/fpsd/{SC Info,adi}`(权限 `drwxr-x--- _fpsd`)。
    **只读**,不写系统、不改 fairplayd。

    注意 `output` 的**父目录必须先存在** —— 上游用的是单层 `os.Mkdir`,
    不会递归创建。
    """
    executable = binary()

    if not app.is_dir():
        raise UnfairplayError(f"--app 不是目录: {app}")
    if not (app / "Info.plist").is_file() or not (app / "SC_Info").is_dir():
        raise UnfairplayError(
            f"--app 不像 iOS bundle(缺 Info.plist 或 SC_Info): {app}\n"
            "Mac 上装的 iOS 应用是包在 Wrapper 里的,要传内层那一层:\n"
            "  /Applications/<App>.app/Wrapper/<App>.app"
        )
    if not ipa.is_file():
        raise UnfairplayError(f"--ipa 不存在: {ipa}")
    if output.exists():
        raise UnfairplayError(f"输出目录已存在(unfairplay 要求必须是新目录): {output}")
    if not output.parent.is_dir():
        raise UnfairplayError(
            f"输出目录的父目录不存在: {output.parent}\n"
            "上游用的是单层 mkdir,不会递归创建 —— 先 mkdir -p 父目录。"
        )

    if on_stage:
        on_stage("正在抓取 Mac 的 FairPlay enrollment 状态(需要 root,只读)")

    command = [
        str(executable),
        "collect",
        "--app",
        str(app),
        "--ipa",
        str(ipa),
        "--output",
        str(output),
    ]
    if use_sudo:
        # -n: 没缓存凭据就直接失败,不要在后台任务里挂住等密码输入。
        command = ["sudo", "-n", *command]

    result = _run(command, timeout=COLLECT_TIMEOUT_SECONDS, what="collect", allow_failure=True)
    payload = _parse_json(result.stdout)

    if result.returncode != 0 or payload is None:
        message = _tail(result.stdout, result.stderr)
        if use_sudo and "sudo" in message.lower() and "password" in message.lower():
            message += "\n(sudo 需要密码:请在终端里手动跑一次,或先 sudo -v 缓存凭据)"
        raise UnfairplayError(f"collect 失败:\n{message}")

    return payload


def recover(*, capture: Path, output: Path, on_stage=None) -> dict:
    """从 capture 里离线重演 fairplayd 的密钥派生,产出 enrollment 模型。"""
    executable = binary()

    if not capture.is_dir():
        raise UnfairplayError(f"capture 目录不存在: {capture}")
    if output.exists():
        raise UnfairplayError(f"输出目录已存在: {output}")
    if not output.parent.is_dir():
        raise UnfairplayError(f"输出目录的父目录不存在: {output.parent}")

    if on_stage:
        on_stage("正在离线重演 fairplayd 的密钥派生(几秒钟)")

    result = _run(
        [
            str(executable),
            "recover",
            "--capture",
            str(capture),
            "--output",
            str(output),
        ],
        timeout=DECRYPT_TIMEOUT_SECONDS,
        what="recover",
        allow_failure=True,
    )

    payload = _parse_json(result.stdout)
    if result.returncode != 0 or payload is None:
        raise UnfairplayError(f"recover 失败:\n{_tail(result.stdout, result.stderr)}")

    return payload


def doctor(*, capture: Path) -> dict:
    """诊断一次 capture(9 项检查 + selector)。"""
    executable = binary()
    result = _run(
        [str(executable), "doctor", "--capture", str(capture)],
        timeout=COLLECT_TIMEOUT_SECONDS,
        what="doctor",
        allow_failure=True,
    )
    payload = _parse_json(result.stdout)
    if payload is None:
        raise UnfairplayError(f"doctor 失败:\n{_tail(result.stdout, result.stderr)}")

    return payload


# --------------------------------------------------------------------------
# 内部
# --------------------------------------------------------------------------
def _run(
    command: list[str],
    *,
    timeout: int,
    what: str,
    allow_failure: bool = False,
) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise UnfairplayError(
            f"执行 unfairplay {what} 失败: {exc}\n"
            f"(命令: {' '.join(command[:2])} …)"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise UnfairplayError(
            f"unfairplay {what} 超过 {timeout} 秒没结束,已中止。\n"
            "大包可以调大 config 里的超时;反复超时说明这个包不受支持。"
        ) from exc

    if result.returncode != 0 and not allow_failure:
        raise UnfairplayError(f"unfairplay {what} 失败:\n{_tail(result.stdout, result.stderr)}")

    return result


def _parse_json(text: str) -> dict | None:
    """从 stdout 里取那段 JSON。上游只打印一个对象,但前面可能有日志行。"""
    text = text.strip()
    if not text:
        return None

    start = text.find("{")
    if start < 0:
        return None

    try:
        payload = json.loads(text[start:])
    except json.JSONDecodeError:
        return None

    return payload if isinstance(payload, dict) else None


def _tail(stdout: str, stderr: str, limit: int = 800) -> str:
    """把子进程输出压成一段可直接展示的错误文本。"""
    for stream in (stderr, stdout):
        text = (stream or "").strip()
        if text:
            return text[-limit:]

    return "(无输出)"
