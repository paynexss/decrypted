"""
与越狱手机构通信:状态检测 + 砸壳。

实现方式与原方案不同 —— 原方案要在手机上跑一个 HTTP 服务(device/server.py),
实际做下来发现没必要:

* 手机上已经有 root SSH,`scp -r` 就能把整个 .app 拉下来,连目录结构、
  权限位、符号链接一起保留,比写个 HTTP 服务传文件可靠得多;
* 唯一必须用 frida 的是「读进程内存里解密后的字节」,而 frida 是电脑直连
  USB 的,不需要手机上有服务。

所以现在的模型是「电脑主动连手机」,手机端零部署。

依赖的外部工具(电脑侧):
    iproxy           # SSH over USB 的端口转发(brew install libimobiledevice)
    ssh / scp        # 系统自带
    frida            # pip install 'frida==17.23.3'(版本需与手机 frida-server 一致)

砸壳本身实现在 decrypt/ios_dump.py,本模块只负责:
  - 判断设备是否可达(供界面显示状态)
  - 在后台线程里调用砸壳脚本,把中间进度翻译成任务状态
"""

from __future__ import annotations

import json
import logging
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from . import device_check
from .config import (
    DEVICE_LIST_TIMEOUT_SECONDS,
    DEVICE_SSH_HOST,
    DEVICE_SSH_PORT,
    DEVICE_TIMEOUT_SECONDS,
    DEVICE_UDID,
    FRIDA_PYTHON,
    PROJECT_ROOT,
)
from .models import DeviceStatus, DumpResult

logger = logging.getLogger(__name__)

# 砸壳脚本依赖 frida,必须用装了 frida 的解释器跑,不能用后端的 venv。
DUMP_SCRIPT = PROJECT_ROOT / "decrypt" / "ios_dump.py"


class DeviceError(RuntimeError):
    """设备调用失败。message 可直接展示。"""


def get_status() -> DeviceStatus:
    """
    设备是否可用:SSH 通不通、frida 脚本在不在。

    这是同步函数 —— 它只在启动时与用户点「刷新状态」时调用,
    不做轮询(轮询 SSH 会不断起进程,不值得)。
    """
    if not DUMP_SCRIPT.exists():
        return DeviceStatus(
            configured=False,
            online=False,
            detail=f"找不到砸壳脚本 {DUMP_SCRIPT}",
        )

    if FRIDA_PYTHON is None:
        return DeviceStatus(
            configured=False,
            online=False,
            detail=(
                "未找到带 frida 的 Python 解释器。"
                "运行 python3 -m venv ~/.local/frida-venv && "
                "~/.local/frida-venv/bin/pip install 'frida==17.23.3'"
            ),
        )

    reachable, detail = _check_ssh()
    if not reachable:
        return DeviceStatus(
            configured=True,
            online=False,
            base_url=f"{DEVICE_SSH_HOST}:{DEVICE_SSH_PORT}",
            detail=detail,
        )

    return DeviceStatus(
        configured=True,
        online=True,
        base_url=f"{DEVICE_SSH_HOST}:{DEVICE_SSH_PORT}",
        detail=detail,
    )


def connected_devices() -> list[str] | None:
    """
    当前 USB 上已连线的设备 UDID 列表。

    `idevice_id -l` 只读 usbmuxd 的设备列表,不需要设备跑 SSH。
    返回 None 表示**无法判断**(没装 idevice_id / 命令超时)—— 与「一台也没有」
    (返回空列表)是两回事,调用方要分开处理。
    """
    binary = shutil.which("idevice_id")
    if binary is None:
        return None

    try:
        result = subprocess.run(
            [binary, "-l"],
            capture_output=True,
            text=True,
            check=False,
            timeout=DEVICE_LIST_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def ensure_forward() -> None:
    """
    确保 iproxy 把本地端口转发到设备的 22。

    只在端口没通时才起新进程 —— 重复起会因端口占用而失败,
    而那个失败信息容易让人误以为是设备问题。

    两个关键点(都是踩出来的):

    * **先确认真的有设备**。`iproxy` 在没有设备时也会成功启动并一直等 ——
      只看「端口通了」会得出「设备在线」的错误结论(界面上真的会这么显示)。
    * **显式指定 UDID**。不带 `-u` 时 iproxy 自己挑第一台;机器上同时挂着
      虚拟设备时会挑错,而报错完全看不出是挑错了。
    """
    if device_check.port_open(DEVICE_SSH_HOST, DEVICE_SSH_PORT):
        return

    if shutil.which("iproxy") is None:
        raise DeviceError(
            "未找到 iproxy。安装:brew install libimobiledevice"
        )

    udid = DEVICE_UDID
    devices = connected_devices()
    if devices is not None:
        if not devices:
            raise DeviceError(
                "没有检测到已连线的设备(idevice_id -l 为空)。\n"
                "检查:数据线插好、设备已解锁、并在「信任此电脑」弹窗里点过信任。"
            )
        if not udid:
            if len(devices) > 1:
                raise DeviceError(
                    f"检测到 {len(devices)} 台设备,无法确定用哪一台:\n"
                    + "\n".join(f"  {item}" for item in devices)
                    + "\n请在 backend/.env 里设置 DEVICE_UDID。"
                )
            udid = devices[0]
        elif udid not in devices:
            raise DeviceError(
                f"DEVICE_UDID={udid} 不在当前已连线设备里:\n"
                + "\n".join(f"  {item}" for item in devices)
            )

    command = ["iproxy", str(DEVICE_SSH_PORT), "22"]
    if udid:
        command += ["-u", udid]
    logger.info("启动 %s", " ".join(command))
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 给 iproxy 一点时间建立转发
    for _ in range(20):
        time.sleep(0.25)
        if device_check.port_open(DEVICE_SSH_HOST, DEVICE_SSH_PORT):
            return

    raise DeviceError(
        f"iproxy 启动后 {DEVICE_SSH_HOST}:{DEVICE_SSH_PORT} 仍不可用。"
        "设备是否已连线并在「信任此电脑」弹窗里确认?"
    )


def check_ssh() -> tuple[bool, str]:
    """对外暴露的一次性 SSH 检查(供路由层使用)。"""
    return _check_ssh()


def is_installed(bundle_id: str) -> bool:
    """
    设备上是否已安装某个应用。

    用 frida 的 enumerate_applications 而不是 `appinst --list`:
    后者在 mcp-appinst 这个变体上不支持(实测 --list 直接打印用法)。
    frida 本来就装着,且返回的就是 Bundle ID,不用解析文本。
    """
    if FRIDA_PYTHON is None:
        raise DeviceError("未找到带 frida 的 Python 解释器")

    probe = subprocess.run(
        [
            str(FRIDA_PYTHON), "-c",
            "import frida,sys;"
            "dev=frida.get_usb_device(timeout=10);"
            "ids={a.identifier for a in dev.enumerate_applications()};"
            "sys.exit(0 if sys.argv[1] in ids else 1)",
            bundle_id,
        ],
        capture_output=True, text=True, check=False, timeout=60,
    )

    if probe.returncode == 0:
        return True

    if probe.returncode == 1:
        return False

    # 其他退出码说明 frida 本身出问题了,不能当成「没安装」
    detail = (probe.stderr or probe.stdout).strip().splitlines()
    raise DeviceError(
        f"无法查询设备上的应用列表: {detail[0][:150] if detail else '未知错误'}"
    )


def installed_version(bundle_id: str) -> str | None:
    """
    读设备上**已安装**应用的版本号(CFBundleShortVersionString)。

    这个值决定了砸壳产出的内容对应哪个版本 —— 与 App Store 商店里的
    最新版可能不同(用户没升级)。所以文件名与「已砸过」判断都用它。

    读不到时返回 None(应用没装、Info.plist 异常等)。调用方把 None 当成
    「不知道版本」,走完整流程 —— 猜一个版本号去跳过砸壳是不可接受的。
    """
    ensure_forward()

    # 用 appinst 的索引查路径不可靠(不同变体参数不同),直接遍历容器目录。
    # 83 个应用遍历一两秒,而这一步只在砸壳前做一次。
    script = (
        'for app in /var/containers/Bundle/Application/*/*.app; do '
        '  [ -d "$app" ] || continue; '
        '  if grep -A1 "<key>CFBundleIdentifier</key>" "$app/Info.plist" 2>/dev/null '
        f'     | grep -q "{bundle_id}"; then '
        '    grep -A1 "<key>CFBundleShortVersionString</key>" "$app/Info.plist" '
        # raw string:BSD sed 能正确处理 \/ ,但普通字符串会让 Python 报
        # SyntaxWarning(invalid escape sequence)。
        r'      | tail -1 | sed -e "s/.*<string>//" -e "s/<\/string>.*//"; '
        '    exit 0; '
        '  fi; '
        'done'
    )

    try:
        result = subprocess.run(
            [
                "ssh",
                "-o", "StrictHostKeyChecking=no",
                "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=8",
                "-p", str(DEVICE_SSH_PORT),
                f"root@{DEVICE_SSH_HOST}",
                script,
            ],
            capture_output=True, text=True, check=False, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("读取设备版本失败: %s", exc)
        return None

    version = result.stdout.strip().splitlines()
    return version[0].strip() if version and version[0].strip() else None


def install(ipa_path: Path, *, on_stage=None) -> None:
    """
    把 IPA 安装到设备。需要 AppSync Unified(设备上已装)。

    用 appinst 而不是 ideviceinstaller:后者要经过 usbmux 的
    installation_proxy,对重签名的包限制更多;appinst 直接调用 installd,
    是越狱环境下的标准做法。

    先 push 到设备再本地安装,而不是从电脑侧远程装 —— 几百 MB 的包
    走 scp 比走 appinst 自己的传输更稳,也让进度可观测。
    """
    ensure_forward()

    if not ipa_path.exists():
        raise DeviceError(f"IPA 不存在: {ipa_path}")

    remote_path = f"/var/mobile/Documents/{ipa_path.name}"

    report = on_stage or (lambda *_: None)
    report("upload", f"上传 {ipa_path.name}({ipa_path.stat().st_size / 1024 / 1024:.0f} MB)")

    scp = subprocess.run(
        [
            "scp", "-q",
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            "-P", str(DEVICE_SSH_PORT),
            str(ipa_path),
            f"root@{DEVICE_SSH_HOST}:{remote_path}",
        ],
        capture_output=True, text=True, check=False,
    )
    if scp.returncode != 0:
        detail = (scp.stderr or scp.stdout).strip()[:200]
        raise DeviceError(f"上传 IPA 失败: {detail}")

    report("install", "在设备上安装")
    result = subprocess.run(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",
            "-p", str(DEVICE_SSH_PORT),
            f"root@{DEVICE_SSH_HOST}",
            f"/var/jb/usr/bin/mcp-appinst {shlex.quote(remote_path)}; "
            f"rc=$?; rm -f {shlex.quote(remote_path)}; exit $rc",
        ],
        capture_output=True, text=True, check=False,
    )

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-400:]
        raise DeviceError(f"设备安装失败:\n{detail}")

    logger.info("已安装到设备: %s", ipa_path.name)


def dump(bundle_id: str, *, output: Path, on_stage=None) -> DumpResult:
    """
    砸壳并把结果写到 output。

    参数:
        bundle_id: 目标应用的 Bundle ID。设备上必须已安装(砸壳是让应用
            在设备上运行后从内存取内容,没装就没法砸)。
        on_stage: 可选回调 (stage, detail),用于把进度写进任务状态。

    返回 DumpResult —— 含**设备上实际安装的版本号**,调用方用它命名文件。

    失败抛 DeviceError,message 可直接展示。
    """
    if FRIDA_PYTHON is None:
        raise DeviceError(
            "未找到带 frida 的 Python。请先:"
            "python3 -m venv ~/.local/frida-venv && "
            "~/.local/frida-venv/bin/pip install 'frida==17.23.3'"
        )

    if not DUMP_SCRIPT.exists():
        raise DeviceError(f"找不到砸壳脚本: {DUMP_SCRIPT}")

    ensure_forward()

    command = [
        str(FRIDA_PYTHON),
        str(DUMP_SCRIPT),
        bundle_id,
        "--output",
        str(output),
        "--ssh-host",
        DEVICE_SSH_HOST,
        "--ssh-port",
        str(DEVICE_SSH_PORT),
    ]

    logger.info("开始砸壳: %s", bundle_id)
    report = on_stage or (lambda *_: None)

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(DUMP_SCRIPT.parent.parent),
    )

    output_lines: list[str] = []

    assert process.stdout is not None
    for line in process.stdout:
        line = line.rstrip()
        if not line:
            continue

        output_lines.append(line)
        # 脚本的进度行形如 "  [read-memory] 读取 12.3 MB 解密内容"
        match = re.match(r"\s*\[([a-z-]+)\]\s*(.*)", line)
        if match:
            report(match.group(1), match.group(2))
        else:
            logger.info("[dump] %s", line)

    return_code = process.wait()

    if return_code != 0 or not output.exists():
        tail = "\n".join(output_lines[-12:]) or "(无输出)"
        raise DeviceError(f"砸壳失败(退出码 {return_code}):\n{tail}")

    result = _parse_result(output_lines, fallback_bundle_id=bundle_id, output=output)

    logger.info("砸壳完成: %s (设备版本 %s)", output, result.version or "未知")
    return result


def _parse_result(lines: list[str], *, fallback_bundle_id: str, output: Path) -> DumpResult:
    """
    从砸壳脚本的输出里取结果。

    脚本最后打印一行 `RESULT_JSON {...}`。用固定前缀而不是解析中文文案 ——
    文案会改,JSON 结构不会。

    解析不到时用兜底值:版本号只影响文件命名,不值得因此让整个砸壳失败。
    """
    for line in reversed(lines):
        text = line.strip()
        if not text.startswith("RESULT_JSON "):
            continue
        try:
            payload = json.loads(text[len("RESULT_JSON "):])
        except json.JSONDecodeError:
            break

        if isinstance(payload, dict):
            return DumpResult(
                path=output,
                version=payload.get("version"),
                build=payload.get("build"),
                bundle_id=payload.get("bundle_id") or fallback_bundle_id,
            )
        break

    return DumpResult(path=output, version=None, build=None, bundle_id=fallback_bundle_id)


# --------------------------------------------------------------------------
# 内部
# --------------------------------------------------------------------------
def _check_ssh() -> tuple[bool, str]:
    """
    用一条轻量命令确认 SSH 可用。

    ★ 这里有个容易写错的地方:端口通了**不等于**设备在线。
    `iproxy` 在没有设备时也会成功启动并一直等 —— 早期版本把「端口刚起来」
    当成「设备在线」直接返回 True,结果没插手机时界面也会显示手机端在线。
    所以起完转发之后必须继续往下做一次真实 SSH。
    """
    if not device_check.port_open(DEVICE_SSH_HOST, DEVICE_SSH_PORT):
        # 端口没通不代表设备不在:可能只是 iproxy 还没起(比如服务刚启动,
        # 或用户从没手动跑过)。这里让后端自己把转发拉起来 —— 用户不该为了
        # 一个前端按钮去手动执行 iproxy。
        try:
            ensure_forward()
        except DeviceError as exc:
            return False, str(exc)

        # 故意**不**在这里 return True:继续走下面的 SSH 检查。

    try:
        result = subprocess.run(
            [
                "ssh",
                "-o", "StrictHostKeyChecking=no",
                "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=5",
                "-p", str(DEVICE_SSH_PORT),
                f"root@{DEVICE_SSH_HOST}",
                "echo ok",
            ],
            capture_output=True, text=True, check=False,
            timeout=DEVICE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"SSH 检查失败: {exc}"

    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        first = detail[0] if detail else "未知错误"
        return False, f"SSH 认证失败: {first[:150]}"

    return True, "设备在线,可以砸壳"
