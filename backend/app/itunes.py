"""
iTunes Search API 客户端(公开接口,无需登录)。

为什么用它而不是 ipatool 的 search:
1. `ipatool search` 绑定了登录账号所属的商店,无法选国家;
   而公开的 iTunes API 支持任意 `country=XX` 参数,正好满足前端「选国家」的需求。
2. 它返回图标 URL、体积(fileSizeBytes)、最低系统要求等 ipatool 不返回的字段,
   这些是「文件库」界面要展示的信息。
3. 它不需要 Apple ID,即使 ipatool 尚未登录,搜索功能依然可用 ——
   用户可以先浏览,再决定去登录下载。

代价:下载时 ipatool 用的仍是登录账号的商店。两者不一致时由 Apple 侧拒绝,
错误会原样回传(见 README「已知限制」)。
"""

from __future__ import annotations

import logging
import re

import httpx

from .config import ITUNES_TIMEOUT_SECONDS
from .models import AppSummary, Platform, SearchMode

logger = logging.getLogger(__name__)

_SEARCH_ENDPOINT = "https://itunes.apple.com/search"
_LOOKUP_ENDPOINT = "https://itunes.apple.com/lookup"

# 平台 -> iTunes `entity` 参数。取值经实测:iPad 应用要用 iPadSoftware,
# 用 software 会混进 iPhone 专属应用。
_ENTITY_BY_PLATFORM = {
    Platform.IPHONE: "software",
    Platform.IPAD: "iPadSoftware",
}


class ITunesError(RuntimeError):
    """iTunes API 调用失败。message 可直接展示给用户。"""


def _parse_app(raw: dict, country: str, platform: Platform) -> AppSummary | None:
    """
    把 iTunes 的一条结果转成 AppSummary。

    缺少 trackId / bundleId / trackName 的记录无法用于下载,直接丢弃 ——
    这两项是后续调用 ipatool 的必需参数,缺了就只是个展示用的死数据。
    """
    app_id = raw.get("trackId")
    bundle_id = raw.get("bundleId")
    name = raw.get("trackName")

    if not app_id or not bundle_id or not name:
        return None

    return AppSummary(
        app_id=int(app_id),
        bundle_id=str(bundle_id),
        name=str(name),
        version=raw.get("version"),
        price=raw.get("price"),
        seller_name=raw.get("sellerName") or raw.get("artistName"),
        icon_url=raw.get("artworkUrl512") or raw.get("artworkUrl100"),
        file_size_bytes=_as_int(raw.get("fileSizeBytes")),
        minimum_os_version=raw.get("minimumOsVersion"),
        country=country,
        platform=platform,
    )


def _as_int(value) -> int | None:
    """iTunes 偶尔把数字字段返回成字符串,统一转成 int。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def search(
    *,
    query: str,
    mode: SearchMode,
    country: str,
    platform: Platform,
    limit: int,
) -> list[AppSummary]:
    """
    搜索应用。

    mode 决定查询方式:
      - name: 走 /search 全文搜索,适合模糊查找;
      - bundle_id: 走 /lookup,精确命中唯一一个应用。
    """
    params = {"country": country, "limit": limit}

    if mode is SearchMode.BUNDLE_ID:
        endpoint = _LOOKUP_ENDPOINT
        params["bundleId"] = query
    else:
        endpoint = _SEARCH_ENDPOINT
        params["term"] = query
        params["entity"] = _ENTITY_BY_PLATFORM.get(platform, "software")

    try:
        async with httpx.AsyncClient(timeout=ITUNES_TIMEOUT_SECONDS) as client:
            response = await client.get(endpoint, params=params)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise ITunesError(f"iTunes API 返回 {exc.response.status_code},请稍后重试") from exc
    except httpx.HTTPError as exc:
        raise ITunesError(f"无法连接 iTunes API: {exc}") from exc
    except ValueError as exc:
        raise ITunesError("iTunes API 返回了无法解析的内容") from exc

    results = payload.get("results")
    if not isinstance(results, list):
        # 响应结构异常时按「无结果」处理会让用户误以为应用不存在,
        # 因此这里明确报错。
        raise ITunesError("iTunes API 响应中缺少 results 字段")

    return [
        parsed
        for raw in results
        if isinstance(raw, dict) and (parsed := _parse_app(raw, country, platform))
    ]


async def lookup_by_app_id(app_id: int, country: str, platform: Platform) -> AppSummary | None:
    """
    用 App Store 数字 ID 反查应用元数据。

    发起下载时后端会调用它,以免信任前端传来的应用名 / 图标 / 体积等字段。
    """
    try:
        async with httpx.AsyncClient(timeout=ITUNES_TIMEOUT_SECONDS) as client:
            response = await client.get(
                _LOOKUP_ENDPOINT,
                params={"id": app_id, "country": country},
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise ITunesError(f"iTunes API 返回 {exc.response.status_code},请稍后重试") from exc
    except httpx.HTTPError as exc:
        raise ITunesError(f"无法连接 iTunes API: {exc}") from exc
    except ValueError as exc:
        raise ITunesError("iTunes API 返回了无法解析的内容") from exc

    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return None

    raw = results[0]
    if not isinstance(raw, dict):
        return None

    return _parse_app(raw, country, platform)


# --------------------------------------------------------------------------
# App Store URL 解析
# --------------------------------------------------------------------------
# App Store 的分享链接有几种形态,都带 app id,大多也带国家码:
#
#   https://apps.apple.com/cn/app/id1142110895
#   https://apps.apple.com/us/app/tiktok/id835599320
#   https://apps.apple.com/cn/app/抖音-记录美好生活/id1142110895
#   https://itunes.apple.com/cn/app/id1142110895?mt=8
#
# 共享链接(不带国家码)形如:
#   https://apps.apple.com/app/id1142110895
#
# 用正则而不是 urlparse:路径里可能有中文和百分号编码,而取 id 与地区码
# 是纯模式匹配的活,拆路径反而更绕。
_APP_ID_PATTERN = re.compile(r"/id(\d+)")
_COUNTRY_PATTERN = re.compile(r"(?:apps|itunes)\.apple\.com/([a-z]{2})(?:/|$)")


class UrlParseError(ValueError):
    """URL 无法解析出 App Store 应用标识。"""


def parse_app_store_url(url: str) -> tuple[int, str | None]:
    """
    从 App Store 链接里取出 (app_id, country_code)。

    country_code 可能为 None —— 共享链接不带地区。这种情况由调用方决定
    用哪个地区去找,**不要在这里猜默认值**:同一个 app_id 在不同地区
    可能是完全不同的应用,猜错比查不到更糟。
    """
    text = url.strip()

    id_match = _APP_ID_PATTERN.search(text)
    if not id_match:
        raise UrlParseError(
            "链接里没有找到应用 ID。请确认是 App Store 的应用页面链接 —— "
            "形如 https://apps.apple.com/cn/app/id1142110895"
        )

    country_match = _COUNTRY_PATTERN.search(text)
    country = country_match.group(1).upper() if country_match else None

    return int(id_match.group(1)), country


async def find_across_storefronts(
    app_id: int,
    *,
    countries: list[str],
    platform: Platform,
) -> tuple[AppSummary, str] | None:
    """
    在给定的几个地区里找这个 app_id,返回 (应用, 命中的地区)。

    用途:用户粘了一个链接,但链接没带地区、或带的地区与本地账号不符时,
    自动找出它在哪个地区能查到。按传入顺序逐个试,命中即止 ——
    顺序应当是「本地有账号的地区优先」。

    全部查不到返回 None,不抛异常:这是「没找到」而不是「出错了」。
    """
    for country in countries:
        try:
            app = await lookup_by_app_id(app_id, country, platform)
        except ITunesError:
            # 单个地区查询失败(网络抖动)不该中断整个探测,继续试下一个。
            continue

        if app is not None:
            return app, country

    return None
