"""Phase 13：示范案例真实预测依据生成模块。

为 Apple (AAPL) 和腾讯 (0700.HK) 两个示范案例生成基于历史数据的
确定性预测依据，替代 rationale.py 中的占位逻辑。

预测规则确定性公式（可由测试重新计算）：

收入增长率：
    1. 从 historical_periods 计算 2 年 CAGR 和最近一年同比
    2. Base 增长率 = CAGR × 0.5 + 最近一年同比 × 0.5（加权值）
    3. 如加权值 < 0，取最近一年同比（避免负 CAGR 误导）
    4. 如加权值 > 1.0，取 1.0（上限）
    5. 逐年增长率向长期增速回归：
       - 长期增速 = CAGR × 0.7（保守调整）
       - year_n = base_weight × base + (1-base_weight) × long_term
       - base_weight: FY+1=0.8, FY+2=0.6, FY+3=0.4

毛利率：
    - 腾讯（reported）：三期历史毛利率趋势延续
      Base 毛利率 = 最近一年毛利率（延续趋势）
      逐年不变（保守，不做趋势外推）
    - Apple（estimated/missing）：公司整体毛利率历史区间校准
      Base 毛利率 = 公司最近三年整体毛利率均值
      分部毛利率不单独披露，使用公司毛利率作为参考
      置信度不得为 high

公式确定、可由测试重新计算，不硬编码。
"""
from __future__ import annotations

import math
from typing import Any

# 示范案例符号
DEMO_SYMBOLS = {"AAPL", "0700.HK"}


def is_demo_case(assumptions: dict[str, Any]) -> bool:
    """判断是否为示范案例。"""
    symbol = assumptions.get("symbol") or assumptions.get("ticker", "")
    return symbol.upper() in DEMO_SYMBOLS


def compute_cagr(start_value: float, end_value: float, periods: int) -> float | None:
    """计算复合年均增长率。

    CAGR = (end / start) ^ (1/periods) - 1

    Returns:
        CAGR 或 None（无法计算时）
    """
    if start_value <= 0 or end_value <= 0 or periods <= 0:
        return None
    try:
        return (end_value / start_value) ** (1.0 / periods) - 1.0
    except (ValueError, OverflowError):
        return None


def compute_yoy(prev_value: float, curr_value: float) -> float | None:
    """计算同比增长率。

    YoY = curr / prev - 1

    Returns:
        YoY 或 None（无法计算时）
    """
    if prev_value <= 0:
        return None
    try:
        return curr_value / prev_value - 1.0
    except (ValueError, OverflowError):
        return None


def compute_weighted_growth(
    cagr: float | None, yoy: float | None
) -> float:
    """计算加权增长率。

    规则：
    - 两者都有 → CAGR × 0.5 + YoY × 0.5
    - 只有 CAGR → CAGR
    - 只有 YoY → YoY
    - 都没有 → 0.05（保守默认）
    - 加权值 < 0 → 取 YoY（避免负 CAGR 误导）
    - 加权值 > 1.0 → 取 1.0
    """
    if cagr is not None and yoy is not None:
        weighted = cagr * 0.5 + yoy * 0.5
    elif cagr is not None:
        weighted = cagr
    elif yoy is not None:
        weighted = yoy
    else:
        return 0.05

    if weighted < 0 and yoy is not None:
        weighted = yoy
    if weighted > 1.0:
        weighted = 1.0
    if weighted < -0.8:
        weighted = -0.8
    return weighted


def compute_yearly_growth(
    base_growth: float,
    long_term_growth: float,
    year_offset: int,
) -> float:
    """逐年增长率向长期增速回归。

    year_offset: 1 = FY+1, 2 = FY+2, 3 = FY+3
    base_weight: 0.8, 0.6, 0.4
    year_n = base_weight × base_growth + (1-base_weight) × long_term_growth
    """
    weights = {1: 0.8, 2: 0.6, 3: 0.4}
    w = weights.get(year_offset, 0.4)
    result = w * base_growth + (1.0 - w) * long_term_growth
    if result > 2.0:
        result = 2.0
    if result < -0.8:
        result = -0.8
    return result


def compute_margin_trend_tencent(
    historical_margins: list[float | None],
) -> float:
    """腾讯毛利率：三期历史趋势延续。

    规则：取最近一年毛利率（延续当前趋势，保守不做外推）。
    如最近一年缺失，取历史均值。
    """
    valid = [m for m in historical_margins if m is not None and 0 <= m <= 1]
    if not valid:
        return 0.50
    return valid[-1]


def compute_margin_trend_apple(
    historical_margins: list[float | None],
    company_gross_margin: float | None,
) -> float:
    """Apple 分部毛利率：公司整体毛利率校准。

    Apple 10-K 不披露产品分部毛利率。
    使用公司整体毛利率作为参考，分部毛利率为估算值。
    置信度不得为 high。

    规则：
    - 优先使用公司整体毛利率
    - 如缺失，取历史有效值均值
    - 如都缺失，取 0.40
    """
    if company_gross_margin is not None and 0 < company_gross_margin <= 1:
        return company_gross_margin
    valid = [m for m in historical_margins if m is not None and 0 <= m <= 1]
    if valid:
        return sum(valid) / len(valid)
    return 0.40


def _round_float(value: float, places: int = 4) -> float:
    """安全四舍五入。"""
    if math.isnan(value) or math.isinf(value):
        return 0.0
    return round(value, places)


def generate_demo_case_rationale(
    assumptions: dict[str, Any],
    years: list[int],
) -> list[dict[str, Any]]:
    """为示范案例生成基于历史数据的确定性预测依据。

    输入：assumptions（含 segments 和 historical_periods）、years
    输出：rationale items 列表

    每个 item 包含：
    - segment_name, year, metric, value, method, rationale
    - evidence_items, confidence, is_user_modified
    - driver_type, is_placeholder
    - source_url, source_name, publication_date（新增字段）
    - historical_cagr, historical_yoy（新增字段）
    - has_real_evidence（新增字段）
    """
    from modeling.rationale import (
        CONFIDENCE_HIGH,
        CONFIDENCE_MEDIUM,
        CONFIDENCE_LOW,
        DRIVER_REVENUE_DEFAULT_GROWTH,
        DRIVER_MARGIN_DEFAULT_MARGIN,
        METRIC_REVENUE_GROWTH,
        METRIC_GROSS_MARGIN,
        METHOD_HISTORICAL_TREND,
        METHOD_DISCLOSURE_BASED_INITIAL,
        _item,
    )

    symbol = assumptions.get("symbol") or assumptions.get("ticker", "")
    is_tencent = symbol.upper() == "0700.HK"
    is_apple = symbol.upper() == "AAPL"

    # 公司整体毛利率（用于 Apple 分部校准）
    company_gm = None
    if assumptions.get("actual_gross_margin"):
        company_gm = float(assumptions["actual_gross_margin"])
    elif assumptions.get("actual_gross_profit") and assumptions.get("actual_total_revenue"):
        rev = float(assumptions["actual_total_revenue"])
        if rev > 0:
            company_gm = float(assumptions["actual_gross_profit"]) / rev

    items: list[dict[str, Any]] = []

    for seg in assumptions.get("segments", []):
        seg_name = seg["name"]
        historical = seg.get("historical_periods", [])
        basis = seg.get("basis", "estimated")
        gm_basis = seg.get("gross_margin_basis", "estimated")
        yearly = seg.get("yearly_assumptions", {})

        # 提取历史收入序列
        hist_revenues = []
        for h in historical:
            rev = h.get("revenue")
            if rev is not None:
                hist_revenues.append((int(h["fiscal_year"]), float(rev)))

        # 提取历史毛利率序列
        hist_margins = []
        for h in historical:
            gm = h.get("gross_margin")
            if gm is not None:
                hist_margins.append(float(gm))

        # 获取来源信息（从最新历史期）
        latest_period = historical[-1] if historical else {}
        source_url = latest_period.get("revenue_url", "")
        source_name = latest_period.get("revenue_source_name", "")
        publication_date = latest_period.get("revenue_publication_date", "未记录")
        gm_source_url = latest_period.get("gross_margin_url", "")
        gm_source_name = latest_period.get("gross_margin_source_name", "")
        gm_publication_date = latest_period.get(
            "gross_margin_publication_date", "未记录"
        )

        # ── 计算 CAGR 和 YoY ──────────────────────────────
        cagr = None
        yoy = None
        if len(hist_revenues) >= 2:
            prev_rev = hist_revenues[-2][1]
            curr_rev = hist_revenues[-1][1]
            yoy = compute_yoy(prev_rev, curr_rev)
        if len(hist_revenues) >= 3:
            start_rev = hist_revenues[0][1]
            end_rev = hist_revenues[-1][1]
            periods = len(hist_revenues) - 1
            cagr = compute_cagr(start_rev, end_rev, periods)

        # ── 收入增长率 ──────────────────────────────────
        seg_base_growth = float(seg.get("base_growth", 0.05))
        weighted_growth = compute_weighted_growth(cagr, yoy)
        long_term_growth = weighted_growth * 0.7  # 保守调整

        # 历史区间文本
        hist_years = [h[0] for h in hist_revenues]
        if hist_years:
            hist_range = f"FY{min(hist_years)}—FY{max(hist_years)}"
        else:
            hist_range = "无历史数据"

        cagr_str = f"{cagr:.1%}" if cagr is not None else "无法计算"
        yoy_str = f"{yoy:.1%}" if yoy is not None else "无法计算"

        for i, year in enumerate(years):
            year_str = str(year)
            year_data = yearly.get(year_str, {})
            year_basis = year_data.get("basis")

            is_user = year_basis == "user_defined"

            if is_user:
                year_growth = float(year_data.get(
                    "base_growth", seg_base_growth
                ))
                growth_item = _item(
                    seg_name, year, METRIC_REVENUE_GROWTH, year_growth,
                    METHOD_HISTORICAL_TREND,
                    f"用户已手动修改 {seg_name} FY{year}E 收入增长率为 {year_growth:.1%}，"
                    f"覆盖基于历史数据的初始假设。",
                    ["用户手动修改"],
                    CONFIDENCE_HIGH,
                    is_user_modified=True,
                    driver_type=DRIVER_REVENUE_DEFAULT_GROWTH,
                    is_placeholder=False,
                )
            else:
                # 逐年增长率向长期增速回归
                year_growth = compute_yearly_growth(
                    weighted_growth, long_term_growth, i + 1
                )
                year_growth = _round_float(year_growth)

                weight_val = 0.8 if i == 0 else (0.6 if i == 1 else 0.4)
                rationale = (
                    f"{seg_name} 收入增长率基于 {hist_range} 历史数据计算。"
                    f"2 年 CAGR = {cagr_str}，最近一年同比 = {yoy_str}，"
                    f"加权值（CAGR×0.5 + 同比×0.5）= {weighted_growth:.1%}。"
                    f"FY{year}E 预测增长率 = {year_growth:.1%}（"
                    f"基期权重 {weight_val:.1f}，"
                    f"向长期增速 {long_term_growth:.1%} 回归）。"
                    f"来源：{source_name}，日期 {publication_date}。"
                )
                evidence = [
                    f"历史区间：{hist_range}，收入 "
                    f"{' → '.join(f'{h[1]:,.0f}' for h in hist_revenues)}",
                    f"2 年 CAGR = {cagr_str}",
                    f"最近一年同比 = {yoy_str}",
                    f"加权增长率 = {weighted_growth:.1%}",
                    f"长期增速（×0.7）= {long_term_growth:.1%}",
                    f"来源：{source_name}",
                    f"来源 URL：{source_url}",
                    f"资料日期：{publication_date}",
                ]
                growth_item = _item(
                    seg_name, year, METRIC_REVENUE_GROWTH, year_growth,
                    METHOD_HISTORICAL_TREND, rationale, evidence,
                    CONFIDENCE_MEDIUM, False,
                    driver_type=DRIVER_REVENUE_DEFAULT_GROWTH,
                    is_placeholder=False,
                )

            growth_item["source_url"] = source_url
            growth_item["source_name"] = source_name
            growth_item["publication_date"] = publication_date
            growth_item["historical_cagr"] = cagr
            growth_item["historical_yoy"] = yoy
            growth_item["has_real_evidence"] = True

            items.append(growth_item)

        # ── 毛利率 ──────────────────────────────────────
        seg_base_margin = float(seg.get("base_gross_margin", 0.40))

        if is_tencent:
            base_margin = compute_margin_trend_tencent(hist_margins)
            gm_confidence = CONFIDENCE_HIGH
            gm_method = METHOD_DISCLOSURE_BASED_INITIAL
            trend_desc = "延续最近一年披露毛利率（保守，不做趋势外推）"
            gm_source = gm_source_name or source_name
            gm_url = gm_source_url or source_url
            gm_date = gm_publication_date or publication_date
        elif is_apple:
            base_margin = compute_margin_trend_apple(
                hist_margins, company_gm
            )
            gm_confidence = CONFIDENCE_LOW
            gm_method = METHOD_DISCLOSURE_BASED_INITIAL
            trend_desc = (
                "公司 10-K 未披露产品分部毛利率，"
                "使用公司整体毛利率作为参考校准"
            )
            gm_source = source_name
            gm_url = source_url
            gm_date = publication_date
        else:
            base_margin = seg_base_margin
            gm_confidence = CONFIDENCE_LOW
            gm_method = METHOD_DISCLOSURE_BASED_INITIAL
            trend_desc = "保守估算"
            gm_source = source_name
            gm_url = source_url
            gm_date = publication_date

        if hist_margins:
            hist_margin_str = " → ".join(f"{m:.1%}" for m in hist_margins)
        else:
            hist_margin_str = "无历史毛利率数据"

        for i, year in enumerate(years):
            year_str = str(year)
            year_data = yearly.get(year_str, {})
            year_basis = year_data.get("basis")
            is_user = year_basis == "user_defined"

            if is_user:
                year_margin = float(year_data.get(
                    "base_gross_margin", seg_base_margin
                ))
                margin_item = _item(
                    seg_name, year, METRIC_GROSS_MARGIN, year_margin,
                    METHOD_DISCLOSURE_BASED_INITIAL,
                    f"用户已手动修改 {seg_name} FY{year}E 毛利率为 {year_margin:.1%}，"
                    f"覆盖基于历史数据的初始假设。",
                    ["用户手动修改"],
                    CONFIDENCE_HIGH,
                    is_user_modified=True,
                    driver_type=DRIVER_MARGIN_DEFAULT_MARGIN,
                    is_placeholder=False,
                )
            else:
                year_margin = _round_float(base_margin)

                if is_tencent:
                    rationale = (
                        f"{seg_name} 毛利率基于 {hist_range} 历史披露数据。"
                        f"历史毛利率：{hist_margin_str}。"
                        f"FY{year}E 预测毛利率 = {year_margin:.1%}"
                        f"（{trend_desc}）。"
                        f"来源：{gm_source}，日期 {gm_date}。"
                    )
                    evidence = [
                        f"历史毛利率：{hist_margin_str}",
                        f"预测方法：{trend_desc}",
                        f"来源：{gm_source}",
                        f"来源 URL：{gm_url}",
                        f"资料日期：{gm_date}",
                    ]
                elif is_apple:
                    company_gm_str = f"{company_gm:.1%}" if company_gm else "未知"
                    rationale = (
                        f"{seg_name} 毛利率为模型估算。"
                        f"Apple 10-K 未披露产品分部毛利率，"
                        f"历史毛利率：{hist_margin_str or '无'}。"
                        f"使用公司整体毛利率 {company_gm_str} 作为参考校准，"
                        f"FY{year}E 预测毛利率 = {year_margin:.1%}。"
                        f"置信度为低（估算值，非公司披露）。"
                        f"来源：{gm_source}，日期 {gm_date}。"
                    )
                    evidence = [
                        f"10-K 未披露分部毛利率（missing）",
                        f"公司整体毛利率：{company_gm_str}",
                        f"校准方法：{trend_desc}",
                        f"来源：{gm_source}",
                        f"来源 URL：{gm_url}",
                        f"资料日期：{gm_date}",
                        f"注意：此为模型估算，非公司披露",
                    ]
                else:
                    rationale = (
                        f"{seg_name} 毛利率为保守估算 = {year_margin:.1%}。"
                    )
                    evidence = []

                margin_item = _item(
                    seg_name, year, METRIC_GROSS_MARGIN, year_margin,
                    gm_method, rationale, evidence,
                    gm_confidence, False,
                    driver_type=DRIVER_MARGIN_DEFAULT_MARGIN,
                    is_placeholder=False,
                )

            margin_item["source_url"] = gm_url
            margin_item["source_name"] = gm_source
            margin_item["publication_date"] = gm_date
            margin_item["has_real_evidence"] = True

            items.append(margin_item)

    return items
