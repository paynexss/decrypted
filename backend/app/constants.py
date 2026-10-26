"""
列表字典:前端下拉框的可选项。

单独成文件的原因:这些值是前后端共享的契约(前端渲染选项、后端做校验),
集中在一处避免两边各写一份、改一处漏一处。
"""

from __future__ import annotations

# 国家/地区代码 -> 中文名。取值来自 Apple 的 storefront 国家码。
# 这里只列常用地区,其余代码仍可通过 /api/search 的 country 参数直接使用。
COUNTRIES: dict[str, str] = {
    "US": "美国",
    "CN": "中国大陆",
    "HK": "中国香港",
    "TW": "中国台湾",
    "JP": "日本",
    "KR": "韩国",
    "SG": "新加坡",
    "GB": "英国",
    "DE": "德国",
    "FR": "法国",
    "CA": "加拿大",
    "AU": "澳大利亚",
    "IN": "印度",
    "RU": "俄罗斯",
}

# 平台 -> 中文名。key 与 ipatool --platform 的取值一致。
PLATFORMS: dict[str, str] = {
    "iphone": "iOS",
    "ipad": "iPadOS",
}
