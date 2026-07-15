"""Phase 15-1：估值与市场对照 — 计算逻辑。

将 TickerDNA Base 预测与静态演示数据中的"模拟一致预期"进行对照，
计算收入差异、净利润差异、Forward PE、隐含市值等指标。

所有计算均为纯函数，不依赖 Streamlit，便于测试。

核心规则：
- 模型数据必须从当前 assumptions 和预测年数通过 build_forecast() 取得
- 仅比较模型数据与静态演示数据重叠的年份
- 模拟一致预期静态数据不随用户假设变化
- 币种或金额单位不一致时阻止比较并返回错误
- 净利润 ≤ 0 时 PE 和隐含估值显示"不适用"
- 缺失年份显示"—"，不自行外推
- 分母为 0 时不报错
- 目标 PE 必须为有限正数，非法时返回明确错误
- 不得生成负隐含市值或 nan
"""
from __future__ import annotations

import copy
import math
from typing import Any

import pandas as pd

from modeling.engine import build_forecast
from modeling.demo_valuation import (
    DEMO_DATA_NATURE,
    DEMO_SOURCE_LABEL,
    get_demo_valuation_data,
)


# "不适用"标记（净利润 ≤ 0 或分母为 0 时）
NOT_APPLICABLE = "不适用"

# 缺失年份标记
MISSING = "—"


# 估值页会同时接收披露层的 ISO 代码（如 ``USD百万元``）和展示层的
# 中文标签（如 ``美元百万元``）。比较前必须拆开“币种”和“金额单位”，
# 否则同一口径会因为显示名称不同被误判为不兼容。
_KNOWN_AMOUNT_UNITS = (
    "百万元",
    "千万元",
    "十万元",
    "亿元",
    "万元",
    "千元",
    "million",
    "millions",
)

_CURRENCY_ALIASES = {
    "USD": "USD",
    "US$": "USD",
    "$": "USD",
    "美元": "USD",
    "美金": "USD",
    "CNY": "CNY",
    "RMB": "CNY",
    "人民币": "CNY",
    "人民币元": "CNY",
    "HKD": "HKD",
    "HK$": "HKD",
    "港元": "HKD",
    "港币": "HKD",
}

_UNIT_ALIASES = {
    "百万元": "MILLION",
    "million": "MILLION",
    "millions": "MILLION",
    "千万元": "TEN_MILLION",
    "十万元": "HUNDRED_THOUSAND",
    "亿元": "HUNDRED_MILLION",
    "万元": "TEN_THOUSAND",
    "千元": "THOUSAND",
}


def _split_currency_and_embedded_unit(value: Any) -> tuple[str, str]:
    """把复合标签拆成币种文本与金额单位。

    例如 ``USD百万元`` → ``("USD", "百万元")``，
    ``美元百万元`` → ``("美元", "百万元")``。
    """
    raw = str(value or "").strip()
    if not raw:
        return "", ""

    lowered = raw.lower()
    for unit in _KNOWN_AMOUNT_UNITS:
        if lowered.endswith(unit.lower()):
            return raw[: -len(unit)].strip(), unit
    return raw, ""


def _normalize_currency(value: Any) -> str:
    """将 ISO 代码和中英文显示标签归一化为同一币种代码。"""
    currency_text, _ = _split_currency_and_embedded_unit(value)
    compact = currency_text.replace(" ", "").upper()
    if not compact:
        return ""
    return _CURRENCY_ALIASES.get(compact, compact)


def _normalize_unit(value: Any) -> str:
    """将中英文金额单位归一化，同时保留未知单位的严格比较语义。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    return _UNIT_ALIASES.get(raw.lower(), raw.upper())


def _safe_ratio(numerator: float, denominator: float) -> float | str:
    """安全计算比率，输入无效或分母为 0 时返回 NOT_APPLICABLE。"""
    if not _is_valid_number(numerator) or not _is_valid_number(denominator):
        return NOT_APPLICABLE
    numerator_val = float(numerator)
    denominator_val = float(denominator)
    if denominator_val == 0:
        return NOT_APPLICABLE
    return numerator_val / denominator_val - 1


def _safe_pe(market_cap: float, net_profit: float) -> float | str:
    """安全计算 PE，净利润 ≤ 0 时返回 NOT_APPLICABLE。"""
    if not _is_valid_number(net_profit) or float(net_profit) <= 0:
        return NOT_APPLICABLE
    if not _is_valid_number(market_cap) or float(market_cap) <= 0:
        return NOT_APPLICABLE
    return float(market_cap) / float(net_profit)


def _is_valid_number(value: Any) -> bool:
    """检查 value 是否为有限数值。"""
    if value is None:
        return False
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v)


def _currency_compatible(assumptions: dict[str, Any], demo_data: dict[str, Any]) -> bool:
    """检查币种是否一致。币种缺失视为不兼容。"""
    ac = _normalize_currency(assumptions.get("currency"))
    dc = _normalize_currency(demo_data.get("currency"))
    if not ac or not dc:
        return False
    return ac == dc


def _unit_compatible(assumptions: dict[str, Any], demo_data: dict[str, Any]) -> bool:
    """检查金额单位是否一致。

    规则：
    1. 双方都有独立 unit 字段时，直接比较；
    2. 一方没有 unit 字段时，只从 currency 字符串中提取明确的金额单位；
    3. 无法从任一方确认金额单位时，视为不兼容。
    """
    au = (assumptions.get("unit") or "").strip()
    du = (demo_data.get("unit") or "").strip()

    # 分别核验显式 unit 与 currency 中内嵌的单位。如果同一侧同时存在但
    # 互相矛盾（如 currency=USD亿元、unit=百万元），必须阻止比较。
    def _effective_unit(explicit: str, currency: str) -> tuple[str, bool]:
        explicit_unit = _normalize_unit(explicit)
        _, embedded_raw = _split_currency_and_embedded_unit(currency)
        embedded_unit = _normalize_unit(embedded_raw)
        if explicit_unit and embedded_unit and explicit_unit != embedded_unit:
            return "", False
        return explicit_unit or embedded_unit, True

    ac = (assumptions.get("currency") or "").strip()
    dc = (demo_data.get("currency") or "").strip()
    effective_au, assumption_unit_ok = _effective_unit(au, ac)
    effective_du, demo_unit_ok = _effective_unit(du, dc)
    return bool(
        assumption_unit_ok
        and demo_unit_ok
        and effective_au
        and effective_du
        and effective_au == effective_du
    )


def _format_currency_unit(assumptions: dict[str, Any]) -> str:
    """格式化模型币种和单位用于错误提示。"""
    c = assumptions.get("currency") or "（缺失）"
    u = assumptions.get("unit") or "（含于币种字段）"
    return f"币种「{c}」/ 单位「{u}」"


def _format_demo_currency_unit(demo_data: dict[str, Any]) -> str:
    """格式化演示数据币种和单位用于错误提示。"""
    c = demo_data.get("currency") or "（缺失）"
    u = demo_data.get("unit") or "（缺失）"
    return f"币种「{c}」/ 单位「{u}」"


def build_valuation_comparison(
    assumptions: dict[str, Any],
    years: list[int],
    target_pe: float,
) -> dict[str, Any]:
    """构建估值对照数据。

    参数：
        assumptions: 当前假设
        years: 预测年度列表（如 [2026, 2027, 2028, 2029, 2030]）
        target_pe: 用户输入的目标 PE

    返回结构：
        {
            "supported": bool,           # 是否支持该公司
            "currency_ok": bool,         # 币种单位是否一致
            "currency_error": str,       # 币种不一致时的提示
            "target_pe_ok": bool,        # 目标 PE 是否合法
            "target_pe_error": str,      # 目标 PE 非法时的提示
            "demo_data": dict,          # 静态演示数据（深拷贝）
            "forecast_years": list[int], # 预测年度
            "comparison_table": DataFrame,  # 财务预测对照表
            "valuation_table": DataFrame,   # 估值对照表
            "summary": dict,            # 摘要数据（4个卡）
            "observation": str,         # 相对估值观察文本
        }
    """
    symbol = assumptions.get("symbol") or assumptions.get("ticker", "")
    demo_data = get_demo_valuation_data(symbol)

    if demo_data is None:
        return {
            "supported": False,
            "currency_ok": True,
            "currency_error": "",
            "target_pe_ok": True,
            "target_pe_error": "",
            "demo_data": None,
            "forecast_years": years,
            "comparison_table": pd.DataFrame(),
            "valuation_table": pd.DataFrame(),
            "summary": {},
            "observation": "",
        }

    # 只有支持的演示公司才进入参数校验；未知公司始终保持诚实空状态语义。
    pe_error = validate_target_pe(target_pe)
    if pe_error:
        return {
            "supported": True,
            "currency_ok": True,
            "currency_error": "",
            "target_pe_ok": False,
            "target_pe_error": pe_error,
            "demo_data": demo_data,
            "forecast_years": years,
            "comparison_table": pd.DataFrame(),
            "valuation_table": pd.DataFrame(),
            "summary": {},
            "observation": "",
        }

    # 币种单位检查
    cur_ok = _currency_compatible(assumptions, demo_data)
    unit_ok = _unit_compatible(assumptions, demo_data)
    if not cur_ok or not unit_ok:
        model_cu = _format_currency_unit(assumptions)
        demo_cu = _format_demo_currency_unit(demo_data)
        return {
            "supported": True,
            "currency_ok": False,
            "currency_error": (
                f"当前模型{model_cu}，演示数据{demo_cu}，"
                f"币种或单位不一致，已阻止比较。请检查数据口径。"
            ),
            "target_pe_ok": True,
            "target_pe_error": "",
            "demo_data": demo_data,
            "forecast_years": years,
            "comparison_table": pd.DataFrame(),
            "valuation_table": pd.DataFrame(),
            "summary": {},
            "observation": "",
        }

    target_pe_val = float(target_pe)

    # 从当前 assumptions 重新运行 build_forecast 取得模型数据
    forecasts = build_forecast(assumptions, years)
    base = forecasts["Base"]

    reference_market_cap = demo_data["reference_market_cap"]
    consensus_data = demo_data["consensus"]

    # ── 财务预测对照表 ──
    # 每列对应一个预测年度，每行对应一个指标
    # 模型Base收入 / 模拟一致预期收入 / 收入差异
    # 模型Base净利润 / 模拟一致预期净利润 / 净利润差异
    comparison_rows: list[dict[str, Any]] = []

    model_revenues: dict[int, float | None] = {}
    model_profits: dict[int, float | None] = {}
    consensus_revenues: dict[int, float | None] = {}
    consensus_profits: dict[int, float | None] = {}

    for year in years:
        # 从 Base DataFrame 中获取该年度数据
        row = base[base["年度"] == year]
        if len(row) > 0:
            r = row.iloc[0]
            model_revenues[year] = float(r["收入"])
            model_profits[year] = float(r["净利润"])
        else:
            model_revenues[year] = None
            model_profits[year] = None

        # 从静态演示数据中获取一致预期
        cons = consensus_data.get(year)
        if cons:
            consensus_revenues[year] = float(cons["revenue"])
            consensus_profits[year] = float(cons["net_profit"])
        else:
            consensus_revenues[year] = None
            consensus_profits[year] = None

    # 构建对照表行
    comparison_rows.append({
        "指标": "模型 Base 收入",
        **{f"FY{y}E": _fmt_money(model_revenues.get(y)) for y in years},
    })
    comparison_rows.append({
        "指标": "模拟一致预期收入",
        **{f"FY{y}E": _fmt_money(consensus_revenues.get(y)) for y in years},
    })
    comparison_rows.append({
        "指标": "收入差异",
        **{f"FY{y}E": _fmt_ratio(
            _safe_ratio(model_revenues.get(y, 0), consensus_revenues.get(y, 0))
            if model_revenues.get(y) is not None and consensus_revenues.get(y) is not None
            else MISSING
        ) for y in years},
    })
    comparison_rows.append({
        "指标": "模型 Base 净利润",
        **{f"FY{y}E": _fmt_money(model_profits.get(y)) for y in years},
    })
    comparison_rows.append({
        "指标": "模拟一致预期净利润",
        **{f"FY{y}E": _fmt_money(consensus_profits.get(y)) for y in years},
    })
    comparison_rows.append({
        "指标": "净利润差异",
        **{f"FY{y}E": _fmt_ratio(
            _safe_ratio(model_profits.get(y, 0), consensus_profits.get(y, 0))
            if model_profits.get(y) is not None and consensus_profits.get(y) is not None
            else MISSING
        ) for y in years},
    })

    comparison_table = pd.DataFrame(comparison_rows)

    # ── 估值对照表 ──
    # 模型Forward PE / 模拟一致预期Forward PE / 用户目标PE
    # 模型隐含市值 / 隐含市值相对参考市值差异
    valuation_rows: list[dict[str, Any]] = []

    model_forward_pes: dict[int, float | str] = {}
    consensus_forward_pes: dict[int, float | str] = {}
    implied_caps: dict[int, float | str] = {}
    cap_diffs: dict[int, float | str] = {}

    for year in years:
        mp = model_profits.get(year)
        cp = consensus_profits.get(year)

        # 模型 Forward PE = 参考市值 / 模型Base净利润
        model_forward_pes[year] = _safe_pe(reference_market_cap, mp) if mp is not None else MISSING

        # 模拟一致预期 Forward PE = 参考市值 / 模拟一致预期净利润
        consensus_forward_pes[year] = _safe_pe(reference_market_cap, cp) if cp is not None else MISSING

        # 模型隐含市值 = 模型Base净利润 × 用户输入的目标PE
        # 仅当净利润为有限正数时计算，避免负隐含市值或 nan
        if mp is not None and _is_valid_number(mp) and mp > 0:
            implied_caps[year] = mp * target_pe_val
        else:
            implied_caps[year] = NOT_APPLICABLE

        # 隐含市值相对参考市值差异 = 模型隐含市值 / 参考市值 - 1
        if isinstance(implied_caps[year], (int, float)) and reference_market_cap > 0:
            cap_diffs[year] = _safe_ratio(float(implied_caps[year]), reference_market_cap)
        else:
            cap_diffs[year] = NOT_APPLICABLE

    valuation_rows.append({
        "指标": "模型 Forward PE",
        **{f"FY{y}E": _fmt_pe(model_forward_pes.get(y)) for y in years},
    })
    valuation_rows.append({
        "指标": "模拟一致预期 Forward PE",
        **{f"FY{y}E": _fmt_pe(consensus_forward_pes.get(y)) for y in years},
    })
    valuation_rows.append({
        "指标": "用户目标 PE",
        **{f"FY{y}E": f"{target_pe_val:.1f}" for y in years},
    })
    valuation_rows.append({
        "指标": "模型隐含市值",
        **{f"FY{y}E": _fmt_money(implied_caps.get(y)) for y in years},
    })
    valuation_rows.append({
        "指标": "隐含市值相对参考市值差异",
        **{f"FY{y}E": _fmt_ratio(cap_diffs.get(y)) for y in years},
    })

    valuation_table = pd.DataFrame(valuation_rows)

    # ── 摘要数据（4个卡）──
    # 摘要与观察使用首个有模型数据、一致预期和有效估值差异的重叠年度。
    first_year = next(
        (
            year for year in years
            if _is_valid_number(model_profits.get(year))
            and _is_valid_number(consensus_profits.get(year))
            and _is_valid_number(cap_diffs.get(year))
        ),
        years[0] if years else None,
    )
    first_model_pe = model_forward_pes.get(first_year, MISSING)
    first_consensus_pe = consensus_forward_pes.get(first_year, MISSING)
    first_profit_diff = (
        _safe_ratio(model_profits.get(first_year, 0), consensus_profits.get(first_year, 0))
        if model_profits.get(first_year) is not None and consensus_profits.get(first_year) is not None
        else MISSING
    )
    first_implied_cap = implied_caps.get(first_year, MISSING)
    first_cap_diff = cap_diffs.get(first_year, MISSING)

    summary = {
        "reference_market_cap": reference_market_cap,
        "first_year": first_year,
        "first_model_forward_pe": first_model_pe,
        "first_consensus_forward_pe": first_consensus_pe,
        "first_profit_diff": first_profit_diff,
        "first_implied_cap": first_implied_cap,
        "first_cap_diff": first_cap_diff,
        "currency": demo_data["currency"],
    }

    # ── 相对估值观察 ──
    observation = _build_observation(
        first_profit_diff, first_cap_diff, target_pe_val,
    )

    return {
        "supported": True,
        "currency_ok": True,
        "currency_error": "",
        "target_pe_ok": True,
        "target_pe_error": "",
        "demo_data": demo_data,
        "forecast_years": years,
        "comparison_table": comparison_table,
        "valuation_table": valuation_table,
        "summary": summary,
        "observation": observation,
    }


def _build_observation(
    profit_diff: float | str,
    cap_diff: float | str,
    target_pe: float,
) -> str:
    """构建相对估值观察文本。

    两个独立维度：
    1. 业绩差异：模型 Base 净利润相对模拟一致预期的差异；
    2. 估值位置：首个有效预测年度的模型隐含市值相对参考市值的差异。

    估值位置使用：隐含市值差异 = 模型净利润 × 用户目标 PE / 参考市值 - 1

    条件式口径：
    - 业绩差异 ≥ 15%：相对偏高；≤ -15%：相对偏低；其余：接近
    - 估值位置 ≥ 15%：当前参考市值相对模型隐含市值偏低；
      ≤ -15%：当前参考市值相对模型隐含市值偏高；其余：接近

    只允许使用"相对偏低、接近、相对偏高"等条件式表述。
    必须写清"在当前 Base 假设和目标 PE 下"。
    不得使用"买入、卖出、推荐、目标价、上涨空间"等投资建议表达。
    """
    parts = [
        "在当前 Base 假设和目标 PE 下"
        f"（目标 PE {target_pe:.1f} 倍；静态演示数据，不构成投资建议）："
    ]

    # 维度 1：业绩差异（净利润）
    if isinstance(profit_diff, (int, float)):
        if profit_diff >= 0.15:
            parts.append("模型 Base 净利润相对模拟一致预期偏高")
        elif profit_diff <= -0.15:
            parts.append("模型 Base 净利润相对模拟一致预期偏低")
        else:
            parts.append("模型 Base 净利润与模拟一致预期接近")
    else:
        parts.append("净利润差异不适用，无法判断业绩位置")

    # 维度 2：估值位置（隐含市值差异）
    # 差异 = 模型净利润 × 目标 PE / 参考市值 - 1
    # 差异 ≥ 15% → 参考市值相对模型隐含市值偏低（模型隐含市值高于参考市值）
    # 差异 ≤ -15% → 参考市值相对模型隐含市值偏高（模型隐含市值低于参考市值）
    if isinstance(cap_diff, (int, float)):
        if cap_diff >= 0.15:
            parts.append("当前参考市值相对模型隐含市值偏低")
        elif cap_diff <= -0.15:
            parts.append("当前参考市值相对模型隐含市值偏高")
        else:
            parts.append("当前参考市值与模型隐含市值接近")
    else:
        parts.append("隐含市值差异不适用，无法判断估值位置")

    return " ｜ ".join(parts)


# ── 格式化辅助函数 ──


def _fmt_money(value: float | str | None) -> str:
    """格式化金额（千分位）。None 或字符串原样返回。"""
    if value is None:
        return MISSING
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return f"{value:,.0f}"
    return str(value)


def _fmt_ratio(value: float | str | None) -> str:
    """格式化比率（百分比）。None 或字符串原样返回。"""
    if value is None:
        return MISSING
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return f"{value:+.1%}"
    return str(value)


def _fmt_pe(value: float | str | None) -> str:
    """格式化 PE 值。None 或字符串原样返回。"""
    if value is None:
        return MISSING
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return f"{value:.1f}"
    return str(value)


def get_demo_data_for_symbol(symbol: str) -> dict[str, Any] | None:
    """获取指定公司的静态演示数据（便捷封装）。

    返回深拷贝，避免调用方修改共享静态数据。
    """
    data = get_demo_valuation_data(symbol)
    if data is None:
        return None
    return copy.deepcopy(data)


def validate_target_pe(pe: float) -> str | None:
    """验证目标 PE 输入。

    返回 None 表示有效，返回字符串表示错误提示。
    非数字、NaN、无穷大、≤0、超过合理上限均为非法。
    """
    if pe is None:
        return "目标 PE 不能为空"
    try:
        pe_val = float(pe)
    except (TypeError, ValueError):
        return "目标 PE 必须是数字"
    if not math.isfinite(pe_val):
        return "目标 PE 必须为有限数值"
    if pe_val <= 0:
        return "目标 PE 必须为正数"
    if pe_val > 200:
        return "目标 PE 不合理（超过 200），请检查输入"
    return None
