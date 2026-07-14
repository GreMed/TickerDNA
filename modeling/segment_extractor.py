from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import re
from typing import Any


REVENUE_NAME_HINTS = (
    "revenuefromcontractwithcustomer",
    "salesrevenuenet",
    "netsales",
    "revenues",
    "revenue",
)
PROFIT_CONCEPT_LABELS = {
    "netincomeloss": "分部净利润",
    "profitloss": "分部净利润",
    "segmentprofitloss": "分部利润",
    "operatingincomeloss": "分部营业利润",
    "operatingincome": "分部营业利润",
    "incomelossfromcontinuingoperationsbeforeincometaxes": "分部税前利润",
    "earningsbeforeinteresttaxesdepreciationandamortization": "分部EBITDA",
    "ebitda": "分部EBITDA",
}
SEGMENT_AXIS_HINTS = (
    "statementbusinesssegmentsaxis",
    "businesssegmentsaxis",
    "operatingsegmentsaxis",
    "reportablesegmentsaxis",
)
PRODUCT_AXIS_HINTS = (
    "productorserviceaxis",
    "productandserviceaxis",
    "revenuebyproductaxis",
    "revenuebyserviceaxis",
)
GEOGRAPHY_AXIS_HINTS = (
    "geographical",
    "geography",
    "country",
    "region",
)
EXCLUDED_AXIS_HINTS = (
    "customer",
    "consolidation",
)
EXCLUDED_MEMBER_HINTS = (
    "consolidated",
    "elimination",
    "intersegment",
    "allmember",
    "totalmember",
)
AGGREGATE_MEMBER_NAMES = {
    "reportablesegments",
    "reportablesegment",
    "totalreportablesegments",
    "totalreportablesegment",
    "allreportablesegments",
    "allreportablesegment",
    "total",
    "totalsegments",
    "segmenttotal",
}


@dataclass(frozen=True)
class XbrlContext:
    context_id: str
    start_date: str
    end_date: str
    dimensions: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class XbrlFact:
    name: str
    context_ref: str
    value: float
    unit_ref: str


@dataclass(frozen=True)
class SegmentExtraction:
    segments: list[dict[str, Any]]
    dimension: str
    available_dimensions: list[str]


def _attributes(tag: str) -> dict[str, str]:
    return {
        key.lower(): unescape(value)
        for key, _, value in re.findall(
            r"""([\w:.-]+)\s*=\s*(["'])(.*?)\2""", tag, flags=re.DOTALL
        )
    }


def _tag_text(block: str, local_name: str) -> str:
    match = re.search(
        rf"<(?:[\w.-]+:)?{re.escape(local_name)}\b[^>]*>(.*?)"
        rf"</(?:[\w.-]+:)?{re.escape(local_name)}>",
        block,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return ""
    return unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()


def _local_name(value: str) -> str:
    return value.split(":")[-1]


def _humanize_member(value: str) -> str:
    name = _local_name(value)
    name = re.sub(r"(Member|Domain)$", "", name, flags=re.IGNORECASE)
    name = name.replace("IPhone", "iPhone").replace("IPad", "iPad")
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)
    name = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _parse_contexts(html: str) -> dict[str, XbrlContext]:
    contexts: dict[str, XbrlContext] = {}
    pattern = re.compile(
        r"<(?:[\w.-]+:)?context\b(?P<tag>[^>]*)>(?P<body>.*?)"
        r"</(?:[\w.-]+:)?context>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    member_pattern = re.compile(
        r"<(?:[\w.-]+:)?explicitmember\b(?P<tag>[^>]*)>(?P<member>.*?)"
        r"</(?:[\w.-]+:)?explicitmember>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(html):
        attrs = _attributes(match.group("tag"))
        context_id = attrs.get("id", "")
        if not context_id:
            continue
        body = match.group("body")
        dimensions = []
        for member_match in member_pattern.finditer(body):
            member_attrs = _attributes(member_match.group("tag"))
            dimension = member_attrs.get("dimension", "")
            member = re.sub(r"<[^>]+>", "", member_match.group("member")).strip()
            if dimension and member:
                dimensions.append((dimension, unescape(member)))
        contexts[context_id] = XbrlContext(
            context_id=context_id,
            start_date=_tag_text(body, "startDate"),
            end_date=_tag_text(body, "endDate"),
            dimensions=tuple(dimensions),
        )
    return contexts


def _parse_number(text: str, attrs: dict[str, str]) -> float | None:
    clean = unescape(re.sub(r"<[^>]+>", "", text))
    clean = clean.replace("\u00a0", "").replace(",", "").strip()
    if not clean or clean in {"—", "-", "–"}:
        return None
    negative = clean.startswith("(") and clean.endswith(")")
    clean = clean.strip("()")
    clean = re.sub(r"[^0-9.eE+-]", "", clean)
    if not clean:
        return None
    try:
        value = float(clean)
        scale = int(attrs.get("scale", "0") or 0)
        value *= 10**scale
        if attrs.get("sign") == "-" or negative:
            value *= -1
        return value / 1_000_000
    except (ValueError, OverflowError):
        return None


def _parse_facts(html: str) -> list[XbrlFact]:
    facts: list[XbrlFact] = []
    pattern = re.compile(
        r"<(?:ix:)?nonfraction\b(?P<tag>[^>]*)>(?P<body>.*?)"
        r"</(?:ix:)?nonfraction>",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(html):
        attrs = _attributes(match.group("tag"))
        name = attrs.get("name", "")
        context_ref = attrs.get("contextref", "")
        if not name or not context_ref:
            continue
        value = _parse_number(match.group("body"), attrs)
        if value is None:
            continue
        facts.append(
            XbrlFact(
                name=name,
                context_ref=context_ref,
                value=value,
                unit_ref=attrs.get("unitref", ""),
            )
        )
    return facts


def _is_revenue_fact(name: str) -> bool:
    local = _local_name(name).lower()
    if "costof" in local or "deferredrevenue" in local:
        return False
    return any(hint in local for hint in REVENUE_NAME_HINTS)


def _profit_metric_label(name: str) -> str:
    local = _local_name(name).lower()
    for concept, label in PROFIT_CONCEPT_LABELS.items():
        if concept in local:
            return label
    return ""


def _segment_match_key(value: str) -> str:
    key = _humanize_member(value)
    key = re.sub(
        r"\b(products?|services?|segments?|businesses?|business|and|other)\b",
        " ",
        key,
        flags=re.IGNORECASE,
    )
    key = re.sub(r"[^a-z0-9]+", "", key.lower())
    return key


def _is_aggregate_member(value: str) -> bool:
    key = re.sub(r"[^a-z0-9]+", "", _humanize_member(value).lower())
    return key in AGGREGATE_MEMBER_NAMES


def _dimension_kind(axis: str) -> str:
    normalized_axis = _local_name(axis).lower()
    if any(hint in normalized_axis for hint in GEOGRAPHY_AXIS_HINTS):
        return "geography"
    if any(hint in normalized_axis for hint in PRODUCT_AXIS_HINTS):
        return "product"
    if any(hint in normalized_axis for hint in SEGMENT_AXIS_HINTS):
        return "business"
    return ""


def _segment_dimension(context: XbrlContext) -> tuple[str, str, str] | None:
    candidates: list[tuple[str, str, str]] = []
    for axis, member in context.dimensions:
        normalized_axis = _local_name(axis).lower()
        if any(hint in normalized_axis for hint in EXCLUDED_AXIS_HINTS):
            continue
        dimension = _dimension_kind(axis)
        if not dimension:
            continue
        normalized_member = _local_name(member).lower()
        if any(hint in normalized_member for hint in EXCLUDED_MEMBER_HINTS):
            continue
        candidates.append((dimension, axis, member))
    return candidates[0] if len(candidates) == 1 else None


def _candidate_groups(
    contexts: dict[str, XbrlContext], facts: list[XbrlFact]
) -> dict[tuple[str, str, str, str], dict[str, float]]:
    groups: dict[tuple[str, str, str, str], dict[str, float]] = {}
    for fact in facts:
        if not _is_revenue_fact(fact.name):
            continue
        context = contexts.get(fact.context_ref)
        if not context or not context.end_date or fact.value <= 0:
            continue
        dimension = _segment_dimension(context)
        if not dimension:
            continue
        dimension_name, axis, member = dimension
        if _is_aggregate_member(member):
            continue
        key = (
            dimension_name,
            _local_name(axis),
            _local_name(fact.name),
            context.end_date,
        )
        member_name = _humanize_member(member)
        if not member_name:
            continue
        groups.setdefault(key, {})[member_name] = max(
            groups.setdefault(key, {}).get(member_name, 0),
            fact.value,
        )
    return groups


def _metric_groups(
    contexts: dict[str, XbrlContext],
    facts: list[XbrlFact],
) -> dict[tuple[str, str, str, str], dict[str, float]]:
    groups: dict[tuple[str, str, str, str], dict[str, float]] = {}
    for fact in facts:
        metric_label = _profit_metric_label(fact.name)
        if not metric_label:
            continue
        context = contexts.get(fact.context_ref)
        if not context or not context.end_date:
            continue
        dimension = _segment_dimension(context)
        if not dimension:
            continue
        dimension_name, axis, member = dimension
        if _is_aggregate_member(member):
            continue
        key = (
            dimension_name,
            _local_name(axis),
            _local_name(fact.name),
            context.end_date,
        )
        member_name = _humanize_member(member)
        if not member_name:
            continue
        groups.setdefault(key, {})[member_name] = fact.value
    return groups


def _attach_reported_profit_metrics(
    segments: list[dict[str, Any]],
    metric_groups: dict[tuple[str, str, str, str], dict[str, float]],
    *,
    selected_dimension: str,
    selected_end: str,
) -> None:
    if not segments:
        return

    segment_keys = {
        _segment_match_key(segment["name"]): segment
        for segment in segments
        if _segment_match_key(segment["name"])
    }
    if not segment_keys:
        return

    compatible_dimensions = (
        {"product", "business"}
        if selected_dimension in {"product", "business"}
        else {selected_dimension}
    )
    candidates: list[
        tuple[int, int, str, str, str, dict[str, float], dict[str, dict[str, Any]]]
    ] = []
    for (
        dimension_name,
        axis,
        concept,
        period_end,
    ), values in metric_groups.items():
        if dimension_name not in compatible_dimensions or period_end != selected_end:
            continue
        label = _profit_metric_label(concept)
        if not label:
            continue
        matches: dict[str, dict[str, Any]] = {}
        for member_name, value in values.items():
            key = _segment_match_key(member_name)
            if key in segment_keys:
                matches[member_name] = segment_keys[key]
        if not matches:
            continue
        dimension_bonus = 1 if dimension_name == selected_dimension else 0
        candidates.append(
            (
                len(matches),
                dimension_bonus,
                label,
                concept,
                axis,
                values,
                matches,
            )
        )

    if not candidates:
        return

    _, _, label, concept, axis, values, matches = max(
        candidates,
        key=lambda item: (item[0], item[1], item[2]),
    )
    for member_name, segment in matches.items():
        value = values[member_name]
        revenue = float(segment.get("revenue", 0))
        margin = value / revenue if revenue else None
        metric = {
            "name": label,
            "value": round(value, 6),
            "margin": margin,
            "concept": concept,
            "axis": axis,
            "basis": "reported",
        }
        segment.setdefault("supplemental_metrics", []).append(metric)
        segment["reported_profit"] = round(value, 6)
        segment["reported_profit_margin"] = margin
        segment["profit_metric_name"] = label
        segment["profit_metric_basis"] = "reported"


def extract_inline_xbrl_segment_data(
    html: str,
    total_revenue: float,
    report_date: str = "",
    tolerance: float = 0.12,
    preferred_dimension: str | None = None,
) -> SegmentExtraction:
    """Extract and reconcile annual revenue segments from an SEC iXBRL filing."""
    if not html or total_revenue <= 0:
        return SegmentExtraction([], "", [])

    contexts = _parse_contexts(html)
    facts = _parse_facts(html)
    groups = _candidate_groups(contexts, facts)
    metric_groups = _metric_groups(contexts, facts)
    candidates: list[
        tuple[float, tuple[str, str, str, str], dict[str, float]]
    ] = []
    for key, values in groups.items():
        dimension_name, axis, concept, period_end = key
        if not 2 <= len(values) <= 12:
            continue
        segment_total = sum(values.values())
        coverage = segment_total / total_revenue
        if not 1 - tolerance <= coverage <= 1 + tolerance:
            continue
        dimension_penalty = {
            "business": 0.0,
            "product": 0.01,
            "geography": 0.12,
        }.get(dimension_name, 0.08)
        period_penalty = 0 if report_date and period_end == report_date else 0.03
        score = abs(1 - coverage) + period_penalty + dimension_penalty
        candidates.append((score, key, values))

    if not candidates:
        return SegmentExtraction([], "", [])

    selected_candidates = (
        [
            candidate
            for candidate in candidates
            if preferred_dimension and candidate[1][0] == preferred_dimension
        ]
        or candidates
    )
    _, current_key, current_values = min(
        selected_candidates,
        key=lambda item: item[0],
    )
    dimension_name, axis, concept, current_end = current_key
    previous_candidates = [
        (end, values)
        for (
            group_dimension,
            group_axis,
            group_concept,
            end,
        ), values in groups.items()
        if group_dimension == dimension_name
        and group_axis == axis
        and group_concept == concept
        and end < current_end
        and set(current_values).issubset(values)
    ]
    previous_values = (
        max(previous_candidates, key=lambda item: item[0])[1]
        if previous_candidates
        else {}
    )

    segments = []
    for name, revenue in sorted(
        current_values.items(), key=lambda item: item[1], reverse=True
    ):
        prior_revenue = previous_values.get(name)
        reported_growth = (
            revenue / prior_revenue - 1
            if prior_revenue and prior_revenue > 0
            else None
        )
        if reported_growth is None:
            base_growth = 0.06
        else:
            base_growth = min(max(reported_growth * 0.5, -0.05), 0.25)
        segments.append(
            {
                "name": name,
                "revenue": round(revenue, 6),
                "description": f"由 SEC Inline XBRL 的 {axis} 自动提取。",
                "evidence": (
                    f"{current_end} {concept} / {axis}，"
                    f"自动校验覆盖总收入 {sum(current_values.values()) / total_revenue:.1%}"
                ),
                "reported_growth": reported_growth,
                "base_growth": base_growth,
                "base_gross_margin": 0.45,
                "segment_dimension": dimension_name,
                "extraction_method": "sec_inline_xbrl_dimensions",
            }
        )
    _attach_reported_profit_metrics(
        segments,
        metric_groups,
        selected_dimension=dimension_name,
        selected_end=current_end,
    )
    return SegmentExtraction(
        segments=segments,
        dimension=dimension_name,
        available_dimensions=sorted({candidate[1][0] for candidate in candidates}),
    )


def extract_inline_xbrl_segments(
    html: str,
    total_revenue: float,
    report_date: str = "",
    tolerance: float = 0.12,
    preferred_dimension: str | None = None,
) -> list[dict[str, Any]]:
    """Extract and reconcile annual revenue segments from an SEC iXBRL filing."""
    data = extract_inline_xbrl_segment_data(
        html,
        total_revenue,
        report_date=report_date,
        tolerance=tolerance,
        preferred_dimension=preferred_dimension,
    )
    return data.segments


def _visible_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _table_rows(table: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in re.findall(
        r"<tr\b[^>]*>(.*?)</tr>", table, flags=re.IGNORECASE | re.DOTALL
    ):
        cells = [
            _visible_text(cell)
            for cell in re.findall(
                r"<t[dh]\b[^>]*>(.*?)</t[dh]>",
                row,
                flags=re.IGNORECASE | re.DOTALL,
            )
        ]
        if cells:
            rows.append(cells)
    return rows


def _cell_number(value: str) -> float | None:
    clean = value.replace(",", "").replace("$", "").strip()
    negative = clean.startswith("(") and clean.endswith(")")
    clean = clean.strip("()")
    match = re.search(r"-?\d+(?:\.\d+)?", clean)
    if not match:
        return None
    number = float(match.group())
    return -number if negative else number


def extract_revenue_table_segments(
    html: str,
    total_revenue: float,
    report_date: str = "",
    tolerance: float = 0.12,
    preferred_dimension: str | None = None,
) -> list[dict[str, Any]]:
    """Fallback extraction for annual revenue tables without usable XBRL dimensions."""
    if not html or total_revenue <= 0:
        return []

    candidates: list[tuple[float, str, list[tuple[str, float]], str]] = []
    for table in re.findall(
        r"<table\b[^>]*>.*?</table>", html, flags=re.IGNORECASE | re.DOTALL
    ):
        visible = _visible_text(table).lower()
        if not any(term in visible for term in ("net sales", "revenue", "revenues")):
            continue
        is_geography_table = any(
            term in visible
            for term in ("geographic", "geographical", "geography", "region", "area")
        )
        is_segment_table = any(
            term in visible for term in ("product", "service", "segment")
        )
        if preferred_dimension == "geography":
            if not is_geography_table:
                continue
            dimension = "geography"
        elif is_segment_table:
            dimension = "business"
        else:
            continue

        if "in thousands" in visible:
            scale = 1 / 1000
        elif "in billions" in visible:
            scale = 1000
        else:
            scale = 1

        values: list[tuple[str, float]] = []
        for cells in _table_rows(table):
            if len(cells) < 2:
                continue
            label = cells[0].strip(" :")
            normalized_label = label.lower()
            if (
                not label
                or len(label) > 80
                or any(
                    term in normalized_label
                    for term in (
                        "total",
                        "net sales",
                        "revenue",
                        "gross margin",
                        "cost of",
                        "percentage",
                        "increase",
                        "decrease",
                        "year ended",
                    )
                )
            ):
                continue
            number = next(
                (
                    parsed
                    for parsed in (_cell_number(cell) for cell in cells[1:])
                    if parsed is not None and parsed > 0
                ),
                None,
            )
            if number is not None:
                values.append((label, number * scale))

        deduplicated: dict[str, float] = {}
        for label, value in values:
            deduplicated.setdefault(label, value)
        if not 2 <= len(deduplicated) <= 12:
            continue
        segment_total = sum(deduplicated.values())
        coverage = segment_total / total_revenue
        if not 1 - tolerance <= coverage <= 1 + tolerance:
            continue
        candidates.append(
            (
                abs(1 - coverage),
                dimension,
                list(deduplicated.items()),
                f"HTML年度收入表，覆盖总收入 {coverage:.1%}",
            )
        )

    if not candidates:
        return []

    selected_candidates = (
        [
            candidate
            for candidate in candidates
            if preferred_dimension and candidate[1] == preferred_dimension
        ]
        or candidates
    )
    _, dimension, values, evidence = min(
        selected_candidates,
        key=lambda item: item[0],
    )
    return [
        {
            "name": label,
            "revenue": round(revenue, 6),
            "description": "由最新年度报告收入表自动提取。",
            "evidence": f"{report_date or '最新财年'} {evidence}",
            "reported_growth": None,
            "base_growth": 0.06,
            "base_gross_margin": 0.45,
            "segment_dimension": dimension,
            "extraction_method": "annual_report_html_table",
        }
        for label, revenue in sorted(values, key=lambda item: item[1], reverse=True)
    ]
