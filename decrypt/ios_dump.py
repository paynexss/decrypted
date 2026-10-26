#!/usr/bin/env python3
"""
iOS 应用砸壳(解密)工具。

原理
----
App Store 下发的 IPA 里,主可执行文件的加密区段是**密文**;应用运行时内核
会把它解密到内存。砸壳的本质:

    从设备取回原始 Mach-O  →  用内存里的解密内容覆盖加密区段  →  重新打包

要覆盖的字节范围由 Mach-O 头里的 `LC_ENCRYPTION_INFO_64` 给出
(`cryptoff` + `cryptsize`)。其余区段磁盘上本来就是明文,不需要动。

分工
----
为什么同时用 SSH 和 frida —— 这是踩出来的:

* **frida 只用来读内存。** 它的 File API 没有列目录的能力
  (实测方法只有 tell/seek/readBytes/readText/readLine/write/flush/close),
  要在里面递归遍历 bundle 得自己写一坨;而且从进程内访问别人拼出来的
  `/var/containers/Bundle/Application/...` 会被沙盒拒绝
  (`Operation not permitted`)。

* **取文件走 SSH。** 设备上有 root SSH,`scp -r` 一条命令就能把整个 .app
  连目录结构、权限、符号链接一起拉下来,比在 frida 里手工重建可靠得多。

唯一必须用 frida 的是「读内存里的解密内容」—— 这件事只有注入进程才能做。

环境
----
针对 iPhone 11 Pro Max / iOS 26.0.1 / Dopamine rootless / frida 17.23.3 验证。

避开的一个坑:frida 17 + iOS 26 上,`device.attach()` 直接附加到 `spawn()`
出来的**挂起态**进程会报 `ProcessNotRespondingError: unexpected early
end-of-stream`。必须先 `device.resume(pid)`,等进程真正跑起来再附加。

前置条件
--------
    iproxy 2222 22 &          # SSH over USB;或设备与电脑同网段时用 WiFi IP

用法
----
    python3 decrypt/ios_dump.py com.example.app --output out.ipa
    python3 decrypt/ios_dump.py com.example.app --ssh-host 192.168.1.20
"""

from __future__ import annotations

import argparse
import json
import plistlib
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

try:
    import frida
except ImportError:
    sys.exit(
        "缺少 frida 模块。安装:\n"
        "    python3 -m venv ~/.local/frida-venv\n"
        "    ~/.local/frida-venv/bin/pip install 'frida==17.23.3'"
    )


# --------------------------------------------------------------------------
# frida 注入脚本:只做一件 SSH 做不到的事 —— 从进程内存里取解密后的字节
# --------------------------------------------------------------------------
# 注意:脚本内不使用正则字面量。跨语言转义会让 /.../ 里的斜杠被误当成
# 正则结束符,报 "invalid regular expression flags"。一律用 indexOf 代替。
_MEMORY_SCRIPT = r"""
rpc.exports = {
    // 主模块的 Mach-O 结构信息:路径、加密区段范围、各段文件偏移
    inspect: function () {
        var main = _mainModule();
        var base = main.base;
        var u32 = function (offset) { return base.add(offset).readU32(); };

        var info = {
            path: main.path,
            bundlePath: main.path.substring(0, main.path.indexOf('.app/') + 4),
            ncmds: u32(16),
            sizeofcmds: u32(20),
            encryption: null,
            segments: [],
        };

        var offset = 32;
        for (var i = 0; i < info.ncmds && i < 2000; i++) {
            var cmd = u32(offset);
            var cmdsize = u32(offset + 4);
            if (cmdsize === 0) break;

            if (cmd === 0x2C) {          // LC_ENCRYPTION_INFO_64
                info.encryption = {
                    cryptoff: u32(offset + 8),
                    cryptsize: u32(offset + 12),
                    cryptid: u32(offset + 16),
                };
            } else if (cmd === 0x19) {   // LC_SEGMENT_64
                info.segments.push({
                    name: _readCStr(base.add(offset + 8), 16),
                    vmaddr: base.add(offset + 24).readU64().toString(),
                    fileoff: base.add(offset + 40).readU64().toString(),
                    filesize: base.add(offset + 48).readU64().toString(),
                });
            }
            offset += cmdsize;
        }

        return info;
    },

    // 读内存 [base + offset, base + offset + size)。
    // 分块读:一次性读几百 MB 会超过 frida 的消息长度上限。
    readMemory: function (offset, size) {
        var main = _mainModule();
        var CHUNK = 4 * 1024 * 1024;
        var chunks = [];
        var cursor = offset;
        var remaining = size;

        while (remaining > 0) {
            var take = Math.min(CHUNK, remaining);
            chunks.push(new Uint8Array(main.base.add(cursor).readByteArray(take)));
            cursor += take;
            remaining -= take;
        }

        var total = 0;
        for (var i = 0; i < chunks.length; i++) total += chunks[i].length;

        var merged = new Uint8Array(total);
        var at = 0;
        for (var i = 0; i < chunks.length; i++) {
            merged.set(chunks[i], at);
            at += chunks[i].length;
        }
        // 返回普通数组:frida 对 ArrayBuffer 的序列化形态在不同版本间变过,
        // 数组最稳。
        return Array.prototype.slice.call(merged);
    },
};

// 主可执行模块 = 路径里含 .app/ 的那个。
// 用实际路径判断,不自己拼容器路径 —— 拼出来的会被沙盒拒绝。
function _mainModule() {
    var modules = Process.enumerateModules();
    for (var i = 0; i < modules.length; i++) {
        if (modules[i].path.indexOf('.app/') >= 0) return modules[i];
    }
    throw new Error('未找到主可执行模块');
}

function _readCStr(ptr, max) {
    var bytes = new Uint8Array(ptr.readByteArray(max));
    var text = '';
    for (var i = 0; i < max; i++) {
        if (bytes[i] === 0) break;
        text += String.fromCharCode(bytes[i]);
    }
    return text;
}
"""


class DumpError(RuntimeError):
    """砸壳失败。message 可直接展示给用户。"""


@dataclass
class DumpResult:
    """
    砸壳产出。

    version / build 来自**设备上安装的那个包**的 Info.plist ——
    不是 App Store 商店里的最新版。用户可能没升级,两者会不一致,
    而产出的内容对应的是设备上那一份。
    """

    path: Path
    version: str | None
    build: str | None
    bundle_id: str


# --------------------------------------------------------------------------
# SSH:设备上的命令执行与文件传输
# --------------------------------------------------------------------------
class Ssh:
    def __init__(self, host: str, port: int, user: str = "root") -> None:
        self.host = host
        self.port = port
        self.user = user

    @property
    def _target(self) -> str:
        return f"{self.user}@{self.host}"

    def _ssh_options(self) -> list[str]:
        """ssh / scp 共用的连接选项(不含端口)。"""
        return [
            "-o", "StrictHostKeyChecking=no",
            "-o", "BatchMode=yes",  # 免密;需要交互输密码就没法自动化
            "-o", "ConnectTimeout=8",
        ]

    def run(self, command: str) -> str:
        result = subprocess.run(
            ["ssh", *self._ssh_options(), "-p", str(self.port), self._target, command],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:300]
            raise DumpError(f"设备命令失败: {command}\n{detail}")
        return result.stdout

    def pull(self, remote_path: str, local_dir: Path) -> None:
        """
        递归拉取目录/文件到本地。

        端口用大写 -P。scp 的 -p 是「保留时间戳」,写成小写会把端口号
        当成文件名,报 `cp: 2222: No such file or directory`。
        """
        result = subprocess.run(
            ["scp", "-q", "-r", *self._ssh_options(), "-P", str(self.port),
             f"{self._target}:{remote_path}", str(local_dir)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:300]
            raise DumpError(f"拉取 {remote_path} 失败:\n{detail}")


def _find_app_bundle(ssh: Ssh, bundle_id: str) -> str:
    """
    在设备上定位应用的 bundle 目录。

    实现说明(踩过的坑):
    * 设备上的 plutil 是旧版 odcctools 带的,`-extract <key> raw <file>` 的
      参数顺序不适用(报 "Object not found at keypath")。改用 grep 直接读
      Info.plist —— 它对 plist 的 XML 格式足够,且不依赖任何越狱工具。
    * 先按目录名猜一次(应用名通常等于 bundle 目录名),命中就省掉遍历;
      猜不中再遍历容器目录。83 个应用里遍历也就一两秒,不必强求索引。
    """
    # 快路径:目录名与 .app 名一致时直接命中
    name_hint = bundle_id.rsplit(".", 1)[-1]
    quick = ssh.run(
        f'find /var/containers/Bundle/Application -maxdepth 2 '
        f'-iname "{name_hint}.app" -type d 2>/dev/null | head -1'
    ).strip()
    if quick:
        return quick.splitlines()[0]

    # 慢路径:遍历所有容器,按 Info.plist 里的 CFBundleIdentifier 匹配
    script = (
        'find /var/containers/Bundle/Application -maxdepth 2 -name "*.app" -type d 2>/dev/null | '
        'while read -r app; do '
        f'  if grep -q -A1 "<key>CFBundleIdentifier</key>" "$app/Info.plist" 2>/dev/null && '
        f'     grep -A1 "<key>CFBundleIdentifier</key>" "$app/Info.plist" | grep -q "{bundle_id}"; then '
        '    echo "$app"; break; '
        '  fi; '
        'done'
    )
    output = ssh.run(script).strip()

    if not output:
        raise DumpError(
            f"设备上找不到 bundle id 为 {bundle_id} 的应用。它安装了吗?"
        )

    return output.splitlines()[0]


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def dump(
    bundle_id: str,
    *,
    output: Path,
    ssh_host: str,
    ssh_port: int,
    launch_wait: float = 3.0,
    on_stage=None,
) -> DumpResult:
    """
    砸壳并写入 output。

    返回 DumpResult —— 里面带了从**设备上**读到的真实版本号。
    调用方需要用它来命名文件:App Store 商店里的版本可能比设备上装的更新
    (用户没升级),用商店版本命名会让文件名与内容不符。
    """
    report = on_stage or (lambda *_: None)
    ssh = Ssh(ssh_host, ssh_port)

    # --- 1. 定位并拉取 bundle ---
    report("locate", f"在设备上定位 {bundle_id}")
    remote_bundle = _find_app_bundle(ssh, bundle_id)

    with tempfile.TemporaryDirectory(prefix="iosdump-") as workdir_raw:
        workdir = Path(workdir_raw)

        report("pull", f"拉取 {Path(remote_bundle).name}")
        ssh.pull(remote_bundle, workdir)

        candidates = list(workdir.rglob("*.app"))
        if not candidates:
            raise DumpError(f"拉取后未找到 .app 目录(远端: {remote_bundle})")
        local_bundle = candidates[0]

        # --- 2. 读 Info.plist:主可执行文件名 + 真实版本 ---
        info_plist = local_bundle / "Info.plist"
        if not info_plist.exists():
            raise DumpError("bundle 里没有 Info.plist,拉取可能不完整")

        with info_plist.open("rb") as handle:
            plist = plistlib.load(handle)

        executable = plist.get("CFBundleExecutable")
        if not executable:
            raise DumpError("Info.plist 缺少 CFBundleExecutable")

        # 这两个字段都来自**设备上实际安装的包**,与商店最新版可能不同
        version = plist.get("CFBundleShortVersionString") or plist.get("CFBundleVersion")
        build = plist.get("CFBundleVersion")

        binary = local_bundle / executable
        if not binary.exists():
            raise DumpError(f"主可执行文件不存在: {executable}")

        # --- 3. 从内存取解密内容并写回 ---
        _patch_with_memory(bundle_id, binary, launch_wait=launch_wait, report=report)

        # --- 4. 打包 ---
        report("package", "打包 IPA")
        output.parent.mkdir(parents=True, exist_ok=True)
        _zip_app(local_bundle, output)

    report("done", f"完成: {output.name}")
    return DumpResult(
        path=output,
        version=str(version) if version else None,
        build=str(build) if build else None,
        bundle_id=str(plist.get("CFBundleIdentifier") or bundle_id),
    )


def _patch_with_memory(bundle_id: str, binary: Path, *, launch_wait: float, report) -> None:
    """启动应用,读出内存里的解密区段,覆盖磁盘上的二进制。"""
    try:
        device = frida.get_usb_device(timeout=10)
    except Exception as exc:
        raise DumpError(f"无法连接设备: {exc}") from exc

    try:
        pid = device.spawn(bundle_id)
    except Exception as exc:
        raise DumpError(f"无法启动 {bundle_id}: {exc}") from exc

    try:
        # 必须先 resume。见模块开头的「坑」。
        device.resume(pid)
        time.sleep(launch_wait)

        report("attach", f"附加到进程 {pid}")
        try:
            session = device.attach(pid)
        except Exception as exc:
            raise DumpError(
                f"附加失败: {exc}\n"
                "若报 early end-of-stream,通常是目标进程启动后立即退出了 —— "
                "先手动打开该应用,确认它能正常运行。"
            ) from exc

        try:
            script = session.create_script(_MEMORY_SCRIPT)
            script.load()

            info = script.exports_sync.inspect()
            encryption = info.get("encryption")

            if encryption is None:
                report("plain", "该二进制没有加密信息,无需解密")
                return

            if encryption.get("cryptid") == 0:
                report("plain", "该二进制已解密(cryptid=0)")
                return

            cryptoff = encryption["cryptoff"]
            cryptsize = encryption["cryptsize"]

            if cryptoff + cryptsize > binary.stat().st_size:
                raise DumpError(
                    f"加密区段超出文件范围: cryptoff={cryptoff} cryptsize={cryptsize} "
                    f"文件大小={binary.stat().st_size}。"
                    "拉到的二进制与运行中的可能不是同一份。"
                )

            report(
                "read-memory",
                f"读取 {cryptsize / 1024 / 1024:.1f} MB 解密内容(文件偏移 0x{cryptoff:x})",
            )
            try:
                decrypted = bytes(script.exports_sync.readMemory(cryptoff, cryptsize))
            except Exception as exc:
                raise DumpError(f"读取内存失败: {exc}") from exc

            if len(decrypted) != cryptsize:
                raise DumpError(
                    f"读取长度不符:期望 {cryptsize} 字节,实得 {len(decrypted)} 字节"
                )

            with binary.open("r+b") as handle:
                handle.seek(cryptoff)
                handle.write(decrypted)

            _clear_cryptid(binary)
            report("patched", "已写回解密内容并清除 cryptid")
        finally:
            try:
                session.detach()
            except Exception:
                pass
    finally:
        # 进程由我们启动,收掉;用户手动开的不会走到这里。
        try:
            device.kill(pid)
        except Exception:
            pass


def _clear_cryptid(binary: Path) -> None:
    """
    把 LC_ENCRYPTION_INFO_64 的 cryptid 置 0。

    不清的话,安装后系统仍会尝试解密一段已经是明文的区段,应用启动会崩。
    """
    with binary.open("r+b") as handle:
        header = handle.read(32)
        if len(header) < 32:
            raise DumpError("文件太短,不是有效的 Mach-O")

        ncmds = struct.unpack_from("<I", header, 16)[0]
        sizeofcmds = struct.unpack_from("<I", header, 20)[0]
        commands = handle.read(sizeofcmds)

        offset = 0
        for _ in range(ncmds):
            if offset + 8 > len(commands):
                break

            cmd, cmdsize = struct.unpack_from("<II", commands, offset)
            if cmdsize == 0:
                break

            if cmd == 0x2C:  # LC_ENCRYPTION_INFO_64
                # 布局:cmd(4) cmdsize(4) cryptoff(4) cryptsize(4) cryptid(4) pad(4)
                handle.seek(32 + offset + 16)
                handle.write(struct.pack("<I", 0))
                return

            offset += cmdsize

    # 没有这条指令不算错误:有些二进制本来就没有。
    return


def _zip_app(app_dir: Path, output: Path) -> None:
    """
    把 .app 目录打成 IPA。

    用 zipfile 而不是 shell 的 zip:后者对权限位与中文文件名的处理跨平台不一致。
    """
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(app_dir.rglob("*")):
            if path.is_symlink():
                # 符号链接需要 zip 的属性位才能正确还原,iOS 上写错会导致签名
                # 校验失败。绝大多数应用不依赖它,跳过更稳。
                continue

            rel = path.relative_to(app_dir)
            arcname = f"Payload/{app_dir.name}/{rel.as_posix()}"

            if path.is_dir():
                info = zipfile.ZipInfo(f"{arcname}/")
                info.external_attr = (0o40755 << 16) | 0x10
                archive.writestr(info, b"")
                continue

            info = zipfile.ZipInfo(arcname)
            info.compress_type = zipfile.ZIP_DEFLATED
            # 保留可执行位:iOS 上 framework 的实际文件需要这个位才能被加载
            info.external_attr = (0o755 if path.stat().st_mode & 0o111 else 0o644) << 16
            info.date_time = time.localtime(path.stat().st_mtime)[:6]
            with path.open("rb") as handle:
                archive.writestr(info, handle.read())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="iOS 应用砸壳(SSH 取文件 + frida 读内存解密内容)",
    )
    parser.add_argument("bundle_id", help="目标应用的 Bundle ID")
    parser.add_argument("-o", "--output", type=Path, help="输出 IPA 路径")
    parser.add_argument("--output-dir", type=Path, default=Path("dumps"), help="输出目录")
    parser.add_argument(
        "--ssh-host", default="127.0.0.1",
        help="设备 SSH 地址(默认 127.0.0.1,配合 iproxy 转发的 2222 端口)",
    )
    parser.add_argument("--ssh-port", type=int, default=2222, help="SSH 端口")
    parser.add_argument("--launch-wait", type=float, default=3.0, help="spawn 后等待秒数")

    args = parser.parse_args()

    output = args.output or (args.output_dir / f"{args.bundle_id}.ipa")

    def stage(name: str, detail: str) -> None:
        print(f"  [{name}] {detail}", flush=True)

    try:
        result = dump(
            args.bundle_id,
            output=output,
            ssh_host=args.ssh_host,
            ssh_port=args.ssh_port,
            launch_wait=args.launch_wait,
            on_stage=stage,
        )
    except DumpError as exc:
        print(f"\n失败: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130

    size_mb = result.path.stat().st_size / 1024 / 1024
    print(f"\n完成: {result.path} ({size_mb:.1f} MB)")

    # 给调用方(后端)的机器可读结果。用固定前缀而非解析上面的中文行 ——
    # 文案会改,这个 JSON 结构不会。
    print(
        "RESULT_JSON " + json.dumps({
            "path": str(result.path),
            "version": result.version,
            "build": result.build,
            "bundle_id": result.bundle_id,
            "size": result.path.stat().st_size,
        }, ensure_ascii=False),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
