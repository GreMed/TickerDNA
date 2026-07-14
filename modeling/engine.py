from __future__ import annotations

import math
from copy import deepcopy
from datetime import date
from typing import Any

import pandas as pd


SCENARIOS = ("Bull", "Base", "Bear")

# Phase 12B-2 收口：以下常量仅用于 segment-level 兼容字段和情景振幅校验，
# 不再用于逐年度 Base 收入增长率 / Base 毛利率的截断。
# 逐年度 Base 值由用户输入，引擎按原值参与计算，仅在无法解析或非有限时拒绝。
GROSS_MARGIN_MIN = 0.0
GROSS_MARGIN_MAX = 1.0
GROWTH_MIN = -0.8
GROWTH_MAX = 2.0
GROWTH_SPREAD_MIN = 0.0
GROWTH_SPREAD_MAX = 1.0
GROSS_MARGIN_SPREAD_MIN = 0.0
GROSS_MARGIN_SPREAD_MAX = 0.5
TAX_RATE_MIN = 0.0
TAX_RATE_MAX = 0.6
OPEX_RATIO_MIN = 0.0
OPEX_RATIO_MAX = 0.9
OTHER_RATIO_MIN = -0.3
OTHER_RATIO_MAX = 0.3
ANNUAL_CHANGE_MIN = -0.2
ANNUAL_CHANGE_MAX = 0.2


def forecast_years(years: int = 5, start_year: int | None = None) -> list[int]:
    start = start_year or date.today().year + 1
    return list(range(start, start + years))


def assumption_forecast_years(
    assumptions: dict[str, Any], years: int = 5
) -> list[int]:
    """基于 assumptions 中的最近披露财年计算预测年度。

    Phase 12B-0：统一预测年度计算源。
    - 最近披露为 FY2025 → 默认首个预测年度为 FY2026E
    - 若 FY2026 已被系统识别为真实披露期，从 FY2027E 开始
    - assumptions 无 fiscal_year 时回退到 date.today().year + 1
    """
    fiscal_year = None
    actual_disclosure_years = None
    if assumptions and isinstance(assumptions, dict):
        fiscal_year = assumptions.get("fiscal_year")
        actual_disclosure_years = assumptions.get("actual_disclosure_years")

    from modeling.flow_contract import compute_forecast_start_year
    start, _ = compute_forecast_start_year(
        fiscal_year=str(fiscal_year) if fiscal_year is not None else None,
        actual_disclosure_years=actual_disclosure_years,
    )
    return list(range(start, start + years))


def validate_assumptions(
    assumptions: dict[str, Any],
    years: list[int] | None = None,
) -> list[str]:
    """检查输入假设是否越界，返回通俗的警告列表。

    每条警告格式：「[指标名] 输入值 X，合法范围 Y–Z，系统已调整为 W」
    """
    warnings: list[str] = []
    raw = assumptions or {}

    def _pct(value: float) -> str:
        return f"{value * 100:.1f}%"

    def _check(label: str, raw_value: object, lo: float, hi: float,
                default: float) -> float | None:
        if raw_value is None:
            return None
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            warnings.append(
                f"[ {label} ] 输入无法解析（{raw_value!r}），"
                f"合法范围 {lo * 100:.1f}%–{hi * 100:.1f}%，"
                f"系统已采用默认值 {default * 100:.1f}%"
            )
            return None
        if value < lo:
            warnings.append(
                f"[ {label} ] 输入 {_pct(value)}，"
                f"合法范围 {lo * 100:.1f}%–{hi * 100:.1f}%，"
                f"系统已调整为 {_pct(lo)}"
            )
        elif value > hi:
            warnings.append(
                f"[ {label} ] 输入 {_pct(value)}，"
                f"合法范围 {lo * 100:.1f}%–{hi * 100:.1f}%，"
                f"系统已调整为 {_pct(hi)}"
            )
        return value

    # Phase 12B-2 收口：逐年度 Base 收入增长率 / Base 毛利率 不做硬截断，
    # 仅对非常规有限值提示超出常见观察范围，系统仍按原值计算。
    # 非有限值（NaN / Inf / 无法解析）不进入预测计算，回退到分部默认值，
    # 提示必须包含实际采用的默认值，不得出现"仍按您的输入值计算"。
    def _check_yearly_free(warns: list[str], label: str,
                           raw_value: object,
                           fallback_value: float) -> None:
        if raw_value is None:
            return
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            warns.append(
                f"[ {label} ] 输入无法解析（{raw_value!r}），"
                f"该输入不是有效的有限数值，系统未采用；"
                f"已回退到默认值 {_pct(fallback_value)}。"
            )
            return
        if not math.isfinite(value):
            warns.append(
                f"[ {label} ] 输入为非有限数值（{raw_value!r}），"
                f"该输入不是有效的有限数值，系统未采用；"
                f"已回退到默认值 {_pct(fallback_value)}。"
            )
            return
        if label.endswith("Base 收入增长率"):
            if value < GROWTH_MIN or value > GROWTH_MAX:
                warns.append(
                    f"[ {label} ] 输入 {_pct(value)}，"
                    f"该输入超出常见观察范围，系统仍按您的输入值计算。"
                )
        elif label.endswith("Base 毛利率"):
            if value < GROSS_MARGIN_MIN or value > GROSS_MARGIN_MAX:
                warns.append(
                    f"[ {label} ] 输入 {_pct(value)}，"
                    f"该输入超出常见观察范围，系统仍按您的输入值计算。"
                )

    _check("所得税率", raw.get("tax_rate"), TAX_RATE_MIN, TAX_RATE_MAX, 0.25)
    _check("收入增长率情景振幅", raw.get("growth_scenario_spread"),
           GROWTH_SPREAD_MIN, GROWTH_SPREAD_MAX, 0.05)
    _check("毛利率情景振幅", raw.get("gross_margin_scenario_spread"),
           GROSS_MARGIN_SPREAD_MIN, GROSS_MARGIN_SPREAD_MAX, 0.03)
    _check("经营费用率（基期）", raw.get("base_opex_ratio"),
           OPEX_RATIO_MIN, OPEX_RATIO_MAX, 0.23)
    _check("其他损益率（基期）", raw.get("base_other_ratio"),
           OTHER_RATIO_MIN, OTHER_RATIO_MAX, 0.0)
    _check("经营费用率年变动", raw.get("opex_ratio_annual_change"),
           ANNUAL_CHANGE_MIN, ANNUAL_CHANGE_MAX, 0.0)
    _check("其他损益率年变动", raw.get("other_ratio_annual_change"),
           ANNUAL_CHANGE_MIN, ANNUAL_CHANGE_MAX, 0.0)

    segments = raw.get("segments", [])
    if not isinstance(segments, list):
        segments = []
    for idx, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        name = str(seg.get("name", f"分部{idx + 1}"))
        base_rev = seg.get("base_revenue", 0)
        try:
            float(base_rev)
        except (TypeError, ValueError):
            warnings.append(
                f"[ {name} · 基期收入 ] 输入无法解析（{base_rev!r}），"
                f"合法范围 ≥ 0，系统已采用 0"
            )
        else:
            if float(base_rev) < 0:
                warnings.append(
                    f"[ {name} · 基期收入 ] 输入 {base_rev}，"
                    f"合法范围 ≥ 0，系统已调整为 0"
                )
        for scenario in SCENARIOS:
            key = scenario.lower()
            _check(f"{name} · {scenario} 收入增长率",
                   seg.get(f"{key}_growth"),
                   GROWTH_MIN, GROWTH_MAX, 0.0)
            _check(f"{name} · {scenario} 毛利率",
                   seg.get(f"{key}_gross_margin"),
                   GROSS_MARGIN_MIN, GROSS_MARGIN_MAX, 0.4)

        yearly = seg.get("yearly_assumptions", {})
        if not isinstance(yearly, dict):
            yearly = {}
        seg_default_growth = float(seg.get("base_growth", 0))
        seg_default_margin = float(seg.get("base_gross_margin", 0.4))
        for year_key, annual in yearly.items():
            if not isinstance(annual, dict):
                continue
            # Phase 12B-2 收口：逐年度 Base 收入增长率 / Base 毛利率
            # 不再使用"合法范围 / 系统已调整为"截断式警告；
            # 非常规有限输入仅提示超出常见观察范围，系统仍按原值计算。
            # 非有限值回退到分部默认值，提示必须包含实际采用的默认值。
            _check_yearly_free(
                warnings, f"{name} · {year_key}年 Base 收入增长率",
                annual.get("base_growth"), seg_default_growth,
            )
            _check_yearly_free(
                warnings, f"{name} · {year_key}年 Base 毛利率",
                annual.get("base_gross_margin"), seg_default_margin,
            )

    profit_yearly = raw.get("yearly_profit_assumptions", {})
    if isinstance(profit_yearly, dict):
        for year_key, annual in profit_yearly.items():
            if not isinstance(annual, dict):
                continue
            _check(f"{year_key}年 经营费用率",
                   annual.get("opex_ratio"),
                   OPEX_RATIO_MIN, OPEX_RATIO_MAX, 0.23)
            _check(f"{year_key}年 其他损益率",
                   annual.get("other_ratio"),
                   OTHER_RATIO_MIN, OTHER_RATIO_MAX, 0.0)

    return warnings


def normalize_assumptions(
    assumptions: dict[str, Any],
    years: list[int] | None = None,
) -> dict[str, Any]:
    result = deepcopy(assumptions)
    result["currency"] = str(result.get("currency", "人民币百万元"))
    result["segments"] = list(result.get("segments", []))[:20]
    result["tax_rate"] = min(
        max(float(result.get("tax_rate", 0.25)), TAX_RATE_MIN), TAX_RATE_MAX
    )
    result["growth_scenario_spread"] = min(
        max(float(result.get("growth_scenario_spread", 0.05)),
            GROWTH_SPREAD_MIN),
        GROWTH_SPREAD_MAX,
    )
    result["gross_margin_scenario_spread"] = min(
        max(float(result.get("gross_margin_scenario_spread", 0.03)),
            GROSS_MARGIN_SPREAD_MIN),
        GROSS_MARGIN_SPREAD_MAX,
    )
    result["base_opex_ratio"] = min(
        max(float(result.get("base_opex_ratio", 0.23)), OPEX_RATIO_MIN),
        OPEX_RATIO_MAX,
    )
    result["base_other_ratio"] = min(
        max(float(result.get("base_other_ratio", 0.0)), OTHER_RATIO_MIN),
        OTHER_RATIO_MAX,
    )
    result["opex_ratio_annual_change"] = min(
        max(float(result.get("opex_ratio_annual_change", 0.0)),
            ANNUAL_CHANGE_MIN),
        ANNUAL_CHANGE_MAX,
    )
    result["other_ratio_annual_change"] = min(
        max(float(result.get("other_ratio_annual_change", 0.0)),
            ANNUAL_CHANGE_MIN),
        ANNUAL_CHANGE_MAX,
    )

    for segment in result["segments"]:
        segment["name"] = str(segment.get("name", "业务"))
        segment["base_revenue"] = max(float(segment.get("base_revenue", 0)), 0)
        for scenario in SCENARIOS:
            key = scenario.lower()
            segment[f"{key}_growth"] = min(
                max(float(segment.get(f"{key}_growth", 0)), GROWTH_MIN),
                GROWTH_MAX,
            )
            segment[f"{key}_gross_margin"] = min(
                max(float(segment.get(f"{key}_gross_margin", 0.4)),
                    GROSS_MARGIN_MIN),
                GROSS_MARGIN_MAX,
            )
        yearly = segment.get("yearly_assumptions", {})
        if not isinstance(yearly, dict):
            yearly = {}
        normalized_yearly: dict[str, dict[str, float]] = {}
        year_values = years or [
            int(value)
            for value in yearly
            if str(value).isdigit()
        ]
        for year in year_values:
            annual = yearly.get(str(year), yearly.get(year, {}))
            if not isinstance(annual, dict):
                annual = {}
            # Phase 12B-2 收口：逐年度 Base 收入增长率 / Base 毛利率 不做硬截断，
            # 仅拒绝无法解析或非有限的输入（NaN / Inf），有限值按原值保留。
            raw_growth = annual.get(
                "base_growth", segment.get("base_growth", 0)
            )
            try:
                base_growth_val = float(raw_growth)
                if not math.isfinite(base_growth_val):
                    raise ValueError("non-finite growth")
            except (TypeError, ValueError):
                base_growth_val = float(segment.get("base_growth", 0))

            raw_margin = annual.get(
                "base_gross_margin",
                segment.get("base_gross_margin", 0.4),
            )
            try:
                base_margin_val = float(raw_margin)
                if not math.isfinite(base_margin_val):
                    raise ValueError("non-finite margin")
            except (TypeError, ValueError):
                base_margin_val = float(segment.get("base_gross_margin", 0.4))

            normalized_yearly[str(year)] = {
                "base_growth": base_growth_val,
                "base_gross_margin": base_margin_val,
            }
        segment["yearly_assumptions"] = normalized_yearly

    raw_profit_yearly = result.get("yearly_profit_assumptions", {})
    if not isinstance(raw_profit_yearly, dict):
        raw_profit_yearly = {}
    profit_year_values = years or [
        int(value) for value in raw_profit_yearly if str(value).isdigit()
    ]
    normalized_profit_yearly: dict[str, dict[str, float]] = {}
    for index, year in enumerate(profit_year_values, start=1):
        annual = raw_profit_yearly.get(
            str(year),
            raw_profit_yearly.get(year, {}),
        )
        if not isinstance(annual, dict):
            annual = {}
        normalized_profit_yearly[str(year)] = {
            "opex_ratio": min(
                max(
                    float(
                        annual.get(
                            "opex_ratio",
                            result["base_opex_ratio"]
                            + result["opex_ratio_annual_change"] * index,
                        )
                    ),
                    OPEX_RATIO_MIN,
                ),
                OPEX_RATIO_MAX,
            ),
            "other_ratio": min(
                max(
                    float(
                        annual.get(
                            "other_ratio",
                            result["base_other_ratio"]
                            + result["other_ratio_annual_change"] * index,
                        )
                    ),
                    OTHER_RATIO_MIN,
                ),
                OTHER_RATIO_MAX,
            ),
        }
    result["yearly_profit_assumptions"] = normalized_profit_yearly

    # Keep legacy keys aligned for older saved sessions and integrations.
    for scenario in SCENARIOS:
        key = scenario.lower()
        result[f"{key}_opex_ratio"] = result["base_opex_ratio"]
        result[f"{key}_other_ratio"] = result["base_other_ratio"]

    if not result["segments"]:
        raise ValueError("至少需要一个收入分部。")
    return result


def segment_year_scenario_assumptions(
    assumptions: dict[str, Any],
    segment: dict[str, Any],
    year: int,
    scenario: str,
) -> tuple[float, float]:
    annual = segment.get("yearly_assumptions", {}).get(str(year), {})
    base_growth = float(
        annual.get("base_growth", segment.get("base_growth", 0))
    )
    base_gross_margin = float(
        annual.get(
            "base_gross_margin",
            segment.get("base_gross_margin", 0.4),
        )
    )
    direction = {"Bull": 1, "Base": 0, "Bear": -1}[scenario]
    growth = base_growth + direction * assumptions["growth_scenario_spread"]
    gross_margin = (
        base_gross_margin
        + direction * assumptions["gross_margin_scenario_spread"]
    )
    # Phase 12B-2 收口：Bull/Bear 由 Base ± 振幅计算，不做旧边界截断，
    # 确保 Bull ≥ Base ≥ Bear（方向不变），不因旧边界导致 Bull < Base 或 Bear > Base。
    return growth, gross_margin


def profit_year_assumptions(
    assumptions: dict[str, Any],
    year: int,
    year_index: int = 0,
) -> tuple[float, float]:
    annual = assumptions.get("yearly_profit_assumptions", {}).get(
        str(year),
        {},
    )
    step = year_index + 1
    opex_ratio = float(
        annual.get(
            "opex_ratio",
            assumptions.get("base_opex_ratio", 0.23)
            + assumptions.get("opex_ratio_annual_change", 0.0) * step,
        )
    )
    other_ratio = float(
        annual.get(
            "other_ratio",
            assumptions.get("base_other_ratio", 0.0)
            + assumptions.get("other_ratio_annual_change", 0.0) * step,
        )
    )
    return (
        min(max(opex_ratio, OPEX_RATIO_MIN), OPEX_RATIO_MAX),
        min(max(other_ratio, OTHER_RATIO_MIN), OTHER_RATIO_MAX),
    )


def build_forecast(
    assumptions: dict[str, Any], years: list[int] | None = None
) -> dict[str, pd.DataFrame]:
    # Phase 12B-2：统一预测年度生成规则，不回退到 date.today().year + 1。
    # 调用方（app.py）始终传入由 assumption_forecast_years() 生成的年度列表。
    # 若 years 为空（如无 assumptions），返回空结果而非用错误年份推导。
    if not years:
        return {scenario: pd.DataFrame() for scenario in SCENARIOS}
    assumptions = normalize_assumptions(assumptions, years)
    outputs: dict[str, pd.DataFrame] = {}

    for scenario in SCENARIOS:
        records: list[dict[str, float | int | str]] = []
        previous = {
            segment["name"]: segment["base_revenue"]
            for segment in assumptions["segments"]
        }

        for year_index, year in enumerate(years):
            row: dict[str, float | int | str] = {"情景": scenario, "年度": year}
            revenue = 0.0
            gross_profit = 0.0

            for segment in assumptions["segments"]:
                name = segment["name"]
                growth, gross_margin = segment_year_scenario_assumptions(
                    assumptions,
                    segment,
                    year,
                    scenario,
                )
                segment_revenue = previous[name] * (1 + growth)
                segment_gross_profit = segment_revenue * gross_margin
                row[f"{name}收入"] = segment_revenue
                row[f"{name}毛利"] = segment_gross_profit
                row[f"{name}增长率"] = growth
                row[f"{name}毛利率"] = gross_margin
                revenue += segment_revenue
                gross_profit += segment_gross_profit
                previous[name] = segment_revenue

            opex_ratio, other_ratio = profit_year_assumptions(
                assumptions,
                year,
                year_index,
            )
            opex = revenue * opex_ratio
            other = revenue * other_ratio
            pretax_profit = gross_profit - opex + other
            tax = max(pretax_profit, 0) * assumptions["tax_rate"]
            net_profit = pretax_profit - tax

            row.update(
                {
                    "收入": revenue,
                    "毛利": gross_profit,
                    "毛利率": gross_profit / revenue if revenue else 0,
                    "经营费用": opex,
                    "经营费用率": opex_ratio,
                    "其他损益": other,
                    "其他损益率": other_ratio,
                    "税前利润": pretax_profit,
                    "所得税": tax,
                    "净利润": net_profit,
                    "净利率": net_profit / revenue if revenue else 0,
                }
            )
            records.append(row)

        outputs[scenario] = pd.DataFrame(records)
    return outputs


def summary_table(forecasts: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for scenario in SCENARIOS:
        frame = forecasts[scenario]
        for _, record in frame.iterrows():
            rows.append(
                {
                    "情景": scenario,
                    "年度": int(record["年度"]),
                    "年度类型": "预测",
                    "收入": record["收入"],
                    "毛利": record["毛利"],
                    "毛利率": record["毛利率"],
                    "净利润": record["净利润"],
                    "净利率": record["净利率"],
                }
            )
    return pd.DataFrame(rows)


def baseline_metrics(assumptions: dict[str, Any]) -> dict[str, float]:
    revenue = assumptions.get("actual_total_revenue")
    if revenue is None:
        revenue = sum(
            float(segment.get("base_revenue", 0))
            for segment in assumptions.get("segments", [])
        )
    revenue = float(revenue or 0)

    gross_profit = assumptions.get("actual_gross_profit")
    gross_margin = assumptions.get("actual_gross_margin")
    if gross_profit is None:
        gross_profit = sum(
            float(segment.get("base_revenue", 0))
            * float(segment.get("base_gross_margin", 0))
            for segment in assumptions.get("segments", [])
        )
    gross_profit = float(gross_profit or 0)
    if gross_margin is None:
        gross_margin = gross_profit / revenue if revenue else 0
    gross_margin = float(gross_margin or 0)

    net_profit = assumptions.get("actual_net_profit")
    net_margin = assumptions.get("actual_net_margin")
    if net_profit is None:
        normalized = normalize_assumptions(assumptions)
        pretax_profit = (
            gross_profit
            - revenue * normalized["base_opex_ratio"]
            + revenue * normalized["base_other_ratio"]
        )
        tax = max(pretax_profit, 0) * normalized["tax_rate"]
        net_profit = pretax_profit - tax
    net_profit = float(net_profit or 0)
    if net_margin is None:
        net_margin = net_profit / revenue if revenue else 0

    return {
        "收入": revenue,
        "毛利": gross_profit,
        "毛利率": gross_margin,
        "净利润": net_profit,
        "净利率": float(net_margin or 0),
    }


def baseline_metric_period_type(assumptions: dict[str, Any], metric: str) -> str:
    data_quality = assumptions.get("data_quality", "")

    if metric == "收入":
        actual_revenue = assumptions.get("actual_total_revenue")
        has_disclosure_revenue = (
            actual_revenue is not None
            or data_quality == "公司披露分部 + 建模假设"
        )
        return "实际" if has_disclosure_revenue else "基期估算"

    if metric == "毛利":
        actual_gross_profit = assumptions.get("actual_gross_profit")
        return "实际" if actual_gross_profit is not None else "基期估算"

    if metric == "毛利率":
        actual_gross_margin = assumptions.get("actual_gross_margin")
        if actual_gross_margin is not None:
            return "实际"
        actual_gross_profit = assumptions.get("actual_gross_profit")
        actual_revenue = assumptions.get("actual_total_revenue")
        if actual_gross_profit is not None and actual_revenue is not None:
            return "实际"
        return "基期估算"

    if metric == "净利润":
        actual_net_profit = assumptions.get("actual_net_profit")
        return "实际" if actual_net_profit is not None else "基期估算"

    if metric == "净利率":
        actual_net_margin = assumptions.get("actual_net_margin")
        if actual_net_margin is not None:
            return "实际"
        actual_net_profit = assumptions.get("actual_net_profit")
        actual_revenue = assumptions.get("actual_total_revenue")
        if actual_net_profit is not None and actual_revenue is not None:
            return "实际"
        return "基期估算"

    return "基期估算"


def scenario_chart_table(
    assumptions: dict[str, Any],
    forecasts: dict[str, pd.DataFrame],
    metric: str,
) -> pd.DataFrame:
    if metric not in {"收入", "毛利", "毛利率", "净利润", "净利率"}:
        raise ValueError(f"不支持的图表指标：{metric}")

    fiscal_year = str(assumptions.get("fiscal_year", "")).strip()
    base_period = f"基期 {fiscal_year}" if fiscal_year else "基期"
    base_value = baseline_metrics(assumptions)[metric]
    base_period_type = baseline_metric_period_type(assumptions, metric)

    rows: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        rows.append(
            {
                "期间": base_period,
                "期间类型": base_period_type,
                "情景": scenario,
                "指标": metric,
                "数值": base_value,
                "期间顺序": 0,
            }
        )
        for order, (_, record) in enumerate(
            forecasts[scenario].iterrows(),
            start=1,
        ):
            rows.append(
                {
                    "期间": str(int(record["年度"])),
                    "期间类型": "预测",
                    "情景": scenario,
                    "指标": metric,
                    "数值": float(record[metric]),
                    "期间顺序": order,
                }
            )
    return pd.DataFrame(rows)
