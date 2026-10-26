"""
API 出入参模型(Pydantic)。

这里的模型只描述 HTTP 契约,数据库表结构在 db.py 中定义。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class Platform(str, Enum):
    """
    平台。

    取值与 `ipatool --platform` 完全一致,避免在网络层做二次翻译。
    本阶段只实现 iOS / iPadOS 两种,与产品需求一致。
    """

    IPHONE = "iphone"
    IPAD = "ipad"


class SearchMode(str, Enum):
    """
    搜索方式。

    URL 模式接受 App Store 的分享链接,从中解析 app id 与地区 ——
    链接里通常已经带了地区,所以选「美国」却想找国区应用时,直接粘链接即可。
    """

    NAME = "name"
    BUNDLE_ID = "bundle_id"
    URL = "url"


# --------------------------------------------------------------------------
# 搜索
# --------------------------------------------------------------------------
class AppSummary(BaseModel):
    """
    一个 App Store 应用的展示信息。

    字段来自 iTunes Search API,后端不做二次加工,
    前端需要什么就直接透传什么,避免两边各维护一份映射。
    """

    app_id: int
    bundle_id: str
    name: str
    version: str | None = None
    price: float | None = None
    seller_name: str | None = None
    icon_url: str | None = None
    file_size_bytes: int | None = None
    minimum_os_version: str | None = None
    country: str
    platform: Platform


class SearchResponse(BaseModel):
    query: str
    mode: SearchMode
    # 用户选择的地区。URL 模式且链接自带地区时,这里是**命中的那个地区**,
    # 可能与用户在界面上选的不同 —— 前端据此提示「已按链接改用 XX 区」。
    country: str
    platform: Platform
    # URL 模式下解析出的地区;None 表示链接没带地区,已用用户选的地区搜索。
    detected_country: str | None = None
    count: int
    results: list[AppSummary]


# --------------------------------------------------------------------------
# 下载
# --------------------------------------------------------------------------
class EncryptedDownloadRequest(BaseModel):
    """
    发起砸壳(下载解密 IPA)。

    只接受 app_id + country + platform:名称 / 图标 / 体积等元数据由后端
    用 app_id 反查 iTunes 得到,不信任客户端传来的展示字段。

    country 决定用哪个商店的元数据做展示与体积估算。实际下载来源是
    ipatool 当前登录账号所属商店,两者不一致时下载会由 Apple 侧拒绝,
    错误信息会原样回传给前端(见 README「已知限制」)。
    """

    app_id: int = Field(..., gt=0)
    country: str = Field(..., min_length=2, max_length=2)
    platform: Platform


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class TaskStage(str, Enum):
    """任务当前所处阶段,前端据此显示不同文案。"""

    QUEUED = "queued"  # 已入队,等待开始
    DOWNLOADING = "downloading"  # 正在下载安装包(设备没装该应用时)
    INSTALLING = "installing"  # 正在把包安装到越狱设备(解密流程)
    DUMPING = "dumping"  # 正在从设备内存读取解密内容(解密流程)
    DECRYPTING = "decrypting"  # 正在离线解密(不需要设备,纯算数)
    ARCHIVING = "archiving"  # 正在校验并归档
    FINISHED = "finished"  # 成功结束


class TaskInfo(BaseModel):
    task_id: str
    kind: str
    status: TaskStatus
    stage: TaskStage
    message: str
    app_id: int
    bundle_id: str
    name: str
    percent: float | None = None
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    library_id: int | None = None
    error: str | None = None
    created_at: str
    updated_at: str


# --------------------------------------------------------------------------
# 文件库
# --------------------------------------------------------------------------
class LibraryItem(BaseModel):
    id: int
    app_id: int | None = None
    name: str
    bundle_id: str
    version: str | None = None
    country: str
    platform: Platform
    icon_url: str | None = None
    # 存的是文件名而不是完整路径:目录由 storage 模块决定,
    # 数据库里出现绝对路径只会给将来改存储位置添麻烦。
    encrypted_path: str | None = None
    decrypted_path: str | None = None
    encrypted_size: int | None = None
    decrypted_size: int | None = None
    has_encrypted: bool
    has_decrypted: bool
    created_at: str
    updated_at: str


class LibraryResponse(BaseModel):
    count: int
    encrypted_count: int
    decrypted_count: int
    total_size: int
    items: list[LibraryItem]


# --------------------------------------------------------------------------
# 账号 / 设备
# --------------------------------------------------------------------------
class AccountInfo(BaseModel):
    """
    单个账号的登录状态。只读,不含任何凭据。

    region 来自登录后自动探测的 storeFront,决定这个账号能下载哪个商店的应用。
    """

    id: str | None = None
    label: str | None = None
    logged_in: bool
    name: str | None = None
    email: str | None = None
    region: str | None = None
    error: str | None = None
    # 这个账号是否已备好离线解密模型(fairplay-enrollment.bin)。
    # 与 logged_in 无关:凭证存在就能离线解这个账号下下载的包。
    offline_ready: bool = False


class AccountsOverview(BaseModel):
    """
    账号总览。

    multi_account=false 表示没有配置账号表,此时系统按单账号模式工作 ——
    前端据此决定是显示账号列表还是单行的登录状态。
    """

    multi_account: bool
    accounts: list[AccountInfo]
    # 已登录账号覆盖的区域,用于前端提示「哪些国家可以直接下载」。
    regions: list[str]
    # 已备好凭证的区域。前端据此允许「不用设备也能解密」。
    offline_regions: list[str] = []


@dataclass(frozen=True)
class DumpResult:
    """
    砸壳产出。

    version / build 来自**设备上安装的那个包**的 Info.plist —— 不是 App Store
    商店里的最新版。用户可能还没升级,两者会不一致,而产出的内容对应的是
    设备上那一份。

    独立于 decrypt/ios_dump.py 里的同名类:那个脚本是独立可执行的,
    不在本包内,不能反向依赖。两处字段保持一致即可,由 device.dump() 负责转换。
    """

    path: Path
    version: str | None
    build: str | None
    bundle_id: str


class DeviceStatus(BaseModel):
    """手机端砸壳服务状态(第二阶段)。"""

    configured: bool
    online: bool
    base_url: str | None = None
    detail: str
