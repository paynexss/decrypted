"""
「下载解密 IPA」的完整流程。

有两条路,能走第一条就走第一条:

A. 离线解密(不需要设备)
   条件:目标区域有账号、且这个账号的 16 字节 FairPlay 凭证已经就位。
   步骤:下载加密包 → 在电脑上用 transform 逐页解掉 → 归档。
   模型是账号级的,一台 Mac + 一个 Apple ID 收一次;细节见 app/unfairplay.py。

B. 设备砸壳(frida 读内存)
   条件:有越狱设备在线。凭证缺失、或包用了离线路暂不支持的格式时走这条。
   步骤:确认设备上有这个应用(没有就先装)→ frida 读内存 → 归档。

关于 B 的第 2 步:砸壳的本质是让应用运行后从内存读解密内容,没装的包在内核里
根本不会被解密。所以应用必须先出现在设备上。两种来源:

  * 设备上已经装着(用户在 App Store 正常装的) → 直接用,这是最常见的情况;
  * 设备上没有                                  → 自动下载加密包并装上去。

两条路对用户是透明的:界面上的按钮只有一个「下载解密」。加密包只是中间产物。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from . import (
    accounts,
    device,
    ipatool,
    itunes,
    repository,
    storage,
    tasks,
    unfairplay,
)
from .config import FAIRPLAY_MODE, IPATOOL_AUTO_PURCHASE
from .models import AppSummary, EncryptedDownloadRequest, Platform, TaskStage
from .tasks import Task

logger = logging.getLogger(__name__)


class DecryptRejected(RuntimeError):
    """请求在开始前就被拒绝。同步返回给用户,不会创建任务。"""


async def start_decrypted_download(request: EncryptedDownloadRequest) -> Task:
    """
    创建解密任务并立即返回。

    前置检查在这里做,避免用户等到任务跑一半才发现设备没连、或凭证不对。
    """
    app = await _lookup_app(request)
    offline = _offline_model(app, request.country)

    if offline is not None:
        account, model = offline
        _reject_if_already_dumped(app, request, app.version)

        task = _create_task(app)
        logger.info("账号 %s 用模型离线解密 %s", account.id, app.name)
        tasks.registry.submit(
            task,
            lambda current: _run_offline_download(
                current,
                app=app,
                country=request.country,
                platform=request.platform,
                model=model,
            ),
        )
        return task

    # --- 以下都是设备砸壳路径 ---
    status = device.get_status()
    if not status.online:
        raise DecryptRejected(
            f"设备不可用:{status.detail}\n"
            "砸壳需要越狱设备在线(USB 连接 + OpenSSH + 设备已信任本电脑)。\n"
            + _offline_hint(request.country)
        )

    # 设备上有没有这个应用,决定了走哪条路。这一步要连设备,放到线程里。
    already_installed = await asyncio.to_thread(device.is_installed, app.bundle_id)

    # 设备上的版本已经砸过了吗?
    #
    # 这一步能省掉一次完整的砸壳(大包要一两分钟)。判断依据是设备上
    # 已安装应用的 Info.plist 里的版本号 —— 它才是产出内容对应的版本,
    # 不能用商店的最新版:用户可能没升级,商店版号更高但砸出来的还是旧版。
    #
    # 只在设备上确实装了这个应用时才做这个判断 —— 那种情况下砸出来的
    # 内容必然等于设备当前版本。设备没装要走「下载安装包」那条路时,
    # 安装包拿到的版本我们事先并不知道,跳过判断直接走完整流程。
    if already_installed:
        device_version = await asyncio.to_thread(device.installed_version, app.bundle_id)
        _reject_if_already_dumped(app, request, device_version)

    task = _create_task(app)
    tasks.registry.submit(
        task,
        lambda current: _run_decrypted_download(
            current,
            app=app,
            country=request.country,
            platform=request.platform,
            already_installed=already_installed,
        ),
    )

    return task


def _create_task(app: AppSummary) -> Task:
    return tasks.registry.create(
        kind="decrypted",
        app_id=app.app_id,
        bundle_id=app.bundle_id,
        name=app.name,
    )


def _offline_model(app: AppSummary, country: str) -> tuple[accounts.Account, Path] | None:
    """
    这个请求能不能走离线解密;能则返回(账号, 模型文件)。

    FAIRPLAY_MODE 的作用:
      * device                —— 直接返回 None,走设备砸壳;
      * offline / unfairplay  —— 只允许离线;没有模型就**直接拒绝**(见下);
      * auto                  —— 有模型就用,没有就退回设备。

    offline 模式为什么要拒绝而不是退回设备:用户显式要求离线,
    默默退回设备会让他以为离线真的在生效。
    """
    if FAIRPLAY_MODE == "device":
        return None

    account = accounts.pick_for_offline(country)
    if account is None:
        if FAIRPLAY_MODE in ("offline", "unfairplay"):
            raise DecryptRejected(
                f"FAIRPLAY_MODE={FAIRPLAY_MODE},但 {country} 区没有可用的离线模型。\n"
                + _offline_hint(country)
            )
        return None

    model = accounts.model_path(account)
    if model is None:  # pick_for_offline 已保证,防御性处理
        return None

    # 内容检查:坏文件当「没模型」处理 —— 退回设备仍能砸壳,
    # 但离线模式下必须报错。
    try:
        unfairplay.read_model(model)
        unfairplay.binary()
    except unfairplay.UnfairplayError as exc:
        if FAIRPLAY_MODE in ("offline", "unfairplay"):
            raise DecryptRejected(str(exc)) from exc
        logger.warning("账号 %s 的模型不可用,退回设备砸壳: %s", account.id, exc)
        return None

    logger.info("账号 %s 有离线模型,用 %s 区商店的包离线解密 %s", account.id, country, app.name)
    return account, model


def _offline_hint(country: str) -> str:
    """「现在没有离线模型」时给用户看的具体下一步。"""
    configured = [item for item in accounts.load() if item.region == country.upper()]

    if configured:
        account = configured[0]
        return (
            f"{country} 区账号 {account.id} 还没有离线模型。一条命令搞定\n"
            "(需要一台登录了 Mac App Store 的 Mac,见 README「九、离线解密」):\n"
            f"  python3 manage_unfairplay.py collect {account.id} "
            "--app <Mac上装的iOS应用> --ipa <加密IPA>\n"
            f"  (模型默认落到 {account.model})"
        )

    return (
        "想不用设备砸壳,需要先给对应区域的 Apple ID 准备一份离线模型。\n"
        "见 README「九、离线解密」:\n"
        "  python3 manage_unfairplay.py collect <账号 id> --app <App> --ipa <加密IPA>"
    )


def _reject_if_already_dumped(
    app: AppSummary,
    request: EncryptedDownloadRequest,
    version: str | None,
) -> None:
    """
    这个版本已经砸过了吗?

    只对「知道的版本」做判断:走设备路径且设备上没装这个应用时,拿到的安装包
    版本事先并不知道(商店版号可能更高),判断会误伤,所以传 None 跳过。
    """
    if not version:
        return

    existing = repository.find_one(app.bundle_id, request.country, request.platform)
    if not _already_dumped(existing, version):
        return

    raise DecryptRejected(
        f"{app.name} v{version} 已经砸过了,文件库里有它的解密包。\n"
        "这个版本没变,重复砸壳结果一样。\n"
        "如果确实需要重砸(比如怀疑之前的包不完整),先删除文件库里的条目再试。"
    )


def _already_dumped(
    existing,
    device_version: str,
) -> bool:
    """
    库里是否已有「设备上当前这个版本」的解密包。

    两个条件都要满足:记录里有解密包路径、且磁盘上文件真的存在。
    只看记录不看磁盘的话,文件被手动删掉后会误判成「已经砸过」——
    用户点了解密却什么也没发生,比重新砸一次更糟。
    """
    if existing is None or not existing["decrypted_path"]:
        return False

    if existing["version"] != device_version:
        return False

    path = storage.decrypted_path(existing["decrypted_path"])
    if not path.exists():
        # 记录在但文件没了。不在这里删记录 —— 让砸壳流程正常跑一遍并覆盖。
        logger.warning("记录显示已砸过 %s,但文件不存在: %s", device_version, path.name)
        return False

    return True


async def _lookup_app(request: EncryptedDownloadRequest) -> AppSummary:
    """取应用元数据。解密的元数据通常已在库里,查不到才去问 iTunes。"""
    app = await itunes.lookup_by_app_id(request.app_id, request.country, request.platform)
    if app is None:
        raise DecryptRejected(
            f"在 {request.country} 商店找不到 App ID {request.app_id}。"
        )

    return app


def _run_decrypted_download(
    task: Task,
    *,
    app: AppSummary,
    country: str,
    platform: Platform,
    already_installed: bool,
) -> None:
    """后台线程正文。异常由 TaskRegistry 捕获并写入任务失败状态。"""
    # --- 1. 确保设备上有这个应用 ---
    if not already_installed:
        task.started(
            TaskStage.INSTALLING,
            f"设备上没有 {app.name},正在取回安装包(这一步可能要几分钟)",
        )
        encrypted_path = _download_encrypted(
            task, app, country, platform, purpose="install"
        )

        device.install(
            encrypted_path,
            on_stage=lambda stage, detail: task.note(f"{stage}: {detail}"),
        )
        task.note("安装完成")

    # --- 2. 砸壳 ---
    task.started(TaskStage.DUMPING, "正在砸壳(从内存读取解密内容)")

    # 砸壳脚本产出到临时位置,成功后再归档 —— 避免半个文件进了文件库
    staging = storage.staging_path(f"{task.task_id}-decrypted")

    def on_stage(stage: str, detail: str) -> None:
        task.note(f"{stage}: {detail}")

    result = device.dump(app.bundle_id, output=staging, on_stage=on_stage)

    # 用**设备上的版本**而不是商店版本命名:用户可能还没升级,
    # 商店里是 40.7.0 而设备上装的是 40.6.0,砸出来的内容对应后者。
    # 用商店版本命名会让文件名与内容不符,以后追溯版本时对不上。
    #
    # 拿不到设备版本时(脚本输出解析失败)才退回商店版本。
    if result.version and app.version and result.version != app.version:
        logger.info(
            "设备版本 %s 与商店版本 %s 不一致,按设备版本命名",
            result.version, app.version,
        )

    _archive_decrypted(
        task,
        app=app,
        country=country,
        platform=platform,
        staging=staging,
        version=result.version or app.version,
    )


def _run_offline_download(
    task: Task,
    *,
    app: AppSummary,
    country: str,
    platform: Platform,
    model: Path,
) -> None:
    """
    后台线程正文:离线解密。全程不碰设备。

    与设备砸壳的区别不只是快:它不需要应用能运行起来,所以那些要求更高
    iOS 版本、或者只有 iPad/Catalyst 版本的包也能砸。
    """
    # --- 1. 下载加密包 ---
    #
    # 离线解密必须从商店拿**加密包**:解密的输入就是 SINF/SUPF 加上
    # 那一段密文。这里不做「设备上有没有」的检查 —— 这条路本来就没设备。
    task.started(
        TaskStage.DOWNLOADING,
        f"正在从 App Store 取回 {app.name} 的加密包",
    )
    encrypted = _download_encrypted(task, app, country, platform, purpose="decrypt")

    # --- 2. 离线解密 ---
    staging = storage.staging_path(f"{task.task_id}-decrypted")
    task.started(
        TaskStage.DECRYPTING,
        "正在离线解密(纯本地计算,不需要设备)",
    )

    try:
        summary = _decrypt_with(task, encrypted, staging, model)
    finally:
        # 加密包只是解密的输入,产出已经拿到了。留在 staging 里等下次启动
        # 清理也行,但这个包动辄几百 MB,当场删更干净。
        storage.remove(encrypted)

    logger.info("离线解密完成: %s", summary)

    # --- 3. 归档 ---
    #
    # 版本用商店的:离线解密的输入就是商店刚下发的包,两者必然一致。
    _archive_decrypted(
        task,
        app=app,
        country=country,
        platform=platform,
        staging=staging,
        version=app.version,
        detail=summary,
    )


def _decrypt_with(task: Task, encrypted: Path, staging: Path, model: Path) -> str:
    """调 UnfairPlay 解密,返回一句可展示的摘要。"""
    try:
        result = unfairplay.decrypt_ipa(
            encrypted,
            staging,
            model,
            on_stage=lambda text: task.note(text),
        )
    except unfairplay.ModelMismatch as exc:
        raise RuntimeError(_model_mismatch_hint(exc)) from exc
    except unfairplay.UnfairplayError as exc:
        raise RuntimeError(f"离线解密失败:{exc}") from exc

    return result.summary()


def _model_mismatch_hint(exc: Exception) -> str:
    """模型与包不匹配时的统一提示。"""
    return (
        f"离线解密失败:{exc}\n"
        "模型是**账号级**的:必须用「下载这个包所用的 Apple ID」在那台 Mac 上收的模型。\n"
        "确认无误的话,可以暂时把 FAIRPLAY_MODE 设为 device 退回设备砸壳。"
    )


def _archive_decrypted(
    task: Task,
    *,
    app: AppSummary,
    country: str,
    platform: Platform,
    staging: Path,
    version: str | None,
    detail: str | None = None,
) -> None:
    """把 staging 里已经砸好的包归档入库。两条解密路径共用。"""
    filename = storage.build_filename(
        bundle_id=app.bundle_id,
        version=version,
        country=country,
        platform=platform,
        suffix="_decrypted",
    )
    destination = storage.decrypted_path(filename)

    # 版本变了的话,旧版本的包不再被任何记录引用(库记录只有 decrypted_path
    # 一个字段),留着就是占空间的孤儿。在归档新文件之前清掉。
    _remove_stale_decrypted(task, app.bundle_id, country, platform, keep=filename)

    task.started(TaskStage.ARCHIVING, "正在归档")
    size = storage.archive(staging, destination)

    repository.upsert_decrypted(
        app_id=app.app_id,
        name=app.name,
        bundle_id=app.bundle_id,
        version=version,
        country=country,
        platform=platform,
        icon_url=app.icon_url,
        decrypted_path=destination.name,
        decrypted_size=size,
    )

    suffix = f",{detail}" if detail else ""
    task.succeed(
        library_id=0,  # 解密不新建记录,挂在原有条目上
        message=f"{app.name} 解密完成({size / 1024 / 1024:.1f} MB{suffix})",
    )


def _remove_stale_decrypted(
    task: Task,
    bundle_id: str,
    country: str,
    platform: Platform,
    *,
    keep: str,
) -> None:
    """
    删除同一应用在旧版本下砸出来的包。

    命名规则是 `<bundle_id>_<版本>_<国家>_<平台>_decrypted.ipa`,所以版本一变
    文件名就变,新文件会落到新的路径上。库记录里只有 decrypted_path 一个字段,
    旧的包立刻就没人引用了 —— 留在磁盘上纯占空间。

    只删「同一 bundle_id + 国家 + 平台」下、名字里带 _decrypted 且不等于 keep
    的文件。前缀匹配保证不会误删别的应用。
    """
    prefix = f"{bundle_id}_"
    suffix = f"_{country}_{platform.value}_decrypted.ipa"

    for path in storage.decrypted_path(keep).parent.iterdir():
        if not path.is_file():
            continue
        if path.name == keep:
            continue
        if path.name.startswith(prefix) and path.name.endswith(suffix):
            storage.remove(path)
            logger.info("已清理旧版本的解密包: %s", path.name)
            task.note(f"清理旧版本包 {path.name}")


def _download_encrypted(
    task: Task,
    app: AppSummary,
    country: str,
    platform: Platform,
    *,
    purpose: str,
) -> Path:
    """
    从 App Store 取回这个应用的**加密包**。

    purpose 只用于给 staging 文件命名,区分用途:
      * "install" —— 设备上没装该应用,下它的包用来安装;
      * "decrypt" —— 离线解密的输入。

    这是「下载解密」的内部环节,不是用户可见的独立功能 ——
    界面上只有一个「下载解密」按钮。

    下载到 staging 而不是 storage/encrypted:加密包只是中间产物,
    用户要的是解密包。留在 staging 里,下次启动时会被自动清理。
    """
    # 账号检查:按国家挑对应区域的账号
    configured = accounts.load()
    selected = accounts.pick_for_region(country)

    if configured and selected is None:
        available = ", ".join(
            f"{a.region or '未探测'}({a.id})" for a in configured if a.logged_in
        )
        raise RuntimeError(
            f"需要下载 {app.name} 的安装包,但没有可用于 {country} 地区的账号。"
            f"当前可用: {available or '无'}。"
        )

    account_env = selected.environment() if selected else None

    if ipatool.get_account(account_env) is None:
        raise RuntimeError(
            f"需要下载 {app.name} 的安装包,但 ipatool 未登录。"
            "请在终端执行 ipatool auth login。"
        )

    staging = storage.staging_path(f"{task.task_id}-{purpose}")

    def on_progress(downloaded: int, total: int | None) -> None:
        task.progress(downloaded, total)

    try:
        ipatool.download(
            app_id=app.app_id,
            bundle_id=app.bundle_id,
            platform=platform.value,
            output_path=staging,
            purchase=IPATOOL_AUTO_PURCHASE,
            on_progress=on_progress,
            account_env=account_env,
        )
    except ipatool.IpatoolError as exc:
        raise ipatool.explain(exc, country=country) from exc

    _verify_ipa(staging)
    return staging


def _verify_ipa(path: Path) -> None:
    """
    确认拿到的是安装包而不是错误响应。

    ipatool 的多种失败(登录过期、地区不符)都表现为「退出码 0 但产物不对」,
    装到设备之前必须拦住 —— 否则 installd 会给出更难懂的报错。
    """
    import zipfile

    if not zipfile.is_zipfile(path):
        raise RuntimeError(f"下载到的不是有效 IPA(zip 结构损坏): {path.name}")

    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()

    if not any(name.startswith("Payload/") for name in names):
        raise RuntimeError(
            "下载到的文件缺少 Payload/ 目录,通常意味着 ipatool 拿到的是错误响应。"
            "请检查 ipatool 是否已登录、账号所属商店与所选国家是否一致。"
        )
