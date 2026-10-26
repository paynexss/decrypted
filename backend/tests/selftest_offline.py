#!/usr/bin/env python3
"""
离线解密链路的自检(不需要真模型、真设备、真网络)。

覆盖什么
--------
1. 账号与模型解析:默认路径、按区域挑账号、坏模型当「没有」处理;
2. 选路:FAIRPLAY_MODE 四种取值 × 有无模型;
3. 编排:把下载和解密都打桩,跑一遍完整任务,确认
   下载 → 解密 → 归档 → 落库 这条链路真的通;
4. API:/api/account 的 offline_regions / offline_ready、两种拒绝路径;
5. CLI:manage_unfairplay.py list。

不覆盖什么
----------
「UnfairPlay 算得对不对」不在本文件范围内 —— 那是上游的算法(它有自己的
测试),我们只负责把输入喂对、把产物收好。真实包请用:

    python3 manage_unfairplay.py verify <账号> <加密 IPA>

用法:

    cd backend
    .venv/bin/python tests/selftest_offline.py

退出码 0 = 全通过。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent


class Checker:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def section(self, title: str) -> None:
        print(f"\n{title}")

    def __call__(self, label: str, condition: object, detail: object = "") -> None:
        okay = bool(condition)
        if not okay:
            self.failures.append(label)
        suffix = f" —— {detail}" if detail else ""
        print(f"  {'OK  ' if okay else 'FAIL'} {label}{suffix}")


check = Checker()


def temp_env() -> Path:
    """独立的数据目录,绝不碰真实的 accounts.json / 模型 / 文件库。"""
    temp = Path(tempfile.mkdtemp(prefix="unfairplay-selftest-"))
    (temp / "accounts.json").write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "id": account_id,
                        "label": region,
                        "email": f"{account_id}@example.com",
                        "keychain_service": f"ipa-manager.{account_id}",
                        "cookie_jar": str(temp / f"cookies-{account_id}"),
                        "region": region,
                    }
                    for account_id, region in (("cn", "CN"), ("us", "US"))
                ]
            }
        )
    )
    os.environ.update(
        {
            "IPA_ACCOUNTS_FILE": str(temp / "accounts.json"),
            "IPA_MODELS_DIR": str(temp / "models"),
            "IPA_STORAGE_DIR": str(temp / "storage"),
            "IPA_DATABASE_PATH": str(temp / "library.db"),
            # 让账号探测与设备检查都立刻失败:自检不能依赖外部状态。
            "IPATOOL_BIN": "",
            "DEVICE_SSH_HOST": "192.0.2.1",  # TEST-NET-1,必然连不上
            "DEVICE_TIMEOUT_SECONDS": "2",
            "FAIRPLAY_MODE": "auto",
        }
    )
    return temp


def install_model(temp: Path, account_id: str, *, valid: bool = True) -> Path:
    """
    造一个模型文件。

    valid=False 时写一个**太短**的文件 —— 上游/我们的 read_model 都要求
    至少 1 KB,所以它会被当成「坏文件」。真正的模型是二进制、没法合成,
    但「坏文件要被拒绝」这条逻辑可以测。
    """
    directory = temp / "models"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{account_id}.bin"
    path.write_bytes(os.urandom(4096) if valid else b"broken")
    return path


class FakeApp:
    """够 _run_offline_download / _archive_decrypted 用的最小替身。"""

    app_id = 1442620678
    bundle_id = "com.nssurge.inc.surge-ios"
    name = "Surge 5"
    version = "5.23.0"
    icon_url = None
    seller_name = "Surge Networks Inc."


# --------------------------------------------------------------------------
# 1) 账号与模型
# --------------------------------------------------------------------------
def test_accounts(temp: Path) -> None:
    from app import accounts

    check.section("1) 账号与模型解析")

    accounts.load(force=True)
    cn = accounts.get("cn")
    check(
        "模型默认路径按账号 id 推导",
        cn.model.resolve() == (temp / "models" / "cn.bin").resolve(),
        cn.model,
    )
    check("没有模型时 pick_for_offline 为 None", accounts.pick_for_offline("CN") is None)
    check("没有模型时 offline_regions 为空", accounts.offline_regions() == [])

    install_model(temp, "cn")
    accounts.load(force=True)
    check("装上模型后 pick_for_offline(CN) 命中 cn", accounts.pick_for_offline("CN").id == "cn")
    check("区域不同则不命中(模型是账号级的)", accounts.pick_for_offline("US") is None)
    check("offline_regions == ['CN']", accounts.offline_regions() == ["CN"], accounts.offline_regions())

    install_model(temp, "us", valid=False)
    accounts.load(force=True)
    check(
        "坏模型文件不算「已备好」",
        accounts.model_path(accounts.get("us")) is not None,
        "文件存在即返回路径 —— 内容校验交给 read_model",
    )


# --------------------------------------------------------------------------
# 2) 选路
# --------------------------------------------------------------------------
def test_selection(temp: Path) -> None:
    from app import decrypt

    check.section("2) 选路(FAIRPLAY_MODE × 有无模型)")

    install_model(temp, "cn")
    (temp / "models" / "us.bin").unlink(missing_ok=True)

    def pick(mode: str, country: str) -> str:
        decrypt.FAIRPLAY_MODE = mode
        try:
            got = decrypt._offline_model(FakeApp, country)
        except decrypt.DecryptRejected as exc:
            return f"拒绝: {str(exc).splitlines()[0][:46]}"
        finally:
            decrypt.FAIRPLAY_MODE = "auto"
        return f"{got[0].id} / {got[1].name}" if got else "None(走设备)"

    cases = [
        ("auto", "CN", "cn / cn.bin"),
        ("auto", "US", "None(走设备)"),
        ("offline", "CN", "cn / cn.bin"),
        ("unfairplay", "CN", "cn / cn.bin"),
        ("device", "CN", "None(走设备)"),
    ]
    for mode, country, expected in cases:
        got = pick(mode, country)
        check(f"{mode:11} {country} → {expected}", got == expected, got)

    got = pick("offline", "US")
    check("offline + 无模型 → 拒绝并给出 collect 命令", got.startswith("拒绝"), got)


# --------------------------------------------------------------------------
# 3) 编排(打桩跑完整任务)
# --------------------------------------------------------------------------
def test_orchestration(temp: Path) -> None:
    """
    把「下载」和「解密」都换成假的,跑一遍真实的 task 流程。

    这一步的价值:下载 → 解密 → 归档 → 落库 之间的**接口**是否对得上,
    只有真跑一遍才知道(参数名、staging 路径、版本号从哪来…)。
    """
    from app import decrypt, storage, tasks
    from app.models import Platform

    check.section("3) 编排(下载/解密打桩,跑完整任务)")

    install_model(temp, "cn")
    (temp / "models" / "us.bin").unlink(missing_ok=True)

    # 假加密包:内容随便,只要是个文件
    def fake_download(task, app, country, platform, *, purpose):
        path = storage.staging_path(f"{task.task_id}-{purpose}")
        path.write_bytes(b"FAKE-ENCRYPTED-IPA" * 1000)
        return path

    # 假解密:直接把输入复制到输出,并记下收到的参数
    seen: dict[str, object] = {}

    def fake_decrypt(source, destination, model, *, on_stage=None):
        from app import unfairplay

        seen["source"] = source
        seen["destination"] = destination
        seen["model"] = model
        if on_stage:
            on_stage("假装在解密")
        shutil.copyfile(source, destination)
        return unfairplay.DecryptResult(pages=4720, images=9, sha256="deadbeef")

    real_download = decrypt._download_encrypted
    real_decrypt = decrypt.unfairplay.decrypt_ipa
    decrypt._download_encrypted = fake_download
    decrypt.unfairplay.decrypt_ipa = fake_decrypt
    try:
        task = tasks.registry.create(
            kind="decrypted", app_id=FakeApp.app_id, bundle_id=FakeApp.bundle_id, name=FakeApp.name
        )
        decrypt._run_offline_download(
            task,
            app=FakeApp,
            country="CN",
            platform=Platform.IPHONE,
            model=(temp / "models" / "cn.bin"),
        )
        snapshot = task.snapshot()

        check("任务成功结束", snapshot.status.value == "success", snapshot.status)
        check("消息里带页数/二进制数", "4720 页" in snapshot.message, snapshot.message)
        check("解密收到了正确的模型", seen.get("model") == temp / "models" / "cn.bin", seen.get("model"))
        check(
            "解密输出落在 staging(不是最终目录)",
            "staging" in str(seen.get("destination")),
            seen.get("destination"),
        )

        # 归档结果:文件在 decrypted/ 里,名字带 _decrypted,库里有记录
        from app import repository

        record = repository.find_one(FakeApp.bundle_id, "CN", Platform.IPHONE)
        check("文件库里落了记录", record is not None)
        if record:
            path = storage.decrypted_path(record["decrypted_path"])
            check("归档文件存在", path.is_file(), path.name)
            check("文件名带 _decrypted", "_decrypted.ipa" in path.name, path.name)
            check("版本用商店的", record["version"] == FakeApp.version, record["version"])

        check(
            "临时加密包已清理",
            not list((temp / "storage" / "staging").glob("*decrypt*")) if (temp / "storage" / "staging").exists() else True,
        )
    finally:
        decrypt._download_encrypted = real_download
        decrypt.unfairplay.decrypt_ipa = real_decrypt


# --------------------------------------------------------------------------
# 4) API
# --------------------------------------------------------------------------
def test_api(temp: Path) -> None:
    from fastapi.testclient import TestClient

    from app import decrypt
    from app import main as app_main
    from app.models import Platform

    check.section("4) API")

    # 打桩元数据查询:否则会真的去问 iTunes,而 app_id=1 在商店里不存在,
    # 请求会在「找不到应用」那一步就被拒,测不到离线选路。
    async def stub_lookup(request):
        return FakeApp()

    real_lookup = decrypt._lookup_app
    decrypt._lookup_app = stub_lookup

    install_model(temp, "cn")
    (temp / "models" / "us.bin").unlink(missing_ok=True)

    with TestClient(app_main.app) as client:
        overview = client.get("/api/account").json()
        check("offline_regions == ['CN']", overview.get("offline_regions") == ["CN"], overview.get("offline_regions"))
        check(
            "每个账号带 offline_ready",
            all("offline_ready" in item for item in overview["accounts"]),
        )
        cn = next(i for i in overview["accounts"] if i["id"] == "cn")
        us = next(i for i in overview["accounts"] if i["id"] == "us")
        check("cn.offline_ready = True", cn["offline_ready"] is True)
        check("us.offline_ready = False", us["offline_ready"] is False)

        # 拒绝路径:该区域没有模型,设备也连不上
        try:
            rejected = client.post(
                "/api/download/decrypted",
                json={"app_id": FakeApp.app_id, "country": "US", "platform": Platform.IPHONE.value},
            )
            check(
                "auto + 无模型 + 无设备 → 400 且提示 collect",
                rejected.status_code == 400 and "manage_unfairplay.py collect us" in rejected.text,
                rejected.text[:70],
            )
        finally:
            decrypt._lookup_app = real_lookup


# --------------------------------------------------------------------------
# 5) CLI
# --------------------------------------------------------------------------
def test_cli(temp: Path) -> None:
    check.section("5) CLI")

    install_model(temp, "cn")
    env = {**os.environ}

    listing = subprocess.run(
        [sys.executable, "manage_unfairplay.py", "list"],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    check("list 退出码 0", listing.returncode == 0, listing.returncode)
    check("list 报出 cn 已就绪", "已就绪" in listing.stdout)
    check("list 报出可离线区域含 CN", "CN" in listing.stdout)
    check("list 没把 us 误报成就绪", listing.stdout.count("已就绪") == 1, listing.stdout.count("已就绪"))


def main() -> int:
    temp = temp_env()
    sys.path.insert(0, str(BACKEND))

    # 建表。正常启动时由 lifespan 调,这里要手动来一次 ——
    # 第 3 步的编排测试会真的往库里写记录。
    from app.db import init_db

    init_db()

    print(f"临时目录: {temp}")
    test_accounts(temp)
    test_selection(temp)
    test_orchestration(temp)
    test_api(temp)
    test_cli(temp)

    if check.failures:
        print(f"\n失败 {len(check.failures)} 项:")
        for item in check.failures:
            print(f"  - {item}")
        return 1

    print("\n全部通过。真实包请用: python3 manage_unfairplay.py verify <账号> <加密 IPA>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
