"""
多账号表:哪个区域用哪个 Apple ID。

背景(为什么需要这个模块):
ipatool 上游一台机器只能保存一个账号 —— 钥匙串条目的 service 和 account
两个字段都是源码里的固定常量,第二次 login 会覆盖第一次。

本模块配合一个极小的 ipatool 补丁工作(见 README「多账号」):
  - IPATOOL_KEYCHAIN_SERVICE  覆盖钥匙串命名空间  -> 凭据互不覆盖
  - IPATOOL_COOKIE_JAR        覆盖 cookie 文件路径 -> 会话互不干扰

两个环境变量合起来,就等于「多个账号同时保持登录」。

区域(region)不靠人工填:每个账号登录后用 `auth info` 读到 storeFront,
再按 ipatool 自己的 storefront.go 映射表反查国家码。人工填的 region 只作为
离线环境下的兜底。
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .config import ACCOUNTS_FILE, IPATOOL_BIN, IPATOOL_TIMEOUT_SECONDS, MODELS_DIR

logger = logging.getLogger(__name__)

# storeFront 形如 "143467-2,34",第一段是商店编号。
_STORE_FRONT_PREFIX = re.compile(r"^\s*(\d+)")

# ipatool 的 storefront.go 里 "国家码 -> 商店编号" 的映射。
#
# 这份表是从上游源码里逐行提取的常量,不是运行时探测结果 —— 它对某个 ipatool
# 版本是固定的。之所以内置而不是让用户填:登录后 Region 应当自动出现,
# 否则「两个账号哪个是美国区」这种事要用户自己去查编号。
#
# 若哪天 Apple 改了编号映射,改这一个字典即可。查不到编号时 Region 显示为
# None,不会影响下载(下载用的是账号自己的 storeFront,与这张表无关)。
_STOREFRONT_TO_COUNTRY: dict[str, str] = {
    # 自动生成自上游 pkg/appstore/storefront.go,请勿手工编辑 ——
    # 手抄这张表出错率极高(第一版 134 条里错了 94 条),
    # 需要更新时重新从上游生成即可。
    "143441": "US",
    "143442": "FR",
    "143443": "DE",
    "143444": "GB",
    "143445": "AT",
    "143446": "BE",
    "143447": "FI",
    "143448": "GR",
    "143449": "IE",
    "143450": "IT",
    "143451": "LU",
    "143452": "NL",
    "143453": "PT",
    "143454": "ES",
    "143455": "CA",
    "143456": "SE",
    "143457": "NO",
    "143458": "DK",
    "143459": "CH",
    "143460": "AU",
    "143461": "NZ",
    "143462": "JP",
    "143463": "HK",
    "143464": "SG",
    "143465": "CN",
    "143466": "KR",
    "143467": "IN",
    "143468": "MX",
    "143469": "RU",
    "143470": "TW",
    "143471": "VN",
    "143472": "ZA",
    "143473": "MY",
    "143474": "PH",
    "143475": "TH",
    "143476": "ID",
    "143477": "PK",
    "143478": "PL",
    "143479": "SA",
    "143480": "TR",
    "143481": "AE",
    "143482": "HU",
    "143483": "CL",
    "143484": "NP",
    "143485": "PA",
    "143486": "LK",
    "143487": "RO",
    "143488": "MV",
    "143489": "CZ",
    "143490": "BD",
    "143491": "IL",
    "143492": "UA",
    "143493": "KW",
    "143494": "HR",
    "143495": "CR",
    "143496": "SK",
    "143497": "LB",
    "143498": "QA",
    "143499": "SI",
    "143500": "RS",
    "143501": "CO",
    "143502": "VE",
    "143503": "BR",
    "143504": "GT",
    "143505": "AR",
    "143506": "SV",
    "143507": "PE",
    "143508": "DO",
    "143509": "EC",
    "143510": "HN",
    "143511": "JM",
    "143512": "NI",
    "143513": "PY",
    "143514": "UY",
    "143515": "MO",
    "143516": "EG",
    "143517": "KZ",
    "143518": "EE",
    "143519": "LV",
    "143520": "LT",
    "143521": "MT",
    "143522": "LI",
    "143523": "MD",
    "143524": "AM",
    "143525": "BW",
    "143526": "BG",
    "143527": "CI",
    "143528": "JO",
    "143529": "KE",
    "143530": "MK",
    "143531": "MG",
    "143532": "ML",
    "143533": "MU",
    "143534": "NE",
    "143535": "SN",
    "143536": "TN",
    "143537": "UG",
    "143538": "AI",
    "143539": "BS",
    "143540": "AG",
    "143541": "BB",
    "143542": "BM",
    "143543": "VG",
    "143544": "KY",
    "143545": "DM",
    "143546": "GD",
    "143547": "MS",
    "143548": "KN",
    "143549": "LC",
    "143550": "VC",
    "143551": "TT",
    "143552": "TC",
    "143553": "GY",
    "143554": "SR",
    "143555": "BZ",
    "143556": "BO",
    "143557": "CY",
    "143558": "IS",
    "143559": "BH",
    "143560": "BN",
    "143561": "NG",
    "143562": "OM",
    "143563": "DZ",
    "143564": "AO",
    "143565": "BY",
    "143566": "UZ",
    "143568": "AZ",
    "143571": "YE",
    "143572": "TZ",
    "143573": "GH",
    "143575": "AL",
    "143592": "MN",
    "143615": "GE",
    "143617": "IQ",
}


class AccountsError(RuntimeError):
    """账号表本身有问题(文件缺失 / 格式错误)。message 可直接展示。"""


@dataclass
class Account:
    """一个 Apple ID 的登录态描述。"""

    id: str
    label: str
    email: str
    keychain_service: str
    cookie_jar: Path
    # 离线解密用的 UnfairPlay enrollment 模型(从 Mac 回收)。
    # 默认 <模型目录>/<账号 id>.bin,账号表里用 unfairplay_model 可以指到其它位置。
    #
    # 文件不存在**不是**配置错误:那只是表示这个账号暂时只能走设备砸壳。
    model: Path | None = None
    # 登录后由 refresh_regions() 回填;配置文件里可以手工写死作为兜底。
    region: str | None = None
    # 运行期状态,不来自配置文件。
    logged_in: bool = field(default=False, compare=False)
    name: str | None = field(default=None, compare=False)
    error: str | None = field(default=None, compare=False)

    def environment(self) -> dict[str, str]:
        """
        ipatool 需要的环境变量。

        只有这两个变量参与账号隔离,其余环境原样继承 ——
        不要在这里做 os.environ 的全量拷贝修改,那会让调用方难以推理。
        """
        return {
            "IPATOOL_KEYCHAIN_SERVICE": self.keychain_service,
            "IPATOOL_COOKIE_JAR": str(self.cookie_jar),
        }

    def ensure_cookie_dir(self) -> None:
        """
        确保 cookie 文件的父目录存在。

        ipatool 自己**不会**创建这个目录:它在写 cookie 时才发现目录不存在,
        而错误被包装成「file locked for too long ... failed to create lock file」——
        完全看不出真正原因(见 README 常见问题)。
        这件事必须在这里做,否则用户只能对着一个误导性的报错猜。
        """
        self.cookie_jar.parent.mkdir(parents=True, exist_ok=True)


# 解析结果缓存 + 锁:账号表是只读的,但运行期状态(logged_in/region)会更新,
# 多个 HTTP 请求可能同时触发刷新。
_lock = threading.Lock()
_cache: list[Account] | None = None
_cache_mtime: float | None = None


def load(force: bool = False) -> list[Account]:
    """
    读取账号表。文件未变化时返回缓存,避免每个请求都读盘 + 解析。

    配置文件不存在时返回空列表而不是报错:第一阶段单账号用户不该被
    多账号功能挡住,此时行为与之前完全一致(用 ipatool 的默认命名空间)。
    """
    global _cache, _cache_mtime

    with _lock:
        if not ACCOUNTS_FILE.exists():
            if _cache is None or force:
                logger.info("未找到账号表 %s,按单账号模式运行", ACCOUNTS_FILE)
                _cache = []
                _cache_mtime = None
            return list(_cache)

        mtime = ACCOUNTS_FILE.stat().st_mtime
        if not force and _cache is not None and _cache_mtime == mtime:
            return list(_cache)

        accounts = _parse(ACCOUNTS_FILE)
        _cache = accounts
        _cache_mtime = mtime
        logger.info("已加载 %d 个账号: %s", len(accounts), ", ".join(a.id for a in accounts))
        return list(accounts)


def get(account_id: str) -> Account | None:
    for account in load():
        if account.id == account_id:
            return account
    return None


def save(accounts: list[Account]) -> None:
    """
    把账号表写回磁盘。

    只用于回填 region —— 邮箱 / 密钥命名空间等属于用户手工维护的内容,
    本函数按原样保留(通过重新读取现有文件再合并字段的方式,见下)。
    """
    payload = {
        "accounts": [
            {
                "id": account.id,
                "label": account.label,
                "email": account.email,
                "keychain_service": account.keychain_service,
                "cookie_jar": str(account.cookie_jar),
                "unfairplay_model": str(account.model),
                "region": account.region,
            }
            for account in load()
        ]
    }

    ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    # 让下次 load() 重新读盘,否则会继续命中旧缓存。
    load(force=True)

    # accounts 参数只用于调用方表达意图;真正的数据以缓存为准,
    # 因为它包含了最新的运行期探测结果。
    del accounts


# --------------------------------------------------------------------------
# 运行期探测
# --------------------------------------------------------------------------
def probe(account: Account) -> None:
    """
    探测单个账号的登录状态与商店归属,就地更新运行期字段。

    只读操作(`auth info`),不会修改钥匙串或 cookie。
    """
    if not IPATOOL_BIN:
        account.error = "未配置 ipatool"
        return

    # 先备好 cookie 目录:ipatool 不会自己建,缺目录时的报错极具误导性。
    account.ensure_cookie_dir()

    import os

    env = {**os.environ, **account.environment()}

    try:
        result = subprocess.run(
            [IPATOOL_BIN, "auth", "info", "--non-interactive", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=min(IPATOOL_TIMEOUT_SECONDS, 60),
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        account.error = f"调用 ipatool 失败: {exc}"
        return

    if result.returncode != 0:
        account.logged_in = False
        account.error = _first_error_line(result.stdout, result.stderr) or "该账号尚未登录"
        return

    payload = _first_json_object(result.stdout)
    if payload is None:
        account.error = "ipatool 输出无法解析"
        return

    account.logged_in = bool(payload.get("success"))
    account.name = payload.get("name") or None
    account.email = payload.get("email") or account.email
    account.error = None

    store_front = payload.get("storeFront")
    if store_front:
        detected = region_from_store_front(str(store_front))
        if detected and detected != account.region:
            logger.info("账号 %s 的区域探测为 %s", account.id, detected)
            account.region = detected


def refresh_regions() -> list[Account]:
    """
    探测全部账号,把新探测到的区域写回配置文件。

    启动时调用一次即可 —— 区域是账号的稳定属性,不会频繁变化。
    单个账号探测失败不影响其他账号。
    """
    accounts = load()
    if not accounts:
        return []

    # 先记下探测前的区域。
    #
    # 必须在 probe() **之前**快照:load() 返回的是缓存列表的浅拷贝,里面的
    # Account 就是缓存里的同一批对象,probe() 会就地改掉 region。
    # 探测后再比较「缓存 vs 刚探测到的」永远相等,changed 恒为 False,
    # 区域就写不回配置文件(表现为:界面显示正常,但 accounts.json 里的
    # region 一直是 null)。
    before = {account.id: account.region for account in accounts}

    for account in accounts:
        probe(account)

    if any(before[account.id] != account.region for account in accounts) and ACCOUNTS_FILE.exists():
        _write_regions()

    return accounts


def region_from_store_front(store_front: str) -> str | None:
    """storeFront("143467-2,34")-> 国家码("IN")。查不到返回 None。"""
    match = _STORE_FRONT_PREFIX.match(store_front)
    if not match:
        return None

    return _STOREFRONT_TO_COUNTRY.get(match.group(1))


def pick_for_region(country: str) -> Account | None:
    """
    为某个国家挑一个可用账号;没有匹配的已登录账号时返回 None。

    调用方必须处理 None —— 猜一个账号去下载会失败,而且失败原因
    ("app not found")会让用户以为是应用不存在。宁可明确报「没有该区域的账号」。
    """
    country = country.upper()
    for account in load():
        if account.region == country and account.logged_in:
            return account

    return None


def pick_for_offline(country: str) -> Account | None:
    """
    为某个国家挑一个能离线解密的账号(区域匹配 + 有可用模型)。

    区域必须匹配:模型是**账号级**的,而 IPA 里的 SINF 是按下载它的账号
    加密的 —— 拿美国区模型解中国区下载的包,只会报「解不开」。

    与 pick_for_region 的区别:这里**不要求 logged_in**。离线解密只需要材料
    和一次下载,而下载用的账号是什么 ipatool 的事;拿登录状态当门槛会把
    「页面还没刷过一次账号探测」误判成「不能离线」。
    """
    country = country.upper()
    for account in load():
        if account.region == country and model_path(account) is not None:
            return account

    return None


def offline_regions() -> list[str]:
    """已备好模型的区域列表。前端据此判断哪个国家可以不用设备。"""
    return sorted(
        {
            account.region
            for account in load()
            if account.region and model_path(account) is not None
        }
    )


def model_path(account: Account) -> Path | None:
    """这个账号可用的离线模型;没有或读不到时返回 None。"""
    path = account.model
    if path is None or not path.is_file():
        return None

    return path


# --------------------------------------------------------------------------
# 内部
# --------------------------------------------------------------------------
def _parse(path: Path) -> list[Account]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AccountsError(f"账号表 {path} 无法解析: {exc}") from exc

    raw_accounts = payload.get("accounts")
    if not isinstance(raw_accounts, list):
        raise AccountsError(f"账号表 {path} 缺少 accounts 数组")

    accounts: list[Account] = []
    seen: set[str] = set()

    for index, raw in enumerate(raw_accounts):
        if not isinstance(raw, dict):
            raise AccountsError(f"账号表第 {index + 1} 项不是对象")

        account = _build(raw, index, path)
        if account.id in seen:
            raise AccountsError(f"账号表中 id 重复: {account.id}")
        seen.add(account.id)
        accounts.append(account)

    return accounts


def _build(raw: dict, index: int, path: Path) -> Account:
    """把一条配置转成 Account。字段缺失一律当作配置错误,不猜默认值。"""
    required = ("id", "keychain_service", "cookie_jar")
    missing = [key for key in required if not raw.get(key)]
    if missing:
        raise AccountsError(f"账号表第 {index + 1} 项缺少字段: {', '.join(missing)}")

    cookie_jar = Path(str(raw["cookie_jar"])).expanduser()
    if not cookie_jar.is_absolute():
        # 相对路径按账号表所在目录解析,避免受启动时工作目录影响。
        cookie_jar = (path.parent / cookie_jar).resolve()

    model = _resolve_model(raw, str(raw["id"]), path)

    return Account(
        id=str(raw["id"]),
        label=str(raw.get("label") or raw["id"]),
        email=str(raw.get("email") or ""),
        keychain_service=str(raw["keychain_service"]),
        cookie_jar=cookie_jar,
        model=model,
        region=(str(raw["region"]).upper() if raw.get("region") else None),
    )


def _resolve_model(raw: dict, account_id: str, path: Path) -> Path:
    """
    算出这个账号的离线模型位置。

    默认按约定取 `<模型目录>/<id>.bin` —— 这样安装就是一次文件拷贝,
    不需要改配置文件。想放到别处(比如多个账号共用一份)才写
    `unfairplay_model`。与 cookie_jar 一致,相对路径按账号表目录解析。
    """
    configured = raw.get("unfairplay_model")
    if not configured:
        return MODELS_DIR / f"{account_id}.bin"

    candidate = Path(str(configured)).expanduser()
    if candidate.is_absolute():
        return candidate

    return (path.parent / candidate).resolve()


def _write_regions() -> None:
    """只更新 region 字段,其余内容按原文件顺序重写。"""
    global _cache_mtime

    try:
        payload = json.loads(ACCOUNTS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return

    regions = {account.id: account.region for account in (_cache or [])}
    for raw in payload.get("accounts", []):
        if isinstance(raw, dict) and raw.get("id") in regions:
            raw["region"] = regions[raw["id"]]

    ACCOUNTS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    _cache_mtime = ACCOUNTS_FILE.stat().st_mtime
    logger.info("已把探测到的区域写回 %s", ACCOUNTS_FILE)


def _first_json_object(text: str) -> dict | None:
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload

    return None


def _first_error_line(stdout: str, stderr: str) -> str | None:
    """从 ipatool 的输出里取第一条错误文本。"""
    for stream in (stderr, stdout):
        for line in stream.splitlines():
            line = line.strip()
            if not line:
                continue
            payload = _first_json_object(line)
            if payload and payload.get("error"):
                return str(payload["error"])[:300]
            if not line.startswith("{"):
                return line[:300]

    return None
