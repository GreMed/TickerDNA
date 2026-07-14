"""多页面工作台共享常量与拆分口径辅助函数。

从原 app.py 提取，保持业务逻辑不变。
"""
from __future__ import annotations

from modeling.company_data import CompanyCandidate

SPLIT_BASIS_OPTIONS = {
    "按产品": "product",
    "按地区": "geography",
    "按行业": "industry",
    "自定义口径": "custom",
}
SPLIT_DIMENSION_LABELS = {
    "product": "按产品",
    "geography": "按地区",
    "industry": "按行业",
    # SEC/HK/A-share reports often call product-line splits "business segments"
    # or "reportable segments". In this MVP, that maps to the user-facing
    # product/business-line basis.
    "business": "按产品",
}
SPLIT_MODE_TO_CHOICE = {
    value: label
    for label, value in SPLIT_BASIS_OPTIONS.items()
}


def default_split_choice_for_company(company: CompanyCandidate) -> str:
    """Pick the most likely disclosed split basis before the heavy research step."""
    symbol = company.symbol.upper()
    if symbol or company.cik:
        return SPLIT_MODE_TO_CHOICE["product"]
    return SPLIT_MODE_TO_CHOICE["product"]


def split_basis_request(choice: str, custom_basis: str = "") -> dict[str, str]:
    mode = SPLIT_BASIS_OPTIONS.get(choice, "product")
    label = custom_basis.strip() if mode == "custom" else choice
    return {
        "mode": mode,
        "label": label or "自定义口径",
    }


def available_split_basis_text(values: list[str] | None) -> str:
    labels = []
    seen = set()
    for value in values or []:
        if not value:
            continue
        label = SPLIT_DIMENSION_LABELS.get(value, value)
        if label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return "、".join(labels) if labels else "未识别到可替代的标准口径"
