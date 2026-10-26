"""
/app/api/search —— 搜索 App Store 应用。

后端代理 iTunes API 而不是让前端直连:
1. 避免浏览器跨域问题;
2. 可以在这里做结果归一化(只保留有下载价值的字段)与数量限制;
3. 后续若要换数据源,前端不用改。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from .. import itunes
from ..config import SEARCH_LIMIT_DEFAULT, SEARCH_LIMIT_MAX
from ..constants import COUNTRIES, PLATFORMS
from ..models import Platform, SearchMode, SearchResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["search"])


@router.get("/meta/options")
async def get_options() -> dict:
    """前端下拉框的选项(国家 / 平台)。集中由后端提供,避免两边各维护一份。"""
    return {
        "countries": [{"code": code, "name": name} for code, name in COUNTRIES.items()],
        "platforms": [{"value": value, "name": name} for value, name in PLATFORMS.items()],
    }


@router.get("/search", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1, max_length=4096, description="应用名称 / Bundle ID / App Store 链接"),
    mode: SearchMode = Query(SearchMode.NAME, description="搜索方式"),
    country: str = Query("US", min_length=2, max_length=2, description="国家/地区代码"),
    platform: Platform = Query(Platform.IPHONE, description="平台"),
    limit: int = Query(SEARCH_LIMIT_DEFAULT, ge=1, le=SEARCH_LIMIT_MAX, description="返回条数上限"),
) -> SearchResponse:
    # 国家码统一大写:iTunes API 对此大小写不敏感,但缓存 key 和数据库里要保持一致。
    country = country.upper()

    if mode is SearchMode.URL:
        return await _search_by_url(q, country=country, platform=platform)

    try:
        results = await itunes.search(
            query=q.strip(),
            mode=mode,
            country=country,
            platform=platform,
            limit=limit,
        )
    except itunes.ITunesError as exc:
        # iTunes 不可达属于上游故障,用 502 而不是 500:
        # 前端可以据此提示「搜索服务暂时不可用」,而不是「服务器内部错误」。
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return SearchResponse(
        query=q,
        mode=mode,
        country=country,
        platform=platform,
        count=len(results),
        results=results,
    )


async def _search_by_url(url: str, *, country: str, platform: Platform) -> SearchResponse:
    """
    按 App Store 链接搜索。

    链接里通常带地区(如 /cn/),那个地区是**权威的** —— 用户粘国区链接
    却把界面选在美国时,尊重链接。链接没带地区时才用界面选的地区。

    链接带了地区但那个地区本地没有账号时,不报错 —— 搜索本身不需要账号,
    只是下载会失败,而下载那一步会给出明确提示。
    """
    try:
        app_id, detected = itunes.parse_app_store_url(url)
    except itunes.UrlParseError as exc:
        # 400 而不是 422:链接格式是用户输入,不是请求结构问题
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    target_country = detected or country

    try:
        app = await itunes.lookup_by_app_id(app_id, target_country, platform)
    except itunes.ITunesError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if app is None:
        # 链接自带的地区查不到,但应用可能只在别的地区上架。
        # 用「界面选的国家 + 共享链接不带地区」这两种情况各试一次,
        # 免得用户为了一个链接还要自己猜地区。
        tried = [target_country]
        for fallback in (country, "US", "CN"):
            if fallback not in tried:
                tried.append(fallback)

        found = await itunes.find_across_storefronts(app_id, countries=tried, platform=platform)
        if found is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"App ID {app_id} 在这些地区都查不到: {', '.join(tried)}。"
                    "该应用可能已下架,或只在其他地区上架。"
                ),
            )

        app, target_country = found

    return SearchResponse(
        query=url,
        mode=SearchMode.URL,
        country=target_country,
        platform=platform,
        detected_country=detected,
        count=1,
        results=[app],
    )
