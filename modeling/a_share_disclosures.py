from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

import requests

from modeling.company_data import CompanyCandidate


CACHE_DIR = Path(
    os.getenv(
        "FM_DATA_CACHE_DIR",
        str(Path(__file__).resolve().parents[1] / ".cache" / "company_data"),
    )
)
EASTMONEY_BUSINESS_URL = (
    "https://emweb.securities.eastmoney.com/PC_HSF10/"
    "BusinessAnalysis/PageAjax"
)

NAME_FIELDS = (
    "ITEM_NAME",
    "itemName",
    "MAINOP_NAME",
    "mainopName",
    "BUSINESS_NAME",
    "businessName",
)
REVENUE_FIELDS = (
    "MAIN_BUSINESS_INCOME",
    "mainBusinessIncome",
    "OPERATE_INCOME",
    "operateIncome",
    "REVENUE",
    "revenue",
)
RATIO_FIELDS = (
    "MBI_RATIO",
    "mbiRatio",
    "INCOME_RATIO",
    "incomeRatio",
    "REVENUE_RATIO",
    "revenueRatio",
)
GROWTH_FIELDS = (
    "MBI_YOY",
    "mbiYoy",
    "INCOME_YOY",
    "incomeYoy",
    "YOY",
    "yoy",
)
GROSS_MARGIN_FIELDS = (
    "GROSS_RPOFIT_RATIO",
    "grossRpofitRatio",
    "GROSS_PROFIT_RATIO",
    "grossProfitRatio",
    "GROSS_MARGIN",
    "grossMargin",
)
DATE_FIELDS = (
    "REPORT_DATE",
    "reportDate",
    "REPORTDATE",
    "reportdate",
    "END_DATE",
    "endDate",
)
TYPE_FIELDS = (
    "MAINOP_TYPE",
    "mainopType",
    "BUSINESS_TYPE",
    "businessType",
    "TYPE",
    "type",
)
UNIT_FIELDS = ("UNIT", "unit", "MONEY_UNIT", "moneyUnit")

DIMENSION_NAMES = {
    "1": "industry",
    "2": "product",
    "3": "geography",
    "4": "business",
    "行业": "industry",
    "产品": "product",
    "地区": "geography",
    "业务": "business",
    "industry": "industry",
    "product": "product",
    "geography": "geography",
    "business": "business",
}
DIMENSION_PENALTY = {
    "business": 0,
    "product": 0.01,
    "industry": 0.08,
    "geography": 0.15,
}
TOTAL_NAMES = {"合计", "总计", "主营业务合计", "营业收入合计", "total"}


@dataclass(frozen=True)
class StructuredSegments:
    fiscal_year: str
    currency: str
    total_revenue: float
    segments: list[dict[str, Any]]
    dimension: str
    source_url: str
    gross_profit: float | None = None
    gross_margin: float | None = None
    net_profit: float | None = None
    net_margin: float | None = None
    available_dimensions: list[str] = field(default_factory=list)


def _cache_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    return CACHE_DIR / f"a_share_business_{digest}.json"


def _fresh(path: Path, ttl_hours: float) -> bool:
    if not path.exists():
        return False
    modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return datetime.now(timezone.utc) - modified <= timedelta(hours=ttl_hours)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _value(row: dict[str, Any], fields: tuple[str, ...]) -> Any:
    for field in fields:
        if row.get(field) not in (None, "", "--"):
            return row[field]
    lowered = {str(key).lower(): value for key, value in row.items()}
    for field in fields:
        value = lowered.get(field.lower())
        if value not in (None, "", "--"):
            return value
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").replace(",", "").replace("%", "").strip()
    if not text or text in {"-", "--"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        result: list[dict[str, Any]] = []
        for item in value:
            result.extend(_rows(item))
        return result
    if not isinstance(value, dict):
        return []

    has_name = _value(value, NAME_FIELDS) not in (None, "")
    has_revenue = _value(value, REVENUE_FIELDS) not in (None, "")
    result = [value] if has_name and has_revenue else []
    for item in value.values():
        result.extend(_rows(item))
    return result


def _report_year(value: Any) -> str:
    match = re.search(r"(20\d{2})", str(value or ""))
    return match.group(1) if match else ""


def _dimension(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    for key, dimension in DIMENSION_NAMES.items():
        if normalized == key or key in normalized:
            return dimension
    return "business"


def _is_total(value: str) -> bool:
    normalized = re.sub(r"[\s:：]", "", value).lower()
    return normalized in {
        re.sub(r"[\s:：]", "", item).lower() for item in TOTAL_NAMES
    }


def _unit_factor(rows: list[dict[str, Any]], amounts: list[float]) -> float:
    unit_text = " ".join(
        str(_value(row, UNIT_FIELDS) or "").lower() for row in rows[:10]
    )
    if "亿元" in unit_text:
        return 100.0
    if "万元" in unit_text:
        return 0.01
    if "千元" in unit_text:
        return 0.001
    if "百万元" in unit_text or "million" in unit_text:
        return 1.0
    if "元" in unit_text:
        return 0.000001

    # The public F10 endpoint returns monetary amounts in yuan when no explicit
    # unit is included, including for smaller listed companies.
    return 0.000001


def _amount_in_millions(amount: str, unit: str) -> float:
    value = float(amount.replace(",", ""))
    return value * {
        "亿元": 100.0,
        "百万元": 1.0,
        "万元": 0.01,
        "元": 0.000001,
    }[unit]


def parse_business_composition(
    payload: Any,
    preferred_dimension: str | None = None,
) -> StructuredSegments | None:
    records = _rows(payload)
    if not records:
        return None

    full_year_records = [
        row
        for row in records
        if re.search(r"(?:12[-/]31|12月31)", str(_value(row, DATE_FIELDS) or ""))
    ]
    if full_year_records:
        records = full_year_records
    latest_year = max(
        (_report_year(_value(row, DATE_FIELDS)) for row in records),
        default="",
    )
    if latest_year:
        records = [
            row
            for row in records
            if _report_year(_value(row, DATE_FIELDS)) == latest_year
        ]

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in records:
        grouped.setdefault(_dimension(_value(row, TYPE_FIELDS)), []).append(row)

    candidates: list[
        tuple[
            float,
            str,
            float,
            list[tuple[str, float, float | None, float | None]],
        ]
    ] = []
    for dimension, rows in grouped.items():
        parsed: list[
            tuple[
                str,
                float,
                float | None,
                float | None,
                float | None,
            ]
        ] = []
        for row in rows:
            name = re.sub(r"\s+", " ", str(_value(row, NAME_FIELDS) or "")).strip()
            revenue = _number(_value(row, REVENUE_FIELDS))
            ratio = _number(_value(row, RATIO_FIELDS))
            growth = _number(_value(row, GROWTH_FIELDS))
            gross_margin = _number(_value(row, GROSS_MARGIN_FIELDS))
            if not name or revenue is None or revenue <= 0:
                continue
            parsed.append(
                (
                    name,
                    revenue,
                    ratio,
                    growth / 100 if growth is not None and abs(growth) > 1 else growth,
                    (
                        gross_margin / 100
                        if gross_margin is not None and abs(gross_margin) > 1
                        else gross_margin
                    ),
                )
            )
        if len(parsed) < 2:
            continue

        explicit_total = next(
            (revenue for name, revenue, _, _, _ in parsed if _is_total(name)),
            None,
        )
        detail = [item for item in parsed if not _is_total(item[0])]
        deduplicated: dict[
            str,
            tuple[
                str,
                float,
                float | None,
                float | None,
                float | None,
            ],
        ] = {}
        for item in detail:
            deduplicated.setdefault(item[0], item)
        detail = list(deduplicated.values())
        if not 2 <= len(detail) <= 30:
            continue

        amounts = [item[1] for item in parsed]
        factor = _unit_factor(rows, amounts)
        detail_total = sum(item[1] for item in detail)
        total = explicit_total or detail_total
        coverage = detail_total / total if total else 0
        ratio_sum = sum(
            item[2] for item in detail if item[2] is not None
        )
        ratio_coverage = ratio_sum / 100 if ratio_sum > 1.5 else ratio_sum

        if explicit_total is None and not ratio_sum:
            continue
        if explicit_total and not 0.88 <= coverage <= 1.12:
            continue
        if ratio_sum and not 0.88 <= ratio_coverage <= 1.12:
            continue

        score = (
            abs(1 - coverage)
            + (abs(1 - ratio_coverage) if ratio_sum else 0.03)
            + DIMENSION_PENALTY.get(dimension, 0.1)
        )
        candidates.append(
            (
                score,
                dimension,
                total * factor,
                [
                    (name, revenue * factor, growth, gross_margin)
                    for name, revenue, _, growth, gross_margin in detail
                ],
            )
        )

    if not candidates:
        return None

    preferred_dimension = DIMENSION_NAMES.get(
        str(preferred_dimension or "").lower(),
        preferred_dimension,
    )
    selected_candidates = (
        [
            candidate
            for candidate in candidates
            if preferred_dimension and candidate[1] == preferred_dimension
        ]
        or candidates
    )
    _, dimension, total_revenue, detail = min(
        selected_candidates,
        key=lambda item: item[0],
    )
    segments = []
    for name, revenue, reported_growth, reported_gross_margin in sorted(
        detail, key=lambda item: item[1], reverse=True
    ):
        base_growth = (
            min(max(reported_growth * 0.5, -0.05), 0.25)
            if reported_growth is not None
            else 0.06
        )
        segments.append(
            {
                "name": name,
                "revenue": round(revenue, 6),
                "description": "由公开 F10 主营构成数据自动提取。",
                "evidence": (
                    f"FY{latest_year or '最新'} 主营构成，"
                    f"分部合计覆盖总收入 "
                    f"{sum(item[1] for item in detail) / total_revenue:.1%}"
                ),
                "reported_growth": reported_growth,
                "reported_gross_margin": (
                    reported_gross_margin
                    if reported_gross_margin is not None and 0 <= reported_gross_margin <= 1
                    else None
                ),
                "base_growth": base_growth,
                "base_gross_margin": (
                    reported_gross_margin
                    if reported_gross_margin is not None and 0 <= reported_gross_margin <= 1
                    else 0.40
                ),
                "gross_margin_basis": (
                    "reported"
                    if reported_gross_margin is not None and 0 <= reported_gross_margin <= 1
                    else "estimated"
                ),
                "invalid_reported_gross_margin": (
                    reported_gross_margin
                    if reported_gross_margin is not None and not (0 <= reported_gross_margin <= 1)
                    else None
                ),
                "extraction_method": "structured_business_composition",
            }
        )
    reported_margin_segments = [
        segment
        for segment in segments
        if segment.get("reported_gross_margin") is not None
    ]
    gross_margin = None
    if len(reported_margin_segments) == len(segments):
        gross_margin = sum(
            segment["revenue"] * segment["reported_gross_margin"]
            for segment in reported_margin_segments
        ) / total_revenue

    payload_text = json.dumps(payload, ensure_ascii=False)
    if gross_margin is None:
        gross_margin_match = re.search(
            r"(?:整体|综合)?销售毛利率\s*([0-9]+(?:\.[0-9]+)?)%",
            payload_text,
        )
        if gross_margin_match:
            gross_margin = float(gross_margin_match.group(1)) / 100

    net_profit = None
    net_profit_match = re.search(
        r"(?:实现)?归属于上市公司股东的净利润\s*"
        r"([\d,]+(?:\.\d+)?)\s*(亿元|百万元|万元|元)",
        payload_text,
    )
    if net_profit_match:
        net_profit = _amount_in_millions(
            net_profit_match.group(1),
            net_profit_match.group(2),
        )

    return StructuredSegments(
        fiscal_year=latest_year,
        currency="人民币百万元",
        total_revenue=round(total_revenue, 6),
        segments=segments,
        dimension=dimension,
        source_url=EASTMONEY_BUSINESS_URL,
        gross_profit=(
            round(total_revenue * gross_margin, 6)
            if gross_margin is not None
            else None
        ),
        gross_margin=gross_margin,
        net_profit=round(net_profit, 6) if net_profit is not None else None,
        net_margin=(
            net_profit / total_revenue
            if net_profit is not None and total_revenue
            else None
        ),
        available_dimensions=sorted({dimension for _, dimension, _, _ in candidates}),
    )


# ── Phase 12B-1：真实多年度 historical_periods 构建 ────────────


def build_historical_periods_from_f10(
    payload: Any,
    preferred_dimension: str | None = None,
    *,
    source_name: str = "公开结构化数据（东方财富 F10）",
    source_url: str = EASTMONEY_BUSINESS_URL,
) -> dict[str, list[dict[str, Any]]]:
    """从 F10 原始数据构建真实多年度 historical_periods。

    Section 4：修复来源真实性。

    规则：
    1. 只保留 12-31 年报记录（排除半年报、季度报告）；
    2. 按同一 dimension 分组，排除"其中:"子项，保留"其他(补充)"但标记说明；
    3. 每个年度单独保存 18 个 provenance 字段；
    4. comparability_key 基于分部名称归一化，同分部跨年度一致；
    5. revenue_nature="f10_structured"（公开结构化数据，不等同于实时官方披露）；
    6. gross_margin_nature 按是否有值标记；
    7. revenue_publication_date / gross_margin_publication_date 留空（F10 payload
       无发布日期），页面显示"发布日期未取得"；
    8. period_end_date 只表示报告期截止日，不作为发布日期；
    9. 收入单位转换为百万元（与 StructuredSegments 一致）；
    10. 单一分部公司不因 len(detail_items) < 2 而失去历史。

    Returns:
        {segment_name: [historical_period, ...]}
    """
    records = _rows(payload)
    if not records:
        return {}

    # 只保留年报记录（12-31）
    full_year_records = [
        row for row in records
        if re.search(r"(?:12[-/]31|12月31)", str(_value(row, DATE_FIELDS) or ""))
    ]
    if not full_year_records:
        return {}

    # 按 (year, dimension) 分组
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in full_year_records:
        year = _report_year(_value(row, DATE_FIELDS))
        dim = _dimension(_value(row, TYPE_FIELDS))
        if year and dim:
            grouped.setdefault((year, dim), []).append(row)

    if not grouped:
        return {}

    # 确定 preferred_dimension
    preferred_dim = DIMENSION_NAMES.get(
        str(preferred_dimension or "").lower(), preferred_dimension
    )
    # 获取所有年度
    all_years = sorted({yr for (yr, _) in grouped.keys()})
    # 获取所有 dimension
    all_dims = sorted({dim for (_, dim) in grouped.keys()})

    # 选择目标 dimension：优先 preferred_dimension，否则取记录最多的
    if preferred_dim and preferred_dim in all_dims:
        target_dim = preferred_dim
    else:
        target_dim = max(
            all_dims,
            key=lambda d: sum(
                len(rows) for (yr, dim), rows in grouped.items() if dim == d
            ),
        )

    # 按 segment_name 收集各年度数据
    segment_periods: dict[str, list[dict[str, Any]]] = {}

    for year in all_years:
        key = (year, target_dim)
        if key not in grouped:
            continue
        rows = grouped[key]

        # 计算该年度该 dimension 的单位因子（F10 默认元，转百万元）
        amounts = [_number(_value(r, REVENUE_FIELDS)) or 0 for r in rows]
        factor = _unit_factor(rows, amounts)

        # 收集明细项：保留"其他(补充)"但标记说明，排除"其中:"子项和"合计"行
        detail_items = []
        supplement_items = []  # "其他(补充)"单独保留
        for row in rows:
            name = re.sub(r"\s+", " ", str(_value(row, NAME_FIELDS) or "")).strip()
            if not name:
                continue
            # 排除"其中:"子项（地区子项）
            if name.startswith("其中:") or name.startswith("其中："):
                continue
            # 排除"合计"行
            if _is_total(name):
                continue
            revenue = _number(_value(row, REVENUE_FIELDS))
            if revenue is None or revenue <= 0:
                continue
            # "其他(补充)"平衡项单独保留，不与其他明细项一起跳过
            if "补充" in name or name == "其他(补充)":
                supplement_items.append((name, row, revenue, factor))
            else:
                detail_items.append((name, row, revenue, factor))

        # Section 5.8：单一分部公司不因 len < 2 而失去历史
        # 合并明细项和补充项
        all_items = detail_items + supplement_items
        if not all_items:
            continue

        # 每个分部构建 historical_period 记录
        for name, row, revenue, _ in all_items:
            revenue_in_millions = revenue * factor
            gross_margin_raw = _number(_value(row, GROSS_MARGIN_FIELDS))
            # GROSS_RPOFIT_RATIO 已是小数（如 0.22），不需除以 100
            if gross_margin_raw is not None and abs(gross_margin_raw) > 1:
                gross_margin_value = gross_margin_raw / 100
            else:
                gross_margin_value = gross_margin_raw

            report_date = str(_value(row, DATE_FIELDS) or "").strip()
            period_end_date = report_date[:10] if len(report_date) >= 10 else report_date

            # 是否为"其他(补充)"
            is_supplement = "补充" in name or name == "其他(补充)"

            # comparability_key 基于分部名称归一化
            comp_key = _normalize_segment_name_for_key(name)

            comp_note = f"FY{year} F10 {target_dim} 口径"
            if is_supplement:
                comp_note += "（补充项，原数据标记为'其他(补充)'）"

            period = {
                "fiscal_year": year,
                "period_end_date": period_end_date,
                "revenue": round(revenue_in_millions, 6),
                "gross_margin": (
                    round(gross_margin_value, 6)
                    if gross_margin_value is not None
                    and 0 <= gross_margin_value <= 1
                    else None
                ),
                "revenue_nature": "f10_structured",
                "gross_margin_nature": (
                    "f10_structured"
                    if gross_margin_value is not None
                    and 0 <= gross_margin_value <= 1
                    else "missing"
                ),
                "revenue_channel": source_name,
                "revenue_source_name": source_name,
                "revenue_url": source_url,
                # Section 4.3：F10 原始 payload 没有发布日期，字段留空
                "revenue_publication_date": "",
                "gross_margin_channel": source_name,
                "gross_margin_source_name": source_name,
                "gross_margin_url": source_url,
                "gross_margin_publication_date": "",
                "currency": "人民币百万元",
                "unit": "百万元",
                "dimension": target_dim,
                "comparability_key": comp_key,
                "comparability_note": comp_note,
            }
            segment_periods.setdefault(name, []).append(period)

    return segment_periods


# Phase 12B-1 收口：分部合计（非公司总收入）


def build_segment_historical_totals_from_f10(
    payload: Any,
    preferred_dimension: str | None = None,
    *,
    source_name: str = "公开结构化数据（东方财富 F10）",
    source_url: str = EASTMONEY_BUSINESS_URL,
) -> dict[str, dict[str, Any]]:
    """从 F10 原始数据构建按财年索引的分部合计（非公司总收入）。

    重要：此函数处理的是 F10 主营构成表的合计行或分部明细之和，只能叫"分部合计"，
    不得叫"公司合计收入"，也不得用于 residual 倒算。

    计算规则：
    - 如果 F10 主营构成表中有"合计"行，优先使用该合计行，标记为 segment_table_total；
    - 否则按"非其中子项、非合计"明细项收入之和计算，标记为 segment_sum。

    Returns:
        {
            "2023": {
                "revenue": ...,
                "currency": "人民币百万元",
                "unit": "百万元",
                "dimension": "product",
                "period_end_date": "2023-12-31",
                "source_type": "segment_table_total" | "segment_sum",
                "source_name": "公开结构化数据（东方财富 F10）",
                "source_url": "...",
                "publication_date": ""
            }
        }
    """
    records = _rows(payload)
    if not records:
        return {}

    # 只保留年报记录（12-31）
    full_year_records = [
        row for row in records
        if re.search(r"(?:12[-/]31|12月31)", str(_value(row, DATE_FIELDS) or ""))
    ]
    if not full_year_records:
        return {}

    # 按 (year, dimension) 分组
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in full_year_records:
        year = _report_year(_value(row, DATE_FIELDS))
        dim = _dimension(_value(row, TYPE_FIELDS))
        if year and dim:
            grouped.setdefault((year, dim), []).append(row)

    if not grouped:
        return {}

    # 确定 preferred_dimension
    preferred_dim = DIMENSION_NAMES.get(
        str(preferred_dimension or "").lower(), preferred_dimension
    )
    all_dims = sorted({dim for (_, dim) in grouped.keys()})

    if preferred_dim and preferred_dim in all_dims:
        target_dim = preferred_dim
    else:
        target_dim = max(
            all_dims,
            key=lambda d: sum(
                len(rows) for (yr, dim), rows in grouped.items() if dim == d
            ),
        )

    totals: dict[str, dict[str, Any]] = {}

    for (year, dim), rows in grouped.items():
        if dim != target_dim:
            continue
        amounts = [_number(_value(r, REVENUE_FIELDS)) or 0 for r in rows]
        factor = _unit_factor(rows, amounts)

        # 优先查找 F10 主营构成表中的"合计"行（数据源提供的，非我们计算）
        total_row = None
        for row in rows:
            name = re.sub(r"\s+", " ", str(_value(row, NAME_FIELDS) or "")).strip()
            if _is_total(name):
                total_row = row
                break

        period_end_date = ""
        report_date = str(_value(rows[0], DATE_FIELDS) or "").strip() if rows else ""
        if report_date:
            period_end_date = report_date[:10] if len(report_date) >= 10 else report_date

        if total_row is not None:
            # 使用 F10 主营构成表的"合计"行
            total_revenue = _number(_value(total_row, REVENUE_FIELDS))
            if total_revenue is not None and total_revenue > 0:
                totals[year] = {
                    "fiscal_year": year,
                    "revenue": round(total_revenue * factor, 6),
                    "currency": "人民币百万元",
                    "unit": "百万元",
                    "dimension": target_dim,
                    "period_end_date": period_end_date,
                    "source_type": "segment_table_total",
                    "source_name": source_name,
                    "source_url": source_url,
                    "publication_date": "",
                }
                continue

        # 没有"合计"行时，按分部明细相加计算
        year_total = 0.0
        for row in rows:
            name = re.sub(r"\s+", " ", str(_value(row, NAME_FIELDS) or "")).strip()
            if not name:
                continue
            if name.startswith("其中:") or name.startswith("其中："):
                continue
            if _is_total(name):
                continue
            revenue = _number(_value(row, REVENUE_FIELDS))
            if revenue is not None and revenue > 0:
                year_total += revenue * factor

        if year_total > 0:
            totals[year] = {
                "fiscal_year": year,
                "revenue": round(year_total, 6),
                "currency": "人民币百万元",
                "unit": "百万元",
                "dimension": target_dim,
                "period_end_date": period_end_date,
                "source_type": "segment_sum",
                "source_name": source_name,
                "source_url": source_url,
                "publication_date": "",
            }

    return totals


# ── Phase 12B-1 收口：原始历史分部池 ──────────────────────


def build_raw_historical_segment_pool(
    payload: Any,
    preferred_dimension: str | None = None,
    *,
    source_name: str = "公开结构化数据（东方财富 F10）",
    source_url: str = EASTMONEY_BUSINESS_URL,
) -> list[dict[str, Any]]:
    """从 F10 原始数据构建真实的原始历史分部池。

    保留所有历史年度的分部记录，包括当前年度不再出现的旧口径分部。
    不做名称模糊匹配、金额接近或 AI 猜测。

    Returns:
        [
            {
                "fiscal_year": "2022",
                "original_segment_name": "旧产品A",
                "revenue": 450.0,
                "currency": "人民币百万元",
                "unit": "百万元",
                "dimension": "product",
                "period_end_date": "2022-12-31",
                "revenue_nature": "f10_structured",
                "source_name": "公开结构化数据（东方财富 F10）",
                "source_url": "...",
                "publication_date": "",
            },
            ...
        ]
    """
    records = _rows(payload)
    if not records:
        return []

    # 只保留年报记录（12-31）
    full_year_records = [
        row for row in records
        if re.search(r"(?:12[-/]31|12月31)", str(_value(row, DATE_FIELDS) or ""))
    ]
    if not full_year_records:
        return []

    # 按 (year, dimension) 分组
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in full_year_records:
        year = _report_year(_value(row, DATE_FIELDS))
        dim = _dimension(_value(row, TYPE_FIELDS))
        if year and dim:
            grouped.setdefault((year, dim), []).append(row)

    if not grouped:
        return []

    # 确定 preferred_dimension
    preferred_dim = DIMENSION_NAMES.get(
        str(preferred_dimension or "").lower(), preferred_dimension
    )
    all_dims = sorted({dim for (_, dim) in grouped.keys()})

    if preferred_dim and preferred_dim in all_dims:
        target_dim = preferred_dim
    else:
        target_dim = max(
            all_dims,
            key=lambda d: sum(
                len(rows) for (yr, dim), rows in grouped.items() if dim == d
            ),
        )

    pool: list[dict[str, Any]] = []

    for (year, dim), rows in grouped.items():
        if dim != target_dim:
            continue
        amounts = [_number(_value(r, REVENUE_FIELDS)) or 0 for r in rows]
        factor = _unit_factor(rows, amounts)

        for row in rows:
            name = re.sub(r"\s+", " ", str(_value(row, NAME_FIELDS) or "")).strip()
            if not name:
                continue
            if name.startswith("其中:") or name.startswith("其中："):
                continue
            if _is_total(name):
                continue
            revenue = _number(_value(row, REVENUE_FIELDS))
            if revenue is None or revenue <= 0:
                continue

            report_date = str(_value(row, DATE_FIELDS) or "").strip()
            period_end_date = report_date[:10] if len(report_date) >= 10 else report_date

            gross_margin_raw = _number(_value(row, GROSS_MARGIN_FIELDS))
            if gross_margin_raw is not None and abs(gross_margin_raw) > 1:
                gross_margin_value = gross_margin_raw / 100
            else:
                gross_margin_value = gross_margin_raw

            pool.append({
                "fiscal_year": year,
                "original_segment_name": name,
                "revenue": round(revenue * factor, 6),
                "gross_margin": (
                    round(gross_margin_value, 6)
                    if gross_margin_value is not None
                    and 0 <= gross_margin_value <= 1
                    else None
                ),
                "currency": "人民币百万元",
                "unit": "百万元",
                "dimension": target_dim,
                "period_end_date": period_end_date,
                "revenue_nature": "f10_structured",
                "source_name": source_name,
                "source_url": source_url,
                "publication_date": "",
            })

    return pool


# ── Phase 12B-1 收口：独立公司总收入 ──────────────────────


def build_company_financial_totals(
    payload: Any,
    preferred_dimension: str | None = None,
    *,
    source_name: str = "公开结构化数据（东方财富 F10）",
    source_url: str = EASTMONEY_BUSINESS_URL,
) -> dict[str, dict[str, Any]]:
    """从独立来源构建按财年索引的公司总收入。

    公司逐年度总收入必须来自真正独立的来源：
    - 合并利润表营业收入；
    - 官方年报财务报表；
    - 已核验财务摘要；
    - 明确属于财务报表的结构化接口。

    F10 主营构成表不是独立来源——它是同一张分部构成表。
    即使 F10 payload 中有"合计"行，那也只是主营构成表的合计行
    （segment_table_total），不能冒充合并利润表营业收入（income_statement_total）。

    当前阶段 F10 provider 尚未接入独立的合并利润表/财务报表接口，
    因此返回空 dict。页面显示"缺少独立公司财务总收入，暂无法倒算其他业务"。

    不得为了测试向 F10 fixture 人工加入"合计"行并冒充利润表收入。

    Returns:
        {} — 当前阶段无独立来源，始终返回空。
    """
    return {}


def _normalize_segment_name_for_key(name: str) -> str:
    """将分部名称归一化为 comparability_key。

    去除空格、中英文标点，统一为小写。
    """
    result = re.sub(r"[\s\u3000]+", "", str(name))
    result = re.sub(r"[（）()【】\[\]「」""''\"']", "", result)
    return result.lower()


class AShareBusinessCompositionProvider:
    name = "公开F10主营构成"

    def supports(self, company: CompanyCandidate) -> bool:
        symbol = company.symbol.upper()
        return symbol.endswith((".SH", ".SS", ".SZ", ".BJ")) or (
            company.exchange.upper() in {"SSE", "SHH", "SZSE", "SHZ", "BSE"}
        )

    @staticmethod
    def _security_code(company: CompanyCandidate) -> str:
        code = company.symbol.split(".")[0]
        suffix = company.symbol.upper().split(".")[-1]
        market = "SH" if suffix in {"SH", "SS"} else "SZ"
        if suffix == "BJ":
            market = "BJ"
        return f"{market}{code}"

    def fetch(
        self,
        company: CompanyCandidate,
        preferred_dimension: str | None = None,
    ) -> StructuredSegments | None:
        if not self.supports(company):
            return None

        security_code = self._security_code(company)
        path = _cache_path(security_code)
        failure_path = path.with_suffix(".failed")
        ttl = float(os.getenv("A_SHARE_COMPOSITION_CACHE_TTL_HOURS", "24"))
        payload = _load_json(path) if _fresh(path, ttl) else {}
        if not payload and _fresh(failure_path, 1 / 6):
            return None
        if not payload:
            try:
                response = requests.get(
                    EASTMONEY_BUSINESS_URL,
                    params={"code": security_code},
                    headers={
                        "User-Agent": (
                            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 Chrome/125 Safari/537.36"
                        ),
                        "Accept": "application/json, text/plain, */*",
                        "Referer": (
                            "https://emweb.securities.eastmoney.com/"
                            f"PC_HSF10/BusinessAnalysis/Index?type=web&code={security_code}"
                        ),
                    },
                    timeout=float(
                        os.getenv("A_SHARE_COMPOSITION_TIMEOUT_SECONDS", "5")
                    ),
                )
                response.raise_for_status()
                payload = response.json()
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(payload, ensure_ascii=False),
                    encoding="utf-8",
                )
                failure_path.unlink(missing_ok=True)
            except (requests.RequestException, ValueError, OSError):
                payload = _load_json(path)
                if not payload:
                    try:
                        CACHE_DIR.mkdir(parents=True, exist_ok=True)
                        failure_path.write_text(
                            datetime.now(timezone.utc).isoformat(),
                            encoding="utf-8",
                        )
                    except OSError:
                        pass
        result = parse_business_composition(
            payload,
            preferred_dimension=preferred_dimension,
        )
        if not result:
            return None
        return replace(
            result,
            source_url=f"{EASTMONEY_BUSINESS_URL}?code={security_code}",
        )

    def fetch_historical_periods(
        self,
        company: CompanyCandidate,
        preferred_dimension: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Phase 12B-1：从 F10 缓存构建真实多年度 historical_periods。

        Returns:
            {segment_name: [historical_period, ...]}
            若无缓存或无年报数据返回空 dict。
        """
        if not self.supports(company):
            return {}

        security_code = self._security_code(company)
        path = _cache_path(security_code)
        payload = _load_json(path)
        if not payload:
            return {}

        return build_historical_periods_from_f10(
            payload,
            preferred_dimension=preferred_dimension,
            source_url=f"{EASTMONEY_BUSINESS_URL}?code={security_code}",
        )

    def fetch_segment_historical_totals(
        self,
        company: CompanyCandidate,
        preferred_dimension: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Phase 12B-1 收口：从 F10 缓存构建按财年索引的分部合计（非公司总收入）。

        Returns:
            {"2023": {"revenue": ..., "source_type": "segment_sum", ...}}
            若无缓存或无年报数据返回空 dict。
        """
        if not self.supports(company):
            return {}

        security_code = self._security_code(company)
        path = _cache_path(security_code)
        payload = _load_json(path)
        if not payload:
            return {}

        return build_segment_historical_totals_from_f10(
            payload,
            preferred_dimension=preferred_dimension,
            source_url=f"{EASTMONEY_BUSINESS_URL}?code={security_code}",
        )

    def fetch_company_financial_totals(
        self,
        company: CompanyCandidate,
        preferred_dimension: str | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Phase 12B-1 收口：从独立来源构建按财年索引的公司总收入。

        公司逐年度总收入只能来自真正独立的合并利润表/官方年报财务报表/
        已核验财务摘要/结构化财务报表接口。
        F10 主营构成表不是独立来源，即使有"合计"行也只能作为
        segment_table_total（分部合计），不得冒充 income_statement_total。

        当前阶段 F10 provider 尚未接入独立来源，始终返回空 dict。
        页面显示"缺少独立公司财务总收入，暂无法倒算其他业务"。

        Returns:
            {} — 当前阶段无独立来源，始终返回空。
        """
        if not self.supports(company):
            return {}

        security_code = self._security_code(company)
        path = _cache_path(security_code)
        payload = _load_json(path)
        if not payload:
            return {}

        return build_company_financial_totals(
            payload,
            preferred_dimension=preferred_dimension,
            source_url=f"{EASTMONEY_BUSINESS_URL}?code={security_code}",
        )

    def fetch_raw_historical_segment_pool(
        self,
        company: CompanyCandidate,
        preferred_dimension: str | None = None,
    ) -> list[dict[str, Any]]:
        """Phase 12B-1 收口：构建真实的原始历史分部池。

        保留所有历史年度的分部记录，包括当前年度不再出现的旧口径分部。

        Returns:
            [{"fiscal_year": "2022", "original_segment_name": "旧产品A", ...}]
            若无缓存或无年报数据返回空 list。
        """
        if not self.supports(company):
            return []

        security_code = self._security_code(company)
        path = _cache_path(security_code)
        payload = _load_json(path)
        if not payload:
            return []

        return build_raw_historical_segment_pool(
            payload,
            preferred_dimension=preferred_dimension,
            source_url=f"{EASTMONEY_BUSINESS_URL}?code={security_code}",
        )
