#!/usr/bin/env python3
"""
UnfairPlay 离线材料管理命令行工具。

为什么需要它
------------
UnfairPlay 的离线材料(enrollment 模型)来自一次「从 Mac 上抓取 + 离线重演」,
产出的是 `fairplay-enrollment.bin`。这份模型是**账号级**的 —— 哪个 Apple ID
下载的包,就得用哪个账号的模型解。

上游给的是三个独立子命令(collect / recover / decrypt),中间产物散在磁盘上,
而且 collect 要 sudo、输出目录的父目录还得先存在。本工具把这些串成一条:

    collect → recover → 装进 <模型目录>/<账号 id>.bin → verify 验证

用法
----
    cd backend

    # 一条命令搞定(需要 sudo 密码,会提示)
    python3 manage_unfairplay.py collect us \\
        --app "/Applications/Surge-iOS.app/Wrapper/Surge-iOS.app" \\
        --ipa ~/Downloads/surge.ipa

    python3 manage_unfairplay.py list                 # 账号与离线材料状态
    python3 manage_unfairplay.py verify us some.ipa   # 用真实包验证模型
    python3 manage_unfairplay.py remove us            # 删掉模型

`--app` 要点
------------
Mac 上装的 iOS 应用是**包在 Wrapper 里**的,SC_Info 和 Info.plist 在内层:

    /Applications/<App>.app/Wrapper/<App>.app     ← 传这一层

前置条件
--------
* 一台 Apple Silicon Mac,Mac App Store 登录了**目标账号**
* 从 Mac App Store 装了至少一个 iOS 应用(免费即可)并**打开过一次**
* 用同一个账号通过 ipatool 下载了**同一个 App** 的加密 IPA
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import accounts, unfairplay
from app.config import FAIRPLAY_MODE, MODELS_DIR


def _select(account_id: str) -> accounts.Account:
    account = accounts.get(account_id)
    if account is None:
        available = ", ".join(item.id for item in accounts.load()) or "(账号表为空)"
        raise SystemExit(f"账号表中没有 id 为 {account_id!r} 的账号。可用: {available}")

    return account


def _target(account: accounts.Account) -> Path:
    path = account.model
    if path is None:  # 账号表解析时总会填,防御性处理
        raise SystemExit(f"账号 {account.id} 没有模型路径")

    return path


def _work_dir(account: accounts.Account) -> Path:
    """
    中间产物目录。

    放 <模型目录>/../unfairplay/<账号 id>/ 下:capture 里含 fairplayd 副本
    和本机硬件身份(iomac.bin),属于私密数据,别散落到 /tmp。
    """
    return MODELS_DIR.parent / "unfairplay" / account.id


# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------
def cmd_list(_: argparse.Namespace) -> int:
    configured = accounts.load()
    if not configured:
        raise SystemExit(
            f"未找到账号表 {accounts.ACCOUNTS_FILE}。\n"
            "离线材料靠「区域 → 账号 → 材料」的映射,因此需要多账号表:\n"
            "    cp accounts.example.json accounts.json"
        )

    print(f"解密模式:   {FAIRPLAY_MODE}(auto = 有模型就离线,没有就退回设备)")
    print(f"账号表:     {accounts.ACCOUNTS_FILE}")
    print(f"模型目录:   {MODELS_DIR}")
    print("unfairplay: ", end="")
    try:
        print(unfairplay.binary())
    except unfairplay.UnfairplayError as exc:
        print(f"未就绪 —— {str(exc).splitlines()[0]}")

    print()
    for account in configured:
        print(f"  {account.id:6} [{account.region or '未探测':>6}] {account.label}")

        model = accounts.model_path(account)
        if model is None:
            print(f"         模型: 未安装({_target(account)})")
        else:
            meta = unfairplay.model_sidecar(model)
            size = model.stat().st_size
            print(f"         模型: 已就绪  {size} 字节")
            if meta.get("selector"):
                print(f"           selector: {meta['selector']}")
            if meta.get("installed_at"):
                print(f"           安装于:   {meta['installed_at']}")
            if meta.get("fairplayd_sha256"):
                print(f"           fairplayd: {meta['fairplayd_sha256'][:16]}…")

        print()

    ready = accounts.offline_regions()
    print(f"可离线解密的区域: {', '.join(ready) if ready else '(无)'}")

    return 0


# --------------------------------------------------------------------------
# collect(抓取 + 重演 + 安装 + 验证)
# --------------------------------------------------------------------------
def cmd_collect(args: argparse.Namespace) -> int:
    account = _select(args.account_id)
    app = Path(args.app).expanduser()
    ipa = Path(args.ipa).expanduser()

    work = _work_dir(account)
    capture = work / "capture"
    recovery = work / "recovery"

    print(f"账号:   {account.id}({account.label}, {account.region or '未探测'})")
    print(f"工作区: {work}")
    print()

    # collect 要求输出目录必须是**新**目录,重跑时先把旧的挪开而不是删 ——
    # capture 里有 fairplayd 副本和硬件身份,留一份便于事后比对。
    if capture.exists():
        backup = work / f"capture.old-{int(capture.stat().st_mtime)}"
        if backup.exists():
            shutil.rmtree(backup)
        capture.rename(backup)
        print(f"(旧 capture 已挪到 {backup.name})")

    work.mkdir(parents=True, exist_ok=True)

    print("① collect —— 抓取 Mac 的 FairPlay enrollment 状态")
    print("   (需要 root:要读 /private/var/db/fpsd,只读不写系统)")
    try:
        manifest = unfairplay.collect(
            app=app,
            ipa=ipa,
            output=capture,
            use_sudo=not args.no_sudo,
            on_stage=lambda text: print(f"   {text}"),
        )
    except unfairplay.UnfairplayError as exc:
        raise SystemExit(str(exc)) from exc

    support = manifest.get("fairplay_support") or {}
    print(
        f"   ✅ profile={support.get('profile')} status={support.get('status')} "
        f"selector={manifest.get('selector')}"
    )

    print("\n② recover —— 离线重演 fairplayd 的密钥派生")
    if recovery.exists():
        shutil.rmtree(recovery)
    try:
        recovered = unfairplay.recover(
            capture=capture, output=recovery, on_stage=lambda text: print(f"   {text}")
        )
    except unfairplay.UnfairplayError as exc:
        raise SystemExit(str(exc)) from exc

    print(
        f"   ✅ 解密 {recovered.get('decrypted_pages')} 页 / "
        f"{recovered.get('encrypted_images')} 个二进制"
    )

    model_source = recovery / "fairplay-enrollment.bin"
    if not model_source.is_file():
        raise SystemExit(f"recover 没产出模型文件: {model_source}")

    print("\n③ 安装模型")
    target = _target(account)
    target.parent.mkdir(parents=True, exist_ok=True)

    # 0600:模型等于这个账号在**这台 Mac 上**的解密能力,别让同机其他用户读到。
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(model_source.read_bytes())

    # sidecar:模型本身是二进制、不带可读元信息,界面要靠它显示归属。
    sidecar = {
        "account_id": account.id,
        "region": account.region,
        "selector": recovered.get("selector") or manifest.get("selector"),
        "fairplayd_sha256": recovered.get("fairplayd_sha256") or manifest.get("fairplayd_sha256"),
        "profile": support.get("profile"),
        "product_version": manifest.get("product_version"),
        "build_version": manifest.get("build_version"),
        "installed_at": recovered.get("created_at") or manifest.get("created_at"),
        "source_ipa": str(ipa),
        "decrypted_pages": recovered.get("decrypted_pages"),
    }
    target.with_suffix(".json").write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"   ✅ {target}  ({target.stat().st_size} 字节, 权限 0600)")

    print("\n④ verify —— 用真实包确认模型可用")
    try:
        payload = unfairplay.verify_model(ipa, target)
    except unfairplay.UnfairplayError as exc:
        raise SystemExit(f"   ❌ {exc}") from exc

    if payload.get("success"):
        print(
            f"   ✅ 验证通过: {payload.get('decrypted_pages')} 页 / "
            f"{payload.get('encrypted_images')} 个二进制"
        )
    else:
        print(f"   ⚠ 验证未通过: {json.dumps(payload, ensure_ascii=False)[:300]}")

    print()
    print("以后这个账号下的任何 IPA 都能离线解密(不需要设备、不需要再装 App 到 Mac):")
    print("  在界面上点「下载解密」即可;或 FAIRPLAY_MODE=offline 强制走离线。")

    return 0


def cmd_recover(args: argparse.Namespace) -> int:
    """只重跑 recover(比如 collect 已成功、只想要一份新模型)。"""
    account = _select(args.account_id)
    work = _work_dir(account)
    capture = Path(args.capture).expanduser() if args.capture else work / "capture"
    recovery = work / "recovery"

    if not capture.is_dir():
        raise SystemExit(f"capture 目录不存在: {capture}\n先跑 collect。")

    print("① doctor —— 先诊断 capture")
    try:
        report = unfairplay.doctor(capture=capture)
    except unfairplay.UnfairplayError as exc:
        raise SystemExit(str(exc)) from exc

    failed = [k for k, v in (report.get("checks") or {}).items() if not v]
    print(f"   ready={report.get('ready')} 检查未通过: {failed or '无'}")

    print("\n② recover")
    if recovery.exists():
        shutil.rmtree(recovery)
    recovered = unfairplay.recover(capture=capture, output=recovery)
    print(
        f"   ✅ 解密 {recovered.get('decrypted_pages')} 页 / "
        f"{recovered.get('encrypted_images')} 个二进制"
    )

    target = _target(account)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write((recovery / "fairplay-enrollment.bin").read_bytes())

    print(f"   ✅ 已安装到 {target}")

    return 0


# --------------------------------------------------------------------------
# verify / remove
# --------------------------------------------------------------------------
def cmd_verify(args: argparse.Namespace) -> int:
    """
    用真实 IPA 验证模型。

    判据是「模型能不能解开这个包的 SINF」—— 能明确区分两类失败:
      * 解不开           → 模型是别的账号的(或者模型被换过)
      * 解开了但结果异常 → 模型没问题,是包/系统版本不受支持
    """
    account = _select(args.account_id)
    model = accounts.model_path(account)
    if model is None:
        raise SystemExit(
            f"{account.id} 还没有模型:{_target(account)}\n"
            f"先跑: python3 manage_unfairplay.py collect {account.id} --app <App> --ipa <IPA>"
        )

    exit_code = 0
    for raw in args.ipa:
        ipa = Path(raw).expanduser()
        print(f"\n{ipa}")
        if not ipa.is_file():
            print("  跳过(文件不存在)")
            exit_code = 1
            continue

        try:
            payload = unfairplay.verify_model(ipa, model)
        except unfairplay.UnfairplayError as exc:
            print(f"  ❌ {exc}")
            exit_code = 1
            continue

        if payload.get("success"):
            print(
                f"  ✅ 可以解开: {payload.get('decrypted_pages')} 页 / "
                f"{payload.get('encrypted_images')} 个二进制"
            )
            print(f"     logical_key = {str(payload.get('logical_key_sha256'))[:32]}…")
        else:
            print(f"  ❌ 解不开: {json.dumps(payload, ensure_ascii=False)[:300]}")
            print("     模型必须来自「下载这个包所用的 Apple ID」的那台 Mac。")
            exit_code = 1

    return exit_code


def cmd_remove(args: argparse.Namespace) -> int:
    account = _select(args.account_id)
    path = _target(account)
    if not path.is_file():
        print(f"{account.id} 没有模型文件: {path}")
        return 1

    path.unlink()
    sidecar = path.with_suffix(".json")
    if sidecar.is_file():
        sidecar.unlink()

    work = _work_dir(account)
    if work.is_dir() and args.purge:
        shutil.rmtree(work)
        print(f"已删除中间产物: {work}")

    print(f"已删除 {account.id} 的模型: {path}")
    print("之后再砸壳会退回设备流程(除非 FAIRPLAY_MODE 指定了别的后端)。")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="UnfairPlay 离线材料管理(不需要越狱设备)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list", help="列出账号与离线材料状态").set_defaults(func=cmd_list)

    collect = subparsers.add_parser(
        "collect", help="抓取 + 重演 + 安装 + 验证(需要 sudo)"
    )
    collect.add_argument("account_id", help="accounts.json 里的 id,例如 us")
    collect.add_argument(
        "--app",
        required=True,
        help="Mac 上那个 iOS 应用的内层 bundle:/Applications/X.app/Wrapper/X.app",
    )
    collect.add_argument("--ipa", required=True, help="同一个 App 的**加密** IPA")
    collect.add_argument("--no-sudo", action="store_true", help="不加 sudo(已用 root 跑时)")
    collect.set_defaults(func=cmd_collect)

    recover = subparsers.add_parser("recover", help="只重跑 recover(用已有的 capture)")
    recover.add_argument("account_id")
    recover.add_argument("--capture", help="capture 目录(默认用工作区里那份)")
    recover.set_defaults(func=cmd_recover)

    verify = subparsers.add_parser("verify", help="用真实 IPA 验证模型属于这个账号")
    verify.add_argument("account_id")
    verify.add_argument("ipa", nargs="+", help="加密的 IPA(可以是多个)")
    verify.set_defaults(func=cmd_verify)

    remove = subparsers.add_parser("remove", help="删除模型")
    remove.add_argument("account_id")
    remove.add_argument("--purge", action="store_true", help="连中间产物(capture/recovery)一起删")
    remove.set_defaults(func=cmd_remove)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
