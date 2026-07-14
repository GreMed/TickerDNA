"""假设依据生成模块 — 为每个 Base 假设提供可解释的依据。

数据流：资料/规则 → 预测方法 → rationale → value
而不是直接 value。
"""

from __future__ import annotations

from typing import Any

# ── 预测方法 ──────────────────────────────────────────────────
METHOD_HISTORICAL_TREND = "historical_trend"
METHOD_DERIVED = "derived"
METHOD_DEFAULT = "default"
METHOD_DISCLOSURE_BASED_INITIAL = "disclosure_based_initial"
METHOD_MARKET_SIZE_SHARE = "market_size_share"
METHOD_USER_ARPU = "user_arpu"
METHOD_STORE_COUNT_SALES = "store_count_sales"
METHOD_CAPACITY_VOLUME_PRICE = "capacity_volume_price"

METHOD_LABELS: dict[str, str] = {
    "historical_trend": "历史趋势外推",
    "derived": "按公司合计反推",
    "default": "保守模型初始假设",
    "disclosure_based_initial": "基于披露基期数据的初始假设",
    "market_size_share": "市场规模/份额",
    "user_arpu": "用户数×ARPU",
    "store_count_sales": "门店数×客单价",
    "capacity_volume_price": "量价拆解",
}

# ── 置信度 ────────────────────────────────────────────────────
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"

CONFIDENCE_LABELS: dict[str, str] = {
    "high": "高",
    "medium": "中",
    "low": "低",
}

# ── 指标 ─────────────────────────────────────────────────────
METRIC_REVENUE_GROWTH = "revenue_growth"
METRIC_GROSS_MARGIN = "gross_margin"

METRIC_LABELS: dict[str, str] = {
    "revenue_growth": "收入增长率",
    "gross_margin": "毛利率",
}


# ── 收入驱动因子类型 ──────────────────────────────────────────
DRIVER_REVENUE_VOLUME_PRICE = "volume_price"
DRIVER_REVENUE_MARKET_SIZE_SHARE = "market_size_share"
DRIVER_REVENUE_STORE_COUNT_SALES = "store_count_sales"
DRIVER_REVENUE_USER_ARPU = "user_arpu"
DRIVER_REVENUE_SUBSCRIPTION_CUSTOMERS_ARPU = "subscription_customers_arpu"
DRIVER_REVENUE_DEFAULT_GROWTH = "default_growth"

REVENUE_DRIVER_LABELS: dict[str, str] = {
    "volume_price": "量价拆解",
    "market_size_share": "市场规模/份额",
    "store_count_sales": "门店数×客单价",
    "user_arpu": "用户数×ARPU",
    "subscription_customers_arpu": "订阅客户数×ARPU",
    "default_growth": "直接增长率",
}

# ── 毛利率驱动因子类型 ────────────────────────────────────────
DRIVER_MARGIN_PRODUCT_MIX = "product_mix"
DRIVER_MARGIN_SCALE_EFFECT = "scale_effect"
DRIVER_MARGIN_COST_PASS_THROUGH = "cost_pass_through"
DRIVER_MARGIN_SUPPLY_DEMAND = "supply_demand"
DRIVER_MARGIN_LIFECYCLE = "lifecycle"
DRIVER_MARGIN_DEFAULT_MARGIN = "default_margin"

MARGIN_DRIVER_LABELS: dict[str, str] = {
    "product_mix": "产品结构",
    "scale_effect": "规模效应",
    "cost_pass_through": "成本传导",
    "supply_demand": "行业供需",
    "lifecycle": "生命周期",
    "default_margin": "直接毛利率",
}


def infer_revenue_driver_type(segment: dict[str, Any]) -> str:
    """根据分部名称和描述推断收入驱动因子类型。

    当前为规则推断，未接入真实经营数据。
    """
    text = " ".join(
        str(part)
        for part in [segment.get("name", ""), segment.get("description", "")]
        if part
    ).lower()

    if any(k in text for k in ("订阅", "会员", "saas", "软件")):
        return DRIVER_REVENUE_SUBSCRIPTION_CUSTOMERS_ARPU
    if any(k in text for k in ("门店", "零售", "连锁", "线下", "餐饮", "百货")):
        return DRIVER_REVENUE_STORE_COUNT_SALES
    if any(k in text for k in ("用户", "活跃", "mau", "dau", "互联网", "平台")):
        return DRIVER_REVENUE_USER_ARPU
    if any(k in text for k in ("市场", "份额", "赛道")):
        return DRIVER_REVENUE_MARKET_SIZE_SHARE
    if any(
        k in text
        for k in (
            "制造", "产能", "产量", "销量", "酒业", "白酒",
            "汽车", "能源", "钢铁", "化工", "产品",
        )
    ):
        return DRIVER_REVENUE_VOLUME_PRICE
    return DRIVER_REVENUE_DEFAULT_GROWTH


def infer_margin_driver_type(segment: dict[str, Any]) -> str:
    """根据分部名称和描述推断毛利率驱动因子类型。

    当前为规则推断，未接入真实经营数据。
    """
    text = " ".join(
        str(part)
        for part in [segment.get("name", ""), segment.get("description", "")]
        if part
    ).lower()

    if any(k in text for k in ("产品", "结构", "组合", "mix")):
        return DRIVER_MARGIN_PRODUCT_MIX
    if any(k in text for k in ("规模", "成本效应", "规模效应")):
        return DRIVER_MARGIN_SCALE_EFFECT
    if any(k in text for k in ("原料", "成本传导", "大宗", "原材料")):
        return DRIVER_MARGIN_COST_PASS_THROUGH
    if any(k in text for k in ("供需", "周期", "景气")):
        return DRIVER_MARGIN_SUPPLY_DEMAND
    if any(k in text for k in ("生命周期", "成熟", "衰退")):
        return DRIVER_MARGIN_LIFECYCLE
    return DRIVER_MARGIN_DEFAULT_MARGIN


def build_driver_assumptions(
    segment: dict[str, Any], years: list[int]
) -> list[dict[str, Any]]:
    """为分部构建驱动因子假设骨架。

    当前为 placeholder 结构，未接入真实经营数据。
    每个 driver 包含：
        driver_name, value, unit, source_type,
        evidence, confidence, is_placeholder
    """
    rev_type = infer_revenue_driver_type(segment)
    margin_type = infer_margin_driver_type(segment)
    drivers: list[dict[str, Any]] = []

    for year in years:
        # 收入驱动因子
        drivers.append(
            {
                "metric": METRIC_REVENUE_GROWTH,
                "year": year,
                "driver_type": rev_type,
                "driver_name": REVENUE_DRIVER_LABELS.get(rev_type, rev_type),
                "value": None,
                "unit": "",
                "source_type": "rule_inferred",
                "evidence": "驱动因子依据不足，当前仍使用保守增长率假设。",
                "confidence": CONFIDENCE_LOW,
                "is_placeholder": True,
            }
        )
        # 毛利率驱动因子
        drivers.append(
            {
                "metric": METRIC_GROSS_MARGIN,
                "year": year,
                "driver_type": margin_type,
                "driver_name": MARGIN_DRIVER_LABELS.get(margin_type, margin_type),
                "value": None,
                "unit": "",
                "source_type": "rule_inferred",
                "evidence": "驱动因子依据不足，当前仍使用保守毛利率假设。",
                "confidence": CONFIDENCE_LOW,
                "is_placeholder": True,
            }
        )
    return drivers


def generate_rationale_items(
    assumptions: dict[str, Any], years: list[int]
) -> list[dict[str, Any]]:
    """为每个分部、每个年度的 revenue_growth 和 gross_margin 生成依据。

    返回扁平列表，每个元素包含：
        segment_name, year, metric, value, method, rationale,
        evidence_items, confidence, is_user_modified,
        driver_type, is_placeholder

    Phase 13：Apple (AAPL) 和腾讯 (0700.HK) 示范案例使用
    demo_case_rationale 模块生成基于历史 CAGR 的真实预测依据，
    is_placeholder=False, has_real_evidence=True。
    其他公司继续保持现有诚实占位逻辑。
    """
    # Phase 13：示范案例使用真实历史数据计算预测依据
    from modeling.demo_case_rationale import is_demo_case, generate_demo_case_rationale
    if is_demo_case(assumptions):
        return generate_demo_case_rationale(assumptions, years)

    items: list[dict[str, Any]] = []
    segments = assumptions.get("segments", [])

    for seg in segments:
        seg_name = seg["name"]
        base_growth = seg.get("base_growth", 0.08)
        base_margin = seg.get("base_gross_margin", 0.40)
        basis = seg.get("basis", "estimated")
        gm_basis = seg.get("gross_margin_basis", "estimated")
        evidence_text = seg.get("evidence", "")
        description = seg.get("description", "")
        reported_gm = seg.get("reported_gross_margin")
        reported_profit = seg.get("reported_profit")
        profit_metric_name = seg.get("profit_metric_name", "")
        seg_revenue = seg.get("base_revenue", 0)
        revenue_share = seg.get("revenue_share")
        actual_total = assumptions.get("actual_total_revenue")
        data_quality = assumptions.get("data_quality", "")
        company_name = assumptions.get("company_name", "")
        disclosure_provider = assumptions.get("disclosure_provider", "")

        # 推断驱动因子类型（当前为规则推断，placeholder）
        rev_driver_type = infer_revenue_driver_type(seg)
        margin_driver_type = infer_margin_driver_type(seg)

        yearly = seg.get("yearly_assumptions", {})

        for year in years:
            year_str = str(year)
            year_data = yearly.get(year_str, {})
            year_growth = year_data.get("base_growth", base_growth)
            year_margin = year_data.get("base_gross_margin", base_margin)
            year_basis = year_data.get("basis")

            # 收入增长率依据
            growth_item = _build_growth_rationale(
                seg_name=seg_name,
                year=year,
                value=year_growth,
                basis=basis,
                year_basis=year_basis,
                evidence_text=evidence_text,
                description=description,
                seg_revenue=seg_revenue,
                revenue_share=revenue_share,
                company_name=company_name,
                disclosure_provider=disclosure_provider,
                driver_type=rev_driver_type,
            )
            items.append(growth_item)

            # 毛利率依据
            margin_item = _build_margin_rationale(
                seg_name=seg_name,
                year=year,
                value=year_margin,
                gm_basis=gm_basis,
                year_basis=year_basis,
                evidence_text=evidence_text,
                description=description,
                reported_gm=reported_gm,
                reported_profit=reported_profit,
                profit_metric_name=profit_metric_name,
                company_name=company_name,
                disclosure_provider=disclosure_provider,
                driver_type=margin_driver_type,
            )
            items.append(margin_item)

    return items


# ── 收入增长率依据 ───────────────────────────────────────────


def _build_growth_rationale(
    *,
    seg_name: str,
    year: int,
    value: float,
    basis: str,
    year_basis: str | None,
    evidence_text: str,
    description: str,
    seg_revenue: float,
    revenue_share: float | None,
    company_name: str,
    disclosure_provider: str,
    driver_type: str = "",
) -> dict[str, Any]:
    is_user = year_basis == "user_defined" or basis == "user_defined"

    if is_user:
        return _item(
            seg_name, year, METRIC_REVENUE_GROWTH, value,
            METHOD_DEFAULT,
            f"用户已手动修改 {seg_name} 第 {year} 年收入增长率为 {value:.1%}，覆盖原始假设。",
            ["用户手动修改"],
            CONFIDENCE_HIGH,
            is_user_modified=True,
            driver_type=driver_type,
            is_placeholder=False,
        )

    # 当前所有 driver 均为 placeholder（未接入真实经营数据）
    if basis == "reported":
        method = METHOD_DISCLOSURE_BASED_INITIAL
        share_str = f"，收入占比 {revenue_share:.1%}" if revenue_share else ""
        rationale = (
            f"{seg_name} 基期收入 {seg_revenue:,.0f} 百万元{share_str}，"
            f"来自 {disclosure_provider or '公司披露'}。"
            f"基于披露基期数据生成初始增长率 {value:.1%}，"
            f"尚未做多期历史 CAGR 计算。"
            f"驱动因子依据不足，当前仍使用保守增长率假设。"
        )
        ev = [
            f"基期收入 = {seg_revenue:,.0f} 百万元（公司披露）",
        ]
        if revenue_share is not None:
            ev.append(f"收入占比 = {revenue_share:.1%}")
        if evidence_text:
            ev.append(evidence_text)
        confidence = CONFIDENCE_MEDIUM
    elif basis == "estimated":
        method = METHOD_DEFAULT
        rationale = (
            f"依据不足，采用保守模型初始假设。"
            f"{seg_name} 的增长率为默认值 {value:.1%}，建议用户根据行业情况调整。"
            f"驱动因子依据不足，当前仍使用保守增长率假设。"
        )
        ev: list[str] = []
        confidence = CONFIDENCE_LOW
    else:
        method = METHOD_DEFAULT
        rationale = (
            f"基于 {seg_name} 基期数据生成初始增长率假设 {value:.1%}，"
            f"尚未做多期历史趋势分析。"
            f"驱动因子依据不足，当前仍使用保守增长率假设。"
        )
        ev = [f"基期收入 = {seg_revenue:,.0f} 百万元"]
        confidence = CONFIDENCE_MEDIUM

    return _item(
        seg_name, year, METRIC_REVENUE_GROWTH, value,
        method, rationale, ev, confidence, False,
        driver_type=driver_type,
        is_placeholder=True,
    )


# ── 毛利率依据 ───────────────────────────────────────────────


def _build_margin_rationale(
    *,
    seg_name: str,
    year: int,
    value: float,
    gm_basis: str,
    year_basis: str | None,
    evidence_text: str,
    description: str,
    reported_gm: float | None,
    reported_profit: float | None,
    profit_metric_name: str,
    company_name: str,
    disclosure_provider: str,
    driver_type: str = "",
) -> dict[str, Any]:
    is_user = year_basis == "user_defined" or gm_basis == "user_defined"

    if is_user:
        return _item(
            seg_name, year, METRIC_GROSS_MARGIN, value,
            METHOD_DEFAULT,
            f"用户已手动修改 {seg_name} 第 {year} 年毛利率为 {value:.1%}，覆盖原始假设。",
            ["用户手动修改"],
            CONFIDENCE_HIGH,
            is_user_modified=True,
            driver_type=driver_type,
            is_placeholder=False,
        )

    # 当前所有 driver 均为 placeholder（未接入真实经营数据）
    if gm_basis == "reported":
        method = METHOD_DISCLOSURE_BASED_INITIAL
        rationale = (
            f"沿用 {seg_name} 披露基期毛利率 {value:.1%}，"
            f"来自 {disclosure_provider or '公司官方披露'}。"
            f"驱动因子依据不足，当前仍使用保守毛利率假设。"
        )
        ev: list[str] = []
        if reported_gm is not None:
            ev.append(f"披露毛利率 = {reported_gm:.1%}（公司披露）")
        if reported_profit is not None and profit_metric_name:
            ev.append(f"{profit_metric_name} = {reported_profit:,.0f} 百万元")
        confidence = CONFIDENCE_HIGH
    elif gm_basis == "derived":
        method = METHOD_DERIVED
        rationale = (
            f"按公司合计毛利反推 {seg_name} 毛利率为 {value:.1%}，"
            f"因该分部未单独披露毛利率。"
            f"驱动因子依据不足，当前仍使用保守毛利率假设。"
        )
        ev = ["公司合计毛利 − 已披露分部毛利 = 本分部毛利"]
        confidence = CONFIDENCE_MEDIUM
    elif gm_basis == "estimated":
        method = METHOD_DEFAULT
        rationale = (
            f"依据不足，采用保守模型初始假设。"
            f"{seg_name} 的毛利率为估算值 {value:.1%}，建议用户根据行业情况调整。"
            f"驱动因子依据不足，当前仍使用保守毛利率假设。"
        )
        ev = []
        confidence = CONFIDENCE_LOW
    else:
        method = METHOD_DEFAULT
        rationale = (
            f"沿用 {seg_name} 基期毛利率 {value:.1%} 作为初始假设，"
            f"尚未做多期历史趋势分析。"
            f"驱动因子依据不足，当前仍使用保守毛利率假设。"
        )
        ev = []
        confidence = CONFIDENCE_MEDIUM

    return _item(
        seg_name, year, METRIC_GROSS_MARGIN, value,
        method, rationale, ev, confidence, False,
        driver_type=driver_type,
        is_placeholder=True,
    )


# ── 工具函数 ─────────────────────────────────────────────────


def _item(
    seg_name: str,
    year: int,
    metric: str,
    value: float,
    method: str,
    rationale: str,
    evidence_items: list[str],
    confidence: str,
    is_user_modified: bool,
    driver_type: str = "",
    is_placeholder: bool = True,
) -> dict[str, Any]:
    return {
        "segment_name": seg_name,
        "year": year,
        "metric": metric,
        "value": value,
        "method": method,
        "rationale": rationale,
        "evidence_items": evidence_items,
        "confidence": confidence,
        "is_user_modified": is_user_modified,
        "driver_type": driver_type,
        "is_placeholder": is_placeholder,
    }


def mark_user_modified(
    assumptions: dict[str, Any],
    segment_name: str,
    year: int,
    metric: str,
) -> None:
    """标记某个假设依据为用户已修改。"""
    items = assumptions.get("rationale_items", [])
    for item in items:
        if (
            item["segment_name"] == segment_name
            and item["year"] == year
            and item["metric"] == metric
        ):
            item["is_user_modified"] = True
            item["is_placeholder"] = False
            break


def has_low_confidence(assumptions: dict[str, Any]) -> bool:
    """检查是否存在低置信度假设。"""
    return any(
        item["confidence"] == CONFIDENCE_LOW
        for item in assumptions.get("rationale_items", [])
    )


def sync_rationale_values(assumptions: dict[str, Any]) -> None:
    """更新 rationale_items 中的 value 字段以匹配当前假设值。

    在用户编辑假设后调用，确保导出 Excel 时数值一致。
    """
    items = assumptions.get("rationale_items", [])
    if not items:
        return

    seg_map = {s["name"]: s for s in assumptions.get("segments", [])}

    for item in items:
        seg = seg_map.get(item["segment_name"])
        if not seg:
            continue
        yearly = seg.get("yearly_assumptions", {})
        year_data = yearly.get(str(item["year"]), {})

        if item["metric"] == METRIC_REVENUE_GROWTH:
            item["value"] = year_data.get(
                "base_growth", seg.get("base_growth", item["value"])
            )
        elif item["metric"] == METRIC_GROSS_MARGIN:
            item["value"] = year_data.get(
                "base_gross_margin", seg.get("base_gross_margin", item["value"])
            )


# ── Phase 12B-2 收口：假设依据聚合纯函数 ───────────────────────


def aggregate_metric_rationale(
    metric_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """对同一业务、同一指标的所有年度依据进行统一聚合分析。

    纯函数，不依赖 Streamlit，可由测试直接调用。

    规则：
    1. 指标顶部性质：全部用户定义→"用户定义"；全部资料不足→"资料不足的初始假设"；
       同时包含两种→"混合：用户定义 + 资料不足的初始假设"。
    2. 共同依据：只有某条依据在全部预测年度中完全一致（出现次数=年度数），
       才能展示为"共同依据"。否则为空字符串。
    3. 共同证据：只有全部年度 evidence_items 都共有的证据才进入共同证据。
    4. 年度差异依据：
       - 用户修改但未填写理由→"用户修改；未提供单独修改理由。"
       - 未修改且确实沿用共同依据→"无单独年度差异依据，沿用共同依据。"
       - 没有共同依据时→各年度展示自己的年度依据，不写"沿用共同依据"。
    5. 不得把第一年度、用户修改年度或出现频率最高但未覆盖全部年度的依据标为共同依据。

    Returns:
        dict 包含：
        - metric_nature: str — 指标顶部性质文本
        - metric_nature_tag: str — 性质标签 key（user_defined/placeholder/mixed/initial）
        - common_rationale: str — 共同依据（空字符串表示无）
        - common_evidence: list[str] — 共同证据列表
        - annual_rows: list[dict] — 年度表格行（year/base_value/year_diff/confidence/nature）
        - partial_user_notice: str — 部分用户修改提示（空字符串表示不显示）
    """
    if not metric_items:
        return {
            "metric_nature": "",
            "metric_nature_tag": "initial",
            "common_rationale": "",
            "common_evidence": [],
            "annual_rows": [],
            "partial_user_notice": "",
        }

    from collections import Counter

    sorted_items = sorted(metric_items, key=lambda x: x["year"])
    n = len(sorted_items)

    # 1. 指标顶部性质
    user_modified_flags = [
        it.get("is_user_modified", False) for it in sorted_items
    ]
    placeholder_flags = [
        it.get("is_placeholder", True) for it in sorted_items
    ]
    all_user = all(user_modified_flags) and n > 0
    all_placeholder = all(placeholder_flags) and n > 0
    any_user = any(user_modified_flags)

    if all_user:
        metric_nature = "用户定义"
        metric_nature_tag = "user_defined"
    elif all_placeholder:
        metric_nature = "资料不足的初始假设"
        metric_nature_tag = "placeholder"
    elif any_user:
        metric_nature = "混合：用户定义 + 资料不足的初始假设"
        metric_nature_tag = "mixed"
    else:
        metric_nature = "初始假设"
        metric_nature_tag = "initial"

    # 2. 共同依据：只有全部年度完全一致才作为共同依据
    rationales = [
        it.get("rationale") or "" for it in sorted_items
    ]
    rationale_counter = Counter(rationales)
    common_rationale = ""
    if n > 0:
        most_common_text, most_common_count = rationale_counter.most_common(1)[0]
        if most_common_count == n and most_common_text:
            common_rationale = most_common_text
    # 删除旧的兜底逻辑：不再取第一条作为参考

    # 3. 共同证据：只有全部年度 evidence_items 都共有的证据才进入
    all_evidence_lists = [
        it.get("evidence_items") or [] for it in sorted_items
    ]
    common_evidence: list[str] = []
    if n > 0 and all(all_evidence_lists):
        # 取交集（保持顺序，以第一年的 evidence_items 顺序为准）
        first_evidence = all_evidence_lists[0]
        for ev in first_evidence:
            if ev and all(ev in el for el in all_evidence_lists[1:]):
                if ev not in common_evidence:
                    common_evidence.append(ev)

    # 4. 年度差异依据
    annual_rows: list[dict[str, Any]] = []
    for it in sorted_items:
        year_rationale = it.get("rationale") or ""
        is_user = it.get("is_user_modified", False)
        is_placeholder = it.get("is_placeholder", True)

        if is_user:
            nature = "用户定义"
        elif is_placeholder:
            nature = "资料不足的初始假设"
        else:
            nature = "初始假设"

        if is_user:
            if not year_rationale or year_rationale == common_rationale:
                year_diff = "用户修改；未提供单独修改理由。"
            else:
                year_diff = year_rationale
        else:
            if common_rationale and (
                not year_rationale or year_rationale == common_rationale
            ):
                year_diff = "无单独年度差异依据，沿用共同依据。"
            elif not common_rationale and not year_rationale:
                year_diff = "无单独年度差异依据。"
            elif not common_rationale and year_rationale:
                year_diff = year_rationale
            else:
                year_diff = year_rationale

        annual_rows.append({
            "year": it["year"],
            "base_value": it["value"],
            "year_diff": year_diff,
            "confidence": it["confidence"],
            "nature": nature,
        })

    # 5. 部分用户修改提示
    has_partial_user = any_user and not all_user and n > 1
    if has_partial_user:
        partial_user_notice = (
            "除用户修改年度外，其余年度沿用同一初始假设，"
            "尚无足够资料支持逐年差异。"
        )
    else:
        partial_user_notice = ""

    return {
        "metric_nature": metric_nature,
        "metric_nature_tag": metric_nature_tag,
        "common_rationale": common_rationale,
        "common_evidence": common_evidence,
        "annual_rows": annual_rows,
        "partial_user_notice": partial_user_notice,
    }

