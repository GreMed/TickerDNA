from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import re
from typing import Any


SECTION_HINTS = (
    "营业收入构成",
    "主营业务构成",
    "主营业务分行业",
    "主营业务分产品",
    "主营业务分地区",
    "分部信息",
    "报告分部",
    "分行业",
    "分产品",
    "分业务",
    "按业务",
    "按产品",
    "收入分部",
    "收入构成",
    "segment revenue",
    "revenue by segment",
    "revenue by business",
    "revenue by product",
    "disaggregation of revenue",
    "disaggregated revenue",
    "segment information",
)
DIMENSION_HINTS = {
    "分产品": "product",
    "按产品": "product",
    "by product": "product",
    "分业务": "business",
    "按业务": "business",
    "业务分部": "business",
    "分部信息": "business",
    "报告分部": "business",
    "by business": "business",
    "by segment": "business",
    "reportable segment": "business",
    "分行业": "industry",
    "按行业": "industry",
    "by industry": "industry",
    "分地区": "geography",
    "按地区": "geography",
    "geographical": "geography",
    "by region": "geography",
}
TOTAL_LABELS = {
    "合计",
    "总计",
    "营业收入合计",
    "主营业务收入合计",
    "total",
    "total revenue",
    "revenue",
    "revenues",
}
ROW_EXCLUSIONS = (
    "报告期内",
    "公司实现",
    "占营业收入",
    "同比增减",
    "毛利率",
    "营业成本",
    "gross margin",
    "cost of",
    "percentage",
    "year ended",
    "截至",
    "附注",
    "note",
    "本期数",
    "上期数",
    "本年发生额",
    "上年发生额",
)
NON_SEGMENT_LABEL_TERMS = (
    "年度报告全文",
    "财务报表主要项目注释",
    "适用",
    "不适用",
    "期末余额",
    "期初余额",
    "账面余额",
    "账面价值",
    "坏账准备",
    "单位名称",
    "客户名称",
    "供应商名称",
    "应收利息",
    "应收股利",
    "应收账款",
    "其他应收款",
    "货币资金",
    "资产总额",
    "负债总额",
    "利润总额",
    "所得税费用",
    "管理费用",
    "财务费用",
    "销售费用",
    "核销金额",
)
NUMBER_PATTERN = re.compile(
    r"(?<![\w])(?:\(\s*)?-?\d[\d,]*(?:\.\d+)?(?:\s*\))?(?![\w])"
)
ACCOUNTING_NUMBER_PATTERN = re.compile(
    r"(?<![\d,])(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}"
)
COMMA_ACCOUNTING_PATTERN = re.compile(
    r"\d{1,3}(?:,\d{3})+\.\d{2}"
)


@dataclass(frozen=True)
class ParsedRow:
    label: str
    current: float
    prior: float | None
    reported_growth: float | None = None
    reported_gross_margin: float | None = None
    reported_profit: float | None = None
    profit_metric_name: str = ""


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract layout-preserving text from a PDF with the optional pypdf dependency."""
    if not pdf_bytes:
        return ""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""

    try:
        reader = PdfReader(BytesIO(pdf_bytes))
    except Exception:
        return ""

    pages: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text(extraction_mode="layout") or ""
        except (TypeError, ValueError):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
        if text:
            pages.append(text)
    return "\n".join(pages)


def _normalize_lines(text: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", line.replace("\u00a0", " ")).strip()
        for line in text.splitlines()
        if line.strip()
    ]


def _has_label(line: str) -> bool:
    return bool(re.search(r"[A-Za-z\u3400-\u9fff]", line))


def _numeric_count(line: str) -> int:
    return len(NUMBER_PATTERN.findall(line))


def _is_standalone_row_label(line: str) -> bool:
    normalized = line.lower()
    structural_terms = (
        *SECTION_HINTS,
        *DIMENSION_HINTS.keys(),
        "单位",
        "币种",
        "rmb million",
        "hk$ million",
        "usd million",
        "营业收入",
        "营业成本",
        "毛利率",
        "本期",
        "上期",
        "本年",
        "上年",
    )
    return (
        _has_label(line)
        and _numeric_count(line) == 0
        and len(line) <= 60
        and not any(term in normalized for term in structural_terms)
        and not line.endswith(("：", ":"))
        and not line.endswith(("情况", "构成", "分析", "明细", "如下"))
    )


def _logical_lines(lines: list[str]) -> list[str]:
    """Repair common PDF row wrapping without assuming a company template."""
    repaired: list[str] = []
    pending_label = ""
    for line in lines:
        numeric_count = _numeric_count(line)
        if _is_standalone_row_label(line):
            if pending_label:
                repaired.append(pending_label)
            pending_label = line
            continue
        if pending_label and numeric_count:
            repaired.append(f"{pending_label} {line}")
            pending_label = ""
            continue
        if pending_label:
            repaired.append(pending_label)
            pending_label = ""
        repaired.append(line)
    if pending_label:
        repaired.append(pending_label)

    joined: list[str] = []
    for line in repaired:
        if (
            joined
            and _has_label(joined[-1])
            and _numeric_count(joined[-1]) == 1
            and not _has_label(line)
            and _numeric_count(line) >= 1
        ):
            joined[-1] = f"{joined[-1]} {line}"
        else:
            joined.append(line)
    return joined


def _unit_details(text: str) -> tuple[float, str]:
    normalized = text.lower().replace("港币", "港元")
    currency = (
        "港元百万元"
        if any(term in normalized for term in ("港元", "hk$", "hkd"))
        else "美元百万元"
        if any(term in normalized for term in ("美元", "us$", "usd"))
        else "人民币百万元"
    )

    chinese_match = re.search(
        r"(?:金额)?单位\s*(?:为|[:：])?\s*(人民币|港元|美元)?\s*"
        r"(亿元|百万元|万元|千元|元)",
        normalized,
    )
    if chinese_match:
        explicit_currency = chinese_match.group(1)
        if explicit_currency:
            currency = {
                "人民币": "人民币百万元",
                "港元": "港元百万元",
                "美元": "美元百万元",
            }[explicit_currency]
        elif chinese_match.group(2) == "元":
            currency = "人民币百万元"
        return {
            "亿元": 100.0,
            "百万元": 1.0,
            "万元": 0.01,
            "千元": 0.001,
            "元": 0.000001,
        }[chinese_match.group(2)], currency

    if re.search(r"(?:rmb|hk\$|hkd|us\$|usd)?\s*(?:million|mn)\b", normalized):
        return 1.0, currency
    if re.search(r"(?:in\s+)?(?:thousand|thousands|rmb'000|hk\$'000)", normalized):
        return 0.001, currency
    if re.search(r"(?:in\s+)?(?:billion|billions|bn)\b", normalized):
        return 1000.0, currency
    return 1.0, currency


def _parse_number(value: str) -> float | None:
    clean = value.replace(",", "").strip()
    negative = clean.startswith("(") and clean.endswith(")")
    clean = clean.strip("() ")
    try:
        number = float(clean)
    except ValueError:
        return None
    return -number if negative else number


def _line_values(line: str) -> tuple[str, list[float]]:
    comma_matches = list(COMMA_ACCOUNTING_PATTERN.finditer(line))
    if len(comma_matches) >= 2:
        label = line[: comma_matches[0].start()].strip(" :：·•*-")
        values = [
            float(match.group().replace(",", ""))
            for match in comma_matches[:2]
        ]
        remainder = line[comma_matches[1].end() :]
        values.extend(
            float(value)
            for value in re.findall(r"-?\d+(?:\.\d+)?", remainder)
        )
        return label, values

    accounting_matches = list(ACCOUNTING_NUMBER_PATTERN.finditer(line))
    matches = (
        accounting_matches
        if len(accounting_matches) >= 2
        else list(NUMBER_PATTERN.finditer(line))
    )
    if not matches:
        return line.strip(" :："), []

    first = matches[0]
    label = line[: first.start()].strip(" :：·•*-")
    values: list[float] = []
    for match in matches:
        token = match.group()
        suffix = line[match.end() : match.end() + 2]
        number = _parse_number(token)
        if number is None or "%" in suffix:
            continue
        if 1900 <= number <= 2100 and "," not in token and "." not in token:
            continue
        values.append(number)
    return label, values


def _clean_label(label: str) -> str:
    value = re.sub(r"^[（(]?\d+[）).、]\s*", "", label)
    if "增减（%）" in value:
        value = value.rsplit("增减（%）", 1)[-1]
    elif "增减(%)" in value:
        value = value.rsplit("增减(%)", 1)[-1]
    value = re.sub(r"^(?:分点|百分点)\s*", "", value)
    value = re.sub(r"\s+", " ", value).strip(" :：·•*-")
    return value


def _dimension(line: str) -> str | None:
    normalized = line.lower()
    for hint, dimension in DIMENSION_HINTS.items():
        if hint in normalized:
            return dimension
    return None


def _is_total(label: str) -> bool:
    normalized = re.sub(r"[\s:：]", "", label).lower()
    return normalized in {
        re.sub(r"[\s:：]", "", value).lower() for value in TOTAL_LABELS
    }


def _is_usable_row(label: str, values: list[float]) -> bool:
    normalized = label.lower()
    compact = re.sub(r"\s+", "", label)
    return (
        bool(label)
        and len(label) <= 80
        and (len(compact) >= 2 or compact.lower() == "other")
        and bool(values)
        and values[0] > 0
        and not any(term in normalized for term in ROW_EXCLUSIONS)
        and not any(term in normalized for term in NON_SEGMENT_LABEL_TERMS)
        and not any(symbol in label for symbol in ("□", "☑", "", "", "√"))
        and not normalized.startswith(("项目 ", "序号 ", "类别 "))
        and not re.fullmatch(r"[\d\s./年月日-]+", label)
        and not re.fullmatch(r"[（）()]+", label)
    )


def _gross_margin_hint(name: str) -> float:
    normalized = name.lower()
    if any(term in normalized for term in ("广告", "营销", "advertis", "marketing")):
        return 0.60
    if any(term in normalized for term in ("软件", "订阅", "服务", "software", "service")):
        return 0.55
    if any(term in normalized for term in ("金融", "支付", "fintech", "payment")):
        return 0.45
    if any(term in normalized for term in ("硬件", "设备", "汽车", "hardware", "device")):
        return 0.25
    return 0.40


def _profit_metric_label(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).lower()
    compact = re.sub(r"[\s（）()/-]", "", text).lower()
    if any(term in normalized for term in ("ebitda", "adjusted ebitda")):
        return "分部EBITDA"
    if any(
        term in normalized
        for term in (
            "operating income",
            "operating profit",
            "operating results",
            "segment operating profit",
        )
    ) or any(term in compact for term in ("营业利润", "经营利润")):
        return "分部营业利润"
    if any(
        term in normalized
        for term in (
            "profit before tax",
            "income before income taxes",
            "pretax income",
        )
    ) or any(term in compact for term in ("税前利润", "除税前利润")):
        return "分部税前利润"
    if any(term in normalized for term in ("net income", "net profit")) or any(
        term in compact for term in ("净利润", "净收益")
    ):
        return "分部净利润"
    if any(
        term in normalized
        for term in (
            "segment profit",
            "segment income",
            "profit/(loss)",
            "profit or loss",
            "results of operations",
        )
    ) or any(term in compact for term in ("分部利润", "报告分部利润", "分部业绩")):
        return "分部利润"
    return ""


def _has_revenue_hint(text: str) -> bool:
    normalized = text.lower()
    compact = re.sub(r"\s+", "", text)
    return any(
        term in normalized
        for term in ("revenue", "revenues", "sales", "income from operations")
    ) or any(term in compact for term in ("营业收入", "主营业务收入", "收入"))


def _is_section_start(line: str) -> bool:
    normalized = line.lower().strip()
    if "□适用" in normalized or "√不适用" in normalized:
        return False
    if "部分产品" in normalized or normalized.endswith("说明"):
        return False
    return any(hint in normalized for hint in SECTION_HINTS)


def _candidate_blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    starts = [
        index
        for index, line in enumerate(lines)
        if _is_section_start(line)
    ]
    blocks: list[tuple[str, list[str]]] = []
    for start in starts:
        dimension = _dimension(lines[start]) or "business"
        begin = max(0, start - 12)
        end = min(start + 60, len(lines))
        blocks.append((dimension, lines[begin:end]))
    return blocks


def _reported_total_millions(text: str) -> list[float]:
    normalized = re.sub(r"\s+", " ", text)
    pattern = re.compile(
        r"(?:主营业务收入|营业总收入|营业收入)"
        r"[^\d]{0,24}([\d,]+(?:\.\d+)?)\s*"
        r"(亿元|百万元|万元|千元|元)"
    )
    factors = {
        "亿元": 100.0,
        "百万元": 1.0,
        "万元": 0.01,
        "千元": 0.001,
        "元": 0.000001,
    }
    values: list[float] = []
    for amount, unit in pattern.findall(normalized):
        value = float(amount.replace(",", "")) * factors[unit]
        if value > 0 and all(abs(value - item) > 0.01 for item in values):
            values.append(value)
    return values


def _groups_from_block(
    default_dimension: str, lines: list[str]
) -> list[tuple[str, list[ParsedRow], float]]:
    groups: list[tuple[str, list[ParsedRow], float]] = []
    dimension = default_dimension
    rows: list[ParsedRow] = []
    revenue_total = 0.0
    cost_table = False
    profit_metric_name = ""
    profit_value_index: int | None = None
    share_table = any(
        any(term in line for term in ("占营业收入", "营业收入占比", "收入占比"))
        and any(term in line for term in ("上年", "同期", "上期"))
        for line in lines[:40]
    )

    for line in lines:
        line_profit_metric = _profit_metric_label(line)
        if line_profit_metric and _has_revenue_hint(line):
            profit_metric_name = line_profit_metric
            if "营业成本" in line and "毛利率" in line:
                profit_value_index = 3
            else:
                profit_value_index = 1

        if "营业成本" in line and "毛利率" in line:
            cost_table = True
            continue

        line_dimension = _dimension(line)
        if line_dimension:
            if rows and line_dimension != dimension:
                groups.append((dimension, rows, revenue_total))
                rows = []
            dimension = line_dimension
            if _numeric_count(line) < 2:
                continue

        label, values = _line_values(line)
        label = _clean_label(label)
        if _is_total(label) and values:
            compact_label = re.sub(r"[\s:：]", "", label)
            if compact_label in {"营业收入合计", "主营业务收入合计"}:
                revenue_total = values[0]
            if len(rows) >= 2:
                groups.append((dimension, rows, values[0]))
            rows = []
            continue
        if not _is_usable_row(label, values):
            continue

        reported_growth = None
        reported_gross_margin = None
        reported_profit = None
        if cost_table:
            prior = None
            if len(values) >= 3 and 0 <= values[2] <= 100:
                reported_gross_margin = values[2] / 100
            if len(values) >= 4 and -100 <= values[3] <= 500:
                reported_growth = values[3] / 100
            if (
                profit_metric_name
                and profit_value_index is not None
                and len(values) > profit_value_index
            ):
                reported_profit = values[profit_value_index]
        elif share_table and "%" not in line and len(values) >= 3:
            prior = values[2] if values[2] > 0 else None
            if len(values) >= 5 and -100 <= values[4] <= 500:
                reported_growth = values[4] / 100
        elif profit_metric_name and profit_value_index is not None:
            reported_profit = (
                values[profit_value_index]
                if len(values) > profit_value_index
                else None
            )
            prior_index = 2 if profit_value_index == 1 else 1
            prior = (
                values[prior_index]
                if len(values) >= 4 and values[prior_index] > 0
                else None
            )
        else:
            prior = next((value for value in values[1:] if value > 0), None)
        rows.append(
            ParsedRow(
                label=label,
                current=values[0],
                prior=prior,
                reported_growth=reported_growth,
                reported_gross_margin=reported_gross_margin,
                reported_profit=reported_profit,
                profit_metric_name=profit_metric_name if reported_profit is not None else "",
            )
        )
        if len(rows) > 15:
            rows = rows[-15:]
    if len(rows) >= 2:
        groups.append((dimension, rows, revenue_total))
    return groups


def _normalized_segment_name(value: str) -> str:
    return re.sub(r"[\s（）()：:、·•*+\-_/]", "", value).lower()


def _reported_segment_margins(
    lines: list[str],
) -> list[tuple[str, str, float]]:
    margins: list[tuple[str, str, float]] = []
    seen: set[tuple[str, str, float]] = set()
    for default_dimension, block in _candidate_blocks(lines):
        for dimension, rows, _ in _groups_from_block(default_dimension, block):
            for row in rows:
                if row.reported_gross_margin is None:
                    continue
                item = (
                    dimension,
                    _normalized_segment_name(row.label),
                    row.reported_gross_margin,
                )
                if item not in seen:
                    seen.add(item)
                    margins.append(item)
    return margins


def _is_usable_metric_row(label: str, values: list[float]) -> bool:
    normalized = label.lower()
    compact = re.sub(r"\s+", "", label)
    return (
        bool(label)
        and bool(values)
        and len(label) <= 80
        and (len(compact) >= 2 or compact.lower() == "other")
        and not _is_total(label)
        and not any(term in normalized for term in ROW_EXCLUSIONS)
        and not any(term in normalized for term in NON_SEGMENT_LABEL_TERMS)
        and not any(symbol in label for symbol in ("□", "☑", "", "", "√"))
        and not normalized.startswith(("项目 ", "序号 ", "类别 "))
        and not re.fullmatch(r"[\d\s./年月日-]+", label)
    )


def _reported_segment_profit_metrics(
    lines: list[str],
) -> list[tuple[str, str, str, float]]:
    metrics: list[tuple[str, str, str, float]] = []
    seen: set[tuple[str, str, str, float]] = set()
    for default_dimension, block in _candidate_blocks(lines):
        dimension = default_dimension
        scale, _ = _unit_details("\n".join(block))
        metric_label = ""
        metric_has_revenue = False
        for line in block:
            line_dimension = _dimension(line)
            if line_dimension:
                dimension = line_dimension

            line_metric = _profit_metric_label(line)
            if line_metric:
                metric_label = line_metric
                metric_has_revenue = _has_revenue_hint(line)
                if _numeric_count(line) < 1:
                    continue
            if not metric_label:
                continue

            row_label, values = _line_values(line)
            row_label = _clean_label(row_label)
            if not _is_usable_metric_row(row_label, values):
                continue
            profit_index = 1 if metric_has_revenue else 0
            if len(values) <= profit_index:
                continue
            item = (
                dimension,
                _normalized_segment_name(row_label),
                metric_label,
                values[profit_index] * scale,
            )
            if item not in seen:
                seen.add(item)
                metrics.append(item)
    return metrics


def _match_reported_segment_margin(
    name: str,
    dimension: str,
    margins: list[tuple[str, str, float]],
) -> float | None:
    normalized = _normalized_segment_name(name)
    exact = [
        margin
        for item_dimension, item_name, margin in margins
        if item_dimension == dimension and item_name == normalized
    ]
    if exact:
        return exact[0]

    prefix_matches = [
        (len(item_name), margin)
        for item_dimension, item_name, margin in margins
        if item_dimension == dimension
        and min(len(item_name), len(normalized)) >= 4
        and (
            item_name.startswith(normalized)
            or normalized.startswith(item_name)
        )
    ]
    if prefix_matches:
        return max(prefix_matches, key=lambda item: item[0])[1]
    return None


def _match_reported_segment_profit_metric(
    name: str,
    dimension: str,
    metrics: list[tuple[str, str, str, float]],
) -> tuple[str, float] | None:
    normalized = _normalized_segment_name(name)
    compatible_dimensions = (
        {"product", "business"}
        if dimension in {"product", "business"}
        else {dimension}
    )
    exact = [
        (metric_label, value)
        for item_dimension, item_name, metric_label, value in metrics
        if item_dimension in compatible_dimensions and item_name == normalized
    ]
    if exact:
        return exact[0]

    prefix_matches = [
        (len(item_name), metric_label, value)
        for item_dimension, item_name, metric_label, value in metrics
        if item_dimension in compatible_dimensions
        and min(len(item_name), len(normalized)) >= 4
        and (
            item_name.startswith(normalized)
            or normalized.startswith(item_name)
        )
    ]
    if prefix_matches:
        _, metric_label, value = max(prefix_matches, key=lambda item: item[0])
        return metric_label, value
    return None


def _statement_line_values(line: str) -> tuple[str, list[float]]:
    matches = list(NUMBER_PATTERN.finditer(line))
    amount_matches = []
    for match in matches:
        token = match.group()
        suffix = line[match.end() : match.end() + 2]
        number = _parse_number(token)
        if number is None or "%" in suffix:
            continue
        if 1900 <= number <= 2100 and "," not in token and "." not in token:
            continue
        if "," in token or "." in token or abs(number) >= 100:
            amount_matches.append((match, number))
    if not amount_matches:
        return line, []
    first = amount_matches[0][0]
    label = line[: first.start()].strip(" :：·•*-")
    return label, [number for _, number in amount_matches]


def _statement_label(value: str) -> str:
    label = re.sub(
        r"^[一二三四五六七八九十]+[、.．]\s*",
        "",
        value,
    )
    label = re.sub(r"^(?:其中|减|加)\s*[：:]\s*", "", label)
    label = re.sub(r"\s+", "", label)
    label = re.sub(r"[（(].*$", "", label)
    label = re.sub(r"[一二三四五六七八九十]+$", "", label)
    return label


def extract_pdf_company_metrics(
    text: str,
    expected_total_revenue: float | None = None,
) -> dict[str, Any]:
    """Extract consolidated revenue, gross profit and net profit from a PDF."""
    if not text:
        return {}

    lines = _normalize_lines(text)
    starts = [
        index
        for index, line in enumerate(lines)
        if "合并利润表" in line or "consolidated income statement" in line.lower()
    ]
    candidates: list[dict[str, Any]] = []
    for start in starts:
        window = lines[start : min(start + 180, len(lines))]
        scale, currency = _unit_details("\n".join(window[:30]))
        revenue = None
        cost = None
        net_profit = None
        for line in window:
            normalized_line = line.lower()
            if (
                line != window[0]
                and (
                    "母公司利润表" in line
                    or "合并现金流量表" in line
                    or "consolidated statement of cash flows" in normalized_line
                )
            ):
                break
            label, values = _statement_line_values(line)
            if not values:
                continue
            normalized = _statement_label(label).lower()
            if revenue is None and normalized in {
                "营业总收入",
                "营业收入",
                "revenue",
                "revenues",
                "totalrevenue",
            }:
                revenue = values[0]
            elif (
                cost is None
                and normalized in {"营业成本", "costofrevenue", "costofsales"}
            ):
                cost = values[0]
            elif (
                net_profit is None
                and normalized in {"净利润", "netprofit", "netincome"}
            ):
                net_profit = values[0]

        if revenue is None or revenue <= 0:
            continue
        total_revenue = revenue * scale
        gross_profit = (
            (revenue - cost) * scale
            if cost is not None and 0 <= cost <= revenue * 1.5
            else None
        )
        net_profit_value = net_profit * scale if net_profit is not None else None
        score = (
            abs(total_revenue - expected_total_revenue) / expected_total_revenue
            if expected_total_revenue
            else 0
        )
        candidates.append(
            {
                "score": score,
                "currency": currency,
                "total_revenue": total_revenue,
                "gross_profit": gross_profit,
                "gross_margin": (
                    gross_profit / total_revenue
                    if gross_profit is not None and total_revenue
                    else None
                ),
                "net_profit": net_profit_value,
                "net_margin": (
                    net_profit_value / total_revenue
                    if net_profit_value is not None and total_revenue
                    else None
                ),
            }
        )

    if not candidates:
        return {}
    result = min(candidates, key=lambda item: item["score"])
    result.pop("score", None)
    return {
        key: round(value, 6) if isinstance(value, float) else value
        for key, value in result.items()
    }


def _revenue_note_candidates(
    lines: list[str],
    report_date: str,
    tolerance: float,
) -> list[tuple[float, str, list[ParsedRow], float, float, str]]:
    candidates: list[
        tuple[float, str, list[ParsedRow], float, float, str]
    ] = []
    for index, line in enumerate(lines):
        normalized = re.sub(r"[\s:：]", "", line)
        if normalized not in {"主营业务收入", "主营业务收入明细"}:
            continue

        context = lines[max(0, index - 15) : min(len(lines), index + 40)]
        scale, currency = _unit_details("\n".join(context))
        rows: list[ParsedRow] = []
        total = 0.0
        for row_line in lines[index + 1 : min(len(lines), index + 35)]:
            row_label, values = _line_values(row_line)
            row_label = _clean_label(row_label)
            normalized_label = re.sub(r"[\s:：]", "", row_label)
            if normalized_label in {
                "其他业务收入",
                "其他营业收入",
                "主营业务收入",
            }:
                continue
            if _is_total(row_label) and values:
                total = values[0]
                break
            if not _is_usable_row(row_label, values):
                continue
            prior = next((value for value in values[1:] if value > 0), None)
            rows.append(
                ParsedRow(
                    label=row_label,
                    current=values[0],
                    prior=prior,
                )
            )

        if total <= 0 or not 2 <= len(rows) <= 20:
            continue
        segment_total = sum(row.current for row in rows)
        coverage = segment_total / total
        if not 1 - tolerance <= coverage <= 1 + tolerance:
            continue
        candidates.append(
            (
                -0.2 + abs(1 - coverage),
                "business",
                rows,
                total,
                scale,
                currency,
            )
        )
    return candidates


def extract_pdf_revenue_segments(
    text: str,
    report_date: str = "",
    tolerance: float = 0.12,
    preferred_dimension: str | None = None,
) -> dict[str, Any] | None:
    """Find a reported revenue split in Chinese or English annual-report text."""
    if not text:
        return None

    lines = _logical_lines(_normalize_lines(text))
    candidates: list[
        tuple[float, str, list[ParsedRow], float, float, str]
    ] = []
    dimension_penalty = {
        "business": 0.0,
        "product": 0.01,
        "industry": 0.06,
        "geography": 0.12,
    }
    candidates.extend(
        _revenue_note_candidates(lines, report_date, tolerance)
    )
    for default_dimension, block in _candidate_blocks(lines):
        reported_totals = _reported_total_millions("\n".join(block))
        scale, currency = _unit_details("\n".join(block))
        for dimension, rows, total in _groups_from_block(default_dimension, block):
            deduplicated: dict[str, ParsedRow] = {}
            for row in rows:
                deduplicated.setdefault(row.label, row)
            values = list(deduplicated.values())
            if not 2 <= len(values) <= 12:
                continue
            segment_total = sum(row.current for row in values)
            if total <= 0:
                matches = [
                    reported_total / scale
                    for reported_total in reported_totals
                    if 1 - tolerance
                    <= segment_total / (reported_total / scale)
                    <= 1 + tolerance
                ]
                if not matches:
                    continue
                total = min(
                    matches,
                    key=lambda item: abs(1 - segment_total / item),
                )
                if segment_total / total >= 0.985:
                    total = segment_total
            coverage = segment_total / total
            if not 1 - tolerance <= coverage <= 1 + tolerance:
                continue
            score = abs(1 - coverage) + dimension_penalty.get(dimension, 0.08)
            candidates.append((score, dimension, values, total, scale, currency))

    if not candidates:
        return None

    selected_candidates = (
        [
            candidate
            for candidate in candidates
            if preferred_dimension and candidate[1] == preferred_dimension
        ]
        or candidates
    )
    _, dimension, rows, total, scale, currency = min(
        selected_candidates, key=lambda item: item[0]
    )
    total_revenue = total * scale
    disclosed_margins = _reported_segment_margins(lines)
    disclosed_profit_metrics = _reported_segment_profit_metrics(lines)
    segments: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: item.current, reverse=True):
        revenue = row.current * scale
        reported_growth = row.reported_growth
        if (
            reported_growth is None
            and row.prior is not None
            and row.prior > 0
        ):
            reported_growth = row.current / row.prior - 1
        base_growth = (
            min(max(reported_growth * 0.5, -0.05), 0.25)
            if reported_growth is not None
            else 0.06
        )
        reported_gross_margin = (
            row.reported_gross_margin
            if row.reported_gross_margin is not None
            else _match_reported_segment_margin(
                row.label,
                dimension,
                disclosed_margins,
            )
        )
        base_gross_margin = (
            reported_gross_margin
            if reported_gross_margin is not None
            else _gross_margin_hint(row.label)
        )
        reported_profit = (
            row.reported_profit * scale
            if row.reported_profit is not None
            else None
        )
        profit_metric_name = row.profit_metric_name
        if reported_profit is None:
            matched_profit_metric = _match_reported_segment_profit_metric(
                row.label,
                dimension,
                disclosed_profit_metrics,
            )
            if matched_profit_metric:
                profit_metric_name, reported_profit = matched_profit_metric
        supplemental_metrics = []
        if reported_profit is not None:
            supplemental_metrics.append(
                {
                    "name": profit_metric_name or "分部利润",
                    "value": round(reported_profit, 6),
                    "margin": reported_profit / revenue if revenue else None,
                    "basis": "reported",
                }
            )
        segments.append(
            {
                "name": row.label,
                "revenue": round(revenue, 6),
                "description": f"由年度报告的 {dimension} 收入表自动提取。",
                "evidence": (
                    f"{report_date or '最新财年'} PDF收入表，"
                    f"分部合计覆盖总收入 "
                    f"{sum(item.current for item in rows) / total:.1%}"
                ),
                "reported_growth": reported_growth,
                "reported_gross_margin": reported_gross_margin,
                "base_growth": base_growth,
                "base_gross_margin": base_gross_margin,
                "gross_margin_basis": (
                    "reported"
                    if reported_gross_margin is not None
                    else "estimated"
                ),
                "reported_profit": (
                    round(reported_profit, 6)
                    if reported_profit is not None
                    else None
                ),
                "reported_profit_margin": (
                    reported_profit / revenue
                    if reported_profit is not None and revenue
                    else None
                ),
                "profit_metric_name": profit_metric_name if reported_profit is not None else "",
                "profit_metric_basis": "reported" if reported_profit is not None else "",
                "supplemental_metrics": supplemental_metrics,
                "extraction_method": "annual_report_pdf_table",
            }
        )
    result = {
        "total_revenue": round(total_revenue, 6),
        "currency": currency,
        "segments": segments,
        "dimension": dimension,
        "available_dimensions": sorted({candidate[1] for candidate in candidates}),
    }
    result.update(
        extract_pdf_company_metrics(
            text,
            expected_total_revenue=total_revenue,
        )
    )
    result["total_revenue"] = round(total_revenue, 6)
    result["currency"] = currency
    return result
