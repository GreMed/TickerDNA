"""Phase 15-1：估值与市场对照 — 本地静态演示数据。

本模块提供 Apple 和腾讯两家公司的"模拟一致预期"静态数据，
用于演示如何将 TickerDNA Base 预测与市场一致预期、估值水平进行对照。

重要声明：
- 以下市场与一致预期数据为本地 AI 生成的功能演示静态数据；
- 不是实时行情，不是真实券商一致预期；
- 不构成投资建议；
- 不得标成"实时""最新"或"市场真实一致预期"。

data_nature = "demo_static"
source_label = "TickerDNA 功能演示静态数据"
"""
from __future__ import annotations

import copy
from typing import Any


# 数据性质标记
DEMO_DATA_NATURE = "demo_static"
DEMO_SOURCE_LABEL = "TickerDNA 功能演示静态数据"

# 醒目免责声明（页面顶部展示）
DEMO_DISCLAIMER = (
    "以下市场与一致预期数据为本地 AI 生成的功能演示静态数据，"
    "不是实时行情，不是真实券商一致预期，不构成投资建议。"
)

# 支持的案例 symbol 列表
SUPPORTED_SYMBOLS = ("AAPL", "0700.HK")


# ── 静态演示数据 ──────────────────────────────────────────────

# Apple（AAPL）
# 币种：美元百万元（与 assumptions.currency 一致）
# 参考市值约 3,500,000 百万美元（约 3.5 万亿美元）
# 默认目标 PE：25
_AAPL_DEMO: dict[str, Any] = {
    "symbol": "AAPL",
    "company_name": "Apple Inc.",
    "as_of_date": "2026-07-15",
    "currency": "美元百万元",
    "unit": "百万元",
    "reference_market_cap": 3_500_000,
    "default_target_pe": 25.0,
    "source_label": DEMO_SOURCE_LABEL,
    "data_nature": DEMO_DATA_NATURE,
    # 各预测年度的模拟一致预期（收入和净利润，百万元）
    # 基期 FY2025 收入约 416,161，净利润约 112,010
    # 模拟一致预期逐年增长约 4-6%
    "consensus": {
        2026: {"revenue": 470_000, "net_profit": 120_000},
        2027: {"revenue": 495_000, "net_profit": 125_000},
        2028: {"revenue": 520_000, "net_profit": 130_000},
        2029: {"revenue": 545_000, "net_profit": 136_000},
        2030: {"revenue": 570_000, "net_profit": 142_000},
        2031: {"revenue": 595_000, "net_profit": 149_000},
        2032: {"revenue": 620_000, "net_profit": 156_000},
        2033: {"revenue": 645_000, "net_profit": 163_000},
        2034: {"revenue": 670_000, "net_profit": 170_000},
        2035: {"revenue": 695_000, "net_profit": 177_000},
    },
}

# 腾讯控股（0700.HK）
# 币种：人民币百万元（与 assumptions.currency 一致）
# 参考市值约 3,800,000 百万元人民币（约 3.8 万亿元人民币）
# 默认目标 PE：15
_0700HK_DEMO: dict[str, Any] = {
    "symbol": "0700.HK",
    "company_name": "腾讯控股有限公司",
    "as_of_date": "2026-07-15",
    "currency": "人民币百万元",
    "unit": "百万元",
    "reference_market_cap": 3_800_000,
    "default_target_pe": 15.0,
    "source_label": DEMO_SOURCE_LABEL,
    "data_nature": DEMO_DATA_NATURE,
    # 各预测年度的模拟一致预期（收入和净利润，百万元）
    # 基期 FY2025 收入约 751,766，净利润约 229,801
    # 模拟一致预期逐年增长约 8-12%
    "consensus": {
        2026: {"revenue": 820_000, "net_profit": 245_000},
        2027: {"revenue": 900_000, "net_profit": 268_000},
        2028: {"revenue": 990_000, "net_profit": 294_000},
        2029: {"revenue": 1_080_000, "net_profit": 322_000},
        2030: {"revenue": 1_170_000, "net_profit": 352_000},
        2031: {"revenue": 1_260_000, "net_profit": 384_000},
        2032: {"revenue": 1_350_000, "net_profit": 418_000},
        2033: {"revenue": 1_440_000, "net_profit": 453_000},
        2034: {"revenue": 1_530_000, "net_profit": 490_000},
        2035: {"revenue": 1_620_000, "net_profit": 528_000},
    },
}

# 按 symbol 索引的静态演示数据表
_DEMO_DATA: dict[str, dict[str, Any]] = {
    "AAPL": _AAPL_DEMO,
    "0700.HK": _0700HK_DEMO,
}


def get_demo_valuation_data(symbol: str) -> dict[str, Any] | None:
    """根据股票代码获取静态演示估值数据。

    返回 None 表示该公司尚未配置估值演示数据（诚实空状态）。
    返回深拷贝，避免调用方修改共享静态数据。
    """
    if not symbol:
        return None
    s = symbol.strip().upper()
    # 大小写不敏感匹配
    for key, data in _DEMO_DATA.items():
        if key.upper() == s:
            return copy.deepcopy(data)
    return None


def is_valuation_supported(symbol: str) -> bool:
    """该公司是否提供估值演示数据。"""
    return get_demo_valuation_data(symbol) is not None
