"""
应用配置。

设计原则:
- 所有路径在导入时解析为绝对路径,不受启动时工作目录影响;
- 全部可通过环境变量覆盖,方便换机器 / 换存储盘;
- 不在此处读取任何 Apple ID 凭据 —— ipatool 的登录由用户在终端里
  自行执行 `ipatool auth login` 完成,本服务只读取登录结果。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

BACKEND_DIR_FOR_ENV = Path(__file__).resolve().parent.parent

# 先加载 backend/.env,再读环境变量。
#
# 为什么在代码里加载而不是只靠 run.sh:直接 `uvicorn app.main:app` 或用
# IDE 调试时不会经过 run.sh,IPATOOL_BIN 就丢了 —— 后果是静默退化成单账号模式
# (系统 ipatool 不认 IPATOOL_KEYCHAIN_SERVICE,所有账号读到同一条钥匙串记录),
# 很难察觉。放在这里加载,任何启动方式都能拿到同一份配置。
#
# override=False:真实环境变量优先,方便临时覆盖。
try:
    from dotenv import load_dotenv

    load_dotenv(BACKEND_DIR_FOR_ENV / ".env", override=False)
except ImportError:  # dotenv 是可选的,没装也能跑(单账号模式不受影响)
    pass


def _env_path(name: str, default: Path) -> Path:
    """读取一个路径型环境变量;未设置时使用默认值。"""
    raw = os.getenv(name)
    return Path(raw).expanduser().resolve() if raw else default


# --------------------------------------------------------------------------
# 目录布局
# --------------------------------------------------------------------------
# backend/app/config.py -> backend/app -> backend -> 项目根
BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent

STORAGE_DIR = _env_path("IPA_STORAGE_DIR", BACKEND_DIR / "storage")
ENCRYPTED_DIR = STORAGE_DIR / "encrypted"  # 从 App Store 下载的加密 IPA
DECRYPTED_DIR = STORAGE_DIR / "decrypted"  # 手机砸壳后回传的解密 IPA(第二阶段使用)
STAGING_DIR = STORAGE_DIR / "staging"  # 下载中间态,进程重启后会被清理

DATABASE_PATH = _env_path("IPA_DATABASE_PATH", BACKEND_DIR / "data" / "library.db")

# 多账号表。每个账号在这里声明自己的钥匙串命名空间与 cookie 文件,
# 两者共同构成一个独立登录态(详见 accounts.py)。
ACCOUNTS_FILE = _env_path("IPA_ACCOUNTS_FILE", BACKEND_DIR / "accounts.json")


FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

# --------------------------------------------------------------------------
# ipatool
# --------------------------------------------------------------------------
def _env_binary(name: str, default: str) -> str:
    """
    读取一个可执行文件路径,相对路径按 backend/ 目录解析。

    .env 里写 `vendor/ipatool-patched/ipatool` 这种相对路径更易读,
    但如果不解析,它会相对于**运行时的工作目录**去找 —— 换一种启动方式
    (IDE 里跑、从项目根跑)就找不到文件了。
    """
    raw = os.getenv(name, default)
    candidate = Path(raw).expanduser()

    if candidate.is_absolute():
        return str(candidate)

    resolved = (BACKEND_DIR_FOR_ENV / candidate).resolve()
    # 只在解析后的路径真的存在时才采用 —— 否则保留原样,交给 PATH 去找。
    return str(resolved) if resolved.exists() else raw


IPATOOL_BIN = _env_binary("IPATOOL_BIN", "ipatool")

# 单个下载任务的最长耗时。大包(数 GB)在慢网络下可能超过 10 分钟。
IPATOOL_TIMEOUT_SECONDS = int(os.getenv("IPATOOL_TIMEOUT_SECONDS", "1800"))

# 轮询 .tmp 文件大小以计算进度的间隔。
PROGRESS_POLL_SECONDS = float(os.getenv("PROGRESS_POLL_SECONDS", "0.5"))

# 是否给 ipatool 传 --purchase(自动为未拥有的应用获取许可)。
#
# 默认关闭,这是实测结论而不是保守取值:
# --purchase 会触发一次**基于密码的重新登录**,而开启了双重认证的 Apple ID
# 在这种情况下会被 Apple 拒绝(实测返回 HTTP 204/301)。结果不是「顺便买下」,
# 而是把本来能下载的应用也一起弄失败。
#
# 关闭后的行为:
#   - 账号已拥有的应用  -> 正常下载;
#   - 未拥有的应用      -> 报 "license is required",提示用户先去 App Store 获取。
# 这比「所有请求都失败」诚实得多。
IPATOOL_AUTO_PURCHASE = os.getenv("IPATOOL_AUTO_PURCHASE", "").lower() in ("1", "true", "yes")

# --------------------------------------------------------------------------
# iTunes Search API
# --------------------------------------------------------------------------
ITUNES_TIMEOUT_SECONDS = float(os.getenv("ITUNES_TIMEOUT_SECONDS", "20"))

# 搜索结果条数。默认 8 —— 结果是个「挑一个」的列表,一屏能看完最好;
# 前端会显式传 limit,这里的默认值只在直接调 API(不传 limit)时生效。
SEARCH_LIMIT_DEFAULT = 5
# 硬上限。前端传再大也会被夹到这里,避免一次拉回一堆用不上的数据。
SEARCH_LIMIT_MAX = 25


# --------------------------------------------------------------------------
# 离线解密
# --------------------------------------------------------------------------
# 离线解密的材料是 **UnfairPlay 的 enrollment 模型**:从一台登录了
# Mac App Store 的 Mac 上回收的 `fairplay-enrollment.bin`(几 KB)。
# 不需要越狱、不需要手机,解出来的二进制是完整的。
#
# 模型是**账号级**的:哪个 Apple ID 下载的包,就得用哪个账号的模型解。
#
# 解密路径的选择:
#   auto        —— 有模型就离线解,没有就退回设备(默认)
#   offline     —— 只允许离线;没有模型就报错,用于确认离线真的在生效 ——
#                  走这条路不会去连设备
#   unfairplay  —— 同上(显式写法)
#   device      —— 强制设备砸壳(frida)。模型坏掉时的退路
_FAIRPLAY_MODES = ("auto", "offline", "unfairplay", "device")
FAIRPLAY_MODE = os.getenv("FAIRPLAY_MODE", "auto").strip().lower()
if FAIRPLAY_MODE not in _FAIRPLAY_MODES:
    logger.warning(
        "FAIRPLAY_MODE=%r 无法识别(可选 %s),按 auto 处理",
        FAIRPLAY_MODE,
        "/".join(_FAIRPLAY_MODES),
    )
    FAIRPLAY_MODE = "auto"

# unfairplay 可执行文件。留空则按下面的顺序找:
#   1. vendor/unfairplay/unfairplay(见 tools/build_unfairplay.sh)
#   2. PATH 里的 unfairplay
UNFAIRPLAY_BIN = os.getenv("UNFAIRPLAY_BIN", "").strip()

# enrollment 模型目录。默认每个账号一个文件 <账号 id>.bin,
# 也可以在账号表里用 unfairplay_model 显式指定别的路径。
MODELS_DIR = _env_path("IPA_MODELS_DIR", BACKEND_DIR / "data" / "models")


# --------------------------------------------------------------------------
# 砸壳设备
# --------------------------------------------------------------------------
# 设备 SSH。默认走 iproxy 从 USB 转发出来的本地端口,因此不需要知道设备 IP。
# 设备与电脑同网段时也可以直接写设备 IP(此时也不需要 iproxy)。
DEVICE_SSH_HOST = os.getenv("DEVICE_SSH_HOST", "127.0.0.1")
DEVICE_SSH_PORT = int(os.getenv("DEVICE_SSH_PORT", "2222"))

# 要转发哪一台设备。留空则「恰好一台已连线设备」时自动用它。
#
# 为什么需要这个:机器上可能同时挂着多台(实测本机除真机外还有几个
# idevice 虚拟设备),而 `iproxy` 不带 -u 时会自己挑第一台 —— 挑错就
# 会去连一个根本没跑 OpenSSH 的东西,报错也看不出所以然。
DEVICE_UDID = os.getenv("DEVICE_UDID", "").strip()

# 健康检查(SSH echo)的超时。砸壳本身的耗时是另一回事,由脚本内部控制。
DEVICE_TIMEOUT_SECONDS = float(os.getenv("DEVICE_TIMEOUT_SECONDS", "10"))

# idevice_id 探测超时。比 SSH 短得多 —— 它只是读一下 usbmuxd 的设备列表。
DEVICE_LIST_TIMEOUT_SECONDS = float(os.getenv("DEVICE_LIST_TIMEOUT_SECONDS", "5"))


def _find_frida_python() -> str | None:
    """
    找一个装了 frida 的 Python 解释器。

    砸壳脚本必须用它跑,不能用后端自己的 venv —— 后者的依赖里没有 frida,
    也没有理由为了一个可选功能给主服务加几十 MB 的二进制依赖。

    查找顺序:
      1. 环境变量 FRIDA_PYTHON 指定的路径(显式配置优先)
      2. 常见位置的 venv
      3. 系统 python3(如果它恰好装了 frida)
    """
    configured = os.getenv("FRIDA_PYTHON")
    if configured:
        path = Path(configured).expanduser()
        return str(path) if path.exists() else None

    candidates = [
        Path.home() / ".local" / "frida-venv" / "bin" / "python",
        PROJECT_ROOT / ".venv-frida" / "bin" / "python",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    # 系统解释器:只有在真的能 import frida 时才认
    system_python = shutil.which("python3")
    if system_python:
        probe = subprocess.run(
            [system_python, "-c", "import frida"],
            capture_output=True, check=False,
        )
        if probe.returncode == 0:
            return system_python

    return None


FRIDA_PYTHON = _find_frida_python()


def ensure_directories() -> None:
    """确保所有需要写入的目录存在。使用 0o755,因为这是本机个人工具。"""
    for directory in (
        STORAGE_DIR,
        ENCRYPTED_DIR,
        DECRYPTED_DIR,
        STAGING_DIR,
        DATABASE_PATH.parent,
        ACCOUNTS_FILE.parent,
        MODELS_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)
