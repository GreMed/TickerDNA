from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Protocol

import requests

from modeling.a_share_disclosures import AShareBusinessCompositionProvider
from modeling.company_data import (
    CompanyCandidate,
    sec_headers,
    sec_user_agent_is_configured,
)
from modeling.market_announcements import (
    CninfoAnnouncementProvider,
    HkexAnnouncementProvider,
    OfficialAnnouncement,
    fetch_document_text,
)
from modeling.pdf_disclosures import (
    extract_pdf_company_metrics,
    extract_pdf_revenue_segments,
    extract_pdf_text,
)
from modeling.segment_extractor import (
    extract_inline_xbrl_segment_data,
    extract_revenue_table_segments,
)


CACHE_DIR = Path(
    os.getenv(
        "FM_DATA_CACHE_DIR",
        str(Path(__file__).resolve().parents[1] / ".cache" / "company_data"),
    )
)
ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A"}
REVENUE_CONCEPTS = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "Revenue",
)
GROSS_PROFIT_CONCEPTS = ("GrossProfit",)
COST_OF_REVENUE_CONCEPTS = (
    "CostOfRevenue",
    "CostOfGoodsAndServicesSold",
    "CostOfGoodsSold",
    "CostOfSales",
)
NET_PROFIT_CONCEPTS = (
    "NetIncomeLoss",
    "ProfitLoss",
)


@dataclass
class DisclosurePacket:
    provider: str
    status: str
    company_name: str
    symbol: str
    market: str = ""
    fiscal_year: str = ""
    currency: str = ""
    total_revenue: float | None = None
    gross_profit: float | None = None
    gross_margin: float | None = None
    net_profit: float | None = None
    net_margin: float | None = None
    segments: list[dict[str, Any]] = field(default_factory=list)
    segment_dimension: str = ""
    available_dimensions: list[str] = field(default_factory=list)
    requested_dimension: str = ""
    facts: list[dict[str, Any]] = field(default_factory=list)
    filings: list[dict[str, str]] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # Phase 12B-1 收口：按财年索引的独立公司总收入（来自合并利润表/财务摘要，非分部相加）
    company_financial_totals: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Phase 12B-1 收口：按财年索引的分部合计（F10 分部相加，非公司总收入）
    segment_historical_totals: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Phase 12B-1 收口：原始历史分部池（保留旧口径分部，不做模糊匹配）
    raw_historical_segment_pool: list[dict[str, Any]] = field(default_factory=list)

    @property
    def source_category(self) -> str:
        """来源类型分类，用于 UI 展示，避免用户混淆内置快照与实时抓取。

        分类规则：
        - 实时官方披露抓取：SEC EDGAR、香港交易所披露易、巨潮资讯（年报 PDF 实时抓取）
        - 上传官方 PDF：上传的官方年度报告
        - 结构化 F10 / 公告平台：公开F10主营构成
        - 内置官方快照：内置官方快照（静态快照，非实时）
        - 无匹配 Provider / 估算：无匹配 Provider 或不支持
        - 混合来源：多个 Provider 组合时
        """
        name = self.provider or ""
        if " + " in name:
            return "混合来源"
        if name == "内置官方快照":
            return "内置官方快照"
        if name == "上传的官方年度报告":
            return "上传官方 PDF"
        if name in {"SEC EDGAR", "香港交易所披露易", "巨潮资讯"}:
            return "实时官方披露抓取"
        if "F10" in name or "主营构成" in name:
            return "结构化 F10 / 公告平台"
        if "无匹配" in name or self.status == "unsupported":
            return "无匹配 Provider / 估算"
        return "其他来源"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_prompt(self) -> str:
        lines = [
            f"官方披露 Provider：{self.provider}",
            f"状态：{self.status}",
            f"公司：{self.company_name}（{self.symbol}）",
        ]
        if self.fiscal_year:
            lines.append(f"最新完整财年：{self.fiscal_year}")
        if self.total_revenue is not None:
            lines.append(f"总收入：{self.total_revenue:.2f} {self.currency}")
        if self.gross_margin is not None:
            lines.append(f"公司毛利率：{self.gross_margin:.2%}")
        if self.net_margin is not None:
            lines.append(f"公司净利率：{self.net_margin:.2%}")
        for segment in self.segments:
            margin = segment.get("reported_gross_margin")
            margin_text = (
                f"，披露毛利率 {float(margin):.2%}"
                if margin is not None
                else ""
            )
            profit_value = segment.get("reported_profit")
            profit_margin = segment.get("reported_profit_margin")
            profit_text = ""
            if profit_value is not None:
                profit_text = (
                    f"，{segment.get('profit_metric_name') or '披露利润'} "
                    f"{float(profit_value):.2f}"
                )
                if profit_margin is not None:
                    profit_text += f"，对应利润率 {float(profit_margin):.2%}"
            lines.append(
                f"披露分部：{segment.get('name')}，收入 {segment.get('revenue')} "
                f"{self.currency}{margin_text}{profit_text}，依据："
                f"{segment.get('evidence', '')}"
            )
        if self.segment_dimension:
            lines.append(f"当前分部口径：{self.segment_dimension}")
        if self.available_dimensions:
            lines.append(f"可用分部口径：{', '.join(self.available_dimensions)}")
        if self.requested_dimension:
            lines.append(f"用户请求分部口径：{self.requested_dimension}")
        for fact in self.facts:
            lines.append(
                f"事实：{fact.get('label')} = {fact.get('value')} "
                f"{fact.get('unit')}，期间 {fact.get('period')}"
            )
        for filing in self.filings:
            lines.append(
                f"文件：{filing.get('form')}，报告期 {filing.get('report_date')}，"
                f"{filing.get('url')}"
            )
        for note in self.notes:
            lines.append(f"说明：{note}")
        return "\n".join(lines)


class DisclosureProvider(Protocol):
    name: str

    def supports(self, company: CompanyCandidate) -> bool:
        ...

    def fetch(
        self,
        company: CompanyCandidate,
        preferred_dimension: str | None = None,
    ) -> DisclosurePacket:
        ...


def _cache_json(name: str, url: str, ttl_hours: float) -> dict[str, Any]:
    path = CACHE_DIR / name
    if path.exists():
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if datetime.now(timezone.utc) - modified <= timedelta(hours=ttl_hours):
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass

    try:
        response = requests.get(
            url,
            headers=sec_headers("data.sec.gov"),
            timeout=float(os.getenv("COMPANY_API_TIMEOUT_SECONDS", "12")),
        )
        response.raise_for_status()
        payload = response.json()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload
    except (requests.RequestException, ValueError, OSError):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}


def _cache_text(name: str, url: str, ttl_hours: float) -> str:
    path = CACHE_DIR / name
    if path.exists():
        modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        if datetime.now(timezone.utc) - modified <= timedelta(hours=ttl_hours):
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                pass

    try:
        response = requests.get(
            url,
            headers=sec_headers("www.sec.gov"),
            timeout=float(os.getenv("COMPANY_API_TIMEOUT_SECONDS", "12")) * 2,
        )
        response.raise_for_status()
        text = response.text
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return text
    except (requests.RequestException, OSError):
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""


def _latest_annual_filing(
    submissions: dict[str, Any], cik: str
) -> dict[str, str] | None:
    recent = submissions.get("filings", {}).get("recent", {})
    keys = ("form", "accessionNumber", "primaryDocument", "reportDate", "filingDate")
    columns = [recent.get(key, []) for key in keys]
    for form, accession, document, report_date, filing_date in zip(*columns):
        if form not in ANNUAL_FORMS:
            continue
        accession_compact = str(accession).replace("-", "")
        cik_compact = str(int(cik))
        url = (
            f"https://www.sec.gov/Archives/edgar/data/{cik_compact}/"
            f"{accession_compact}/{document}"
        )
        return {
            "form": str(form),
            "accession_number": str(accession),
            "report_date": str(report_date),
            "filing_date": str(filing_date),
            "url": url,
        }
    return None


def _latest_annual_revenue(
    companyfacts: dict[str, Any],
) -> tuple[float, str, str, dict[str, Any]] | None:
    taxonomies = companyfacts.get("facts", {})
    candidates: list[tuple[str, dict[str, Any]]] = []
    for taxonomy_name in ("us-gaap", "ifrs-full"):
        taxonomy = taxonomies.get(taxonomy_name, {})
        for concept in REVENUE_CONCEPTS:
            if concept in taxonomy:
                candidates.append((concept, taxonomy[concept]))

    best: tuple[
        str, dict[str, Any], tuple[str, str, str], dict[str, Any]
    ] | None = None
    for concept, fact in candidates:
        for unit, values in fact.get("units", {}).items():
            for value in values:
                if value.get("form") not in ANNUAL_FORMS:
                    continue
                if value.get("fp") not in {"FY", None}:
                    continue
                fiscal_year = str(value.get("fy") or "")
                period_end = str(value.get("end") or "")
                filed = str(value.get("filed", ""))
                score = (fiscal_year, period_end, filed)
                if best is None or score > best[2]:
                    best = (concept, fact, score, {**value, "unit": unit})

    if best is None:
        return None
    concept, fact, _, value = best
    raw_value = float(value.get("val", 0))
    unit = str(value.get("unit", "USD"))
    fiscal_year = str(value.get("fy") or value.get("end") or "")
    details = {
        "concept": concept,
        "label": fact.get("label", "Revenue"),
        "value": raw_value,
        "unit": unit,
        "period": value.get("end", fiscal_year),
        "form": value.get("form", ""),
        "filed": value.get("filed", ""),
    }
    return raw_value / 1_000_000, f"{unit}百万元", fiscal_year, details


def _annual_monetary_fact(
    companyfacts: dict[str, Any],
    concepts: tuple[str, ...],
    *,
    period_end: str,
    fiscal_year: str,
    unit: str,
) -> tuple[float, dict[str, Any]] | None:
    taxonomies = companyfacts.get("facts", {})
    for concept in concepts:
        candidates: list[tuple[tuple[int, str], dict[str, Any], dict[str, Any]]] = []
        for taxonomy_name in ("us-gaap", "ifrs-full"):
            fact = taxonomies.get(taxonomy_name, {}).get(concept)
            if not fact:
                continue
            units = fact.get("units", {})
            unit_values = units.get(unit, [])
            if not unit_values and len(units) == 1:
                unit_values = next(iter(units.values()))
            for value in unit_values:
                if value.get("form") not in ANNUAL_FORMS:
                    continue
                if value.get("fp") not in {"FY", None}:
                    continue
                end = str(value.get("end") or "")
                fy = str(value.get("fy") or "")
                if period_end and end != period_end:
                    continue
                if fiscal_year and fy and fy != fiscal_year:
                    continue
                score = (
                    1 if str(value.get("frame") or "").endswith(period_end[:4]) else 0,
                    str(value.get("filed") or ""),
                )
                candidates.append((score, fact, value))
        if not candidates:
            continue
        _, fact, value = max(candidates, key=lambda item: item[0])
        amount = float(value.get("val", 0)) / 1_000_000
        return amount, {
            "concept": concept,
            "label": fact.get("label", concept),
            "value": amount,
            "unit": f"{unit}百万元",
            "period": value.get("end", period_end),
            "form": value.get("form", ""),
            "filed": value.get("filed", ""),
        }
    return None


def _annual_company_metrics(
    companyfacts: dict[str, Any],
    revenue: tuple[float, str, str, dict[str, Any]] | None,
) -> dict[str, Any]:
    if not revenue:
        return {}
    total_revenue, _, fiscal_year, revenue_detail = revenue
    if total_revenue <= 0:
        return {}

    period_end = str(revenue_detail.get("period") or "")
    unit = str(revenue_detail.get("unit") or "USD")
    gross_fact = _annual_monetary_fact(
        companyfacts,
        GROSS_PROFIT_CONCEPTS,
        period_end=period_end,
        fiscal_year=fiscal_year,
        unit=unit,
    )
    cost_fact = None
    if gross_fact is None:
        cost_fact = _annual_monetary_fact(
            companyfacts,
            COST_OF_REVENUE_CONCEPTS,
            period_end=period_end,
            fiscal_year=fiscal_year,
            unit=unit,
        )
    net_fact = _annual_monetary_fact(
        companyfacts,
        NET_PROFIT_CONCEPTS,
        period_end=period_end,
        fiscal_year=fiscal_year,
        unit=unit,
    )

    gross_profit = (
        gross_fact[0]
        if gross_fact is not None
        else total_revenue - cost_fact[0]
        if cost_fact is not None
        else None
    )
    net_profit = net_fact[0] if net_fact is not None else None
    return {
        "gross_profit": gross_profit,
        "gross_margin": (
            gross_profit / total_revenue
            if gross_profit is not None
            else None
        ),
        "net_profit": net_profit,
        "net_margin": (
            net_profit / total_revenue
            if net_profit is not None
            else None
        ),
        "facts": [
            detail
            for detail in (
                gross_fact[1] if gross_fact else cost_fact[1] if cost_fact else None,
                net_fact[1] if net_fact else None,
            )
            if detail
        ],
    }


class SecEdgarDisclosureProvider:
    name = "SEC EDGAR"

    def supports(self, company: CompanyCandidate) -> bool:
        return bool(company.cik)

    def fetch(
        self,
        company: CompanyCandidate,
        preferred_dimension: str | None = None,
    ) -> DisclosurePacket:
        cik = company.cik.zfill(10)
        ttl = float(os.getenv("SEC_DISCLOSURE_CACHE_TTL_HOURS", "6"))
        submissions_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        facts_url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        sources = [
            {"title": "SEC submissions API", "url": submissions_url},
            {"title": "SEC company facts API", "url": facts_url},
        ]
        if not sec_user_agent_is_configured():
            return DisclosurePacket(
                provider=self.name,
                status="configuration_required",
                company_name=company.name,
                symbol=company.symbol,
                market=company.exchange_name or company.exchange,
                sources=sources,
                notes=[
                    "请在 `.env` 中配置 SEC_USER_AGENT，包含应用名称和联系邮箱。"
                ],
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            submissions_future = executor.submit(
                _cache_json,
                f"sec_submissions_{cik}.json",
                submissions_url,
                ttl,
            )
            facts_future = executor.submit(
                _cache_json,
                f"sec_companyfacts_{cik}.json",
                facts_url,
                ttl,
            )
            submissions = submissions_future.result()
            companyfacts = facts_future.result()

        filing = _latest_annual_filing(submissions, cik) if submissions else None
        if filing:
            sources.append({"title": f"SEC {filing['form']} filing", "url": filing["url"]})
        revenue = _latest_annual_revenue(companyfacts) if companyfacts else None
        company_metrics = _annual_company_metrics(companyfacts, revenue)

        if not submissions and not companyfacts:
            return DisclosurePacket(
                provider=self.name,
                status="unavailable",
                company_name=company.name,
                symbol=company.symbol,
                market=company.exchange_name or company.exchange,
                sources=sources,
                notes=["当前网络无法访问 SEC；如已有缓存会自动使用缓存。"],
            )

        total_revenue = None
        currency = ""
        fiscal_year = filing.get("report_date", "")[:4] if filing else ""
        facts: list[dict[str, Any]] = []
        if revenue:
            total_revenue, currency, revenue_year, detail = revenue
            fiscal_year = revenue_year or fiscal_year
            detail["value"] = total_revenue
            detail["unit"] = currency
            facts.append(detail)
            facts.extend(company_metrics.get("facts", []))

        segments: list[dict[str, Any]] = []
        segment_dimension = ""
        available_dimensions: list[str] = []
        extraction_note = ""
        if filing and total_revenue:
            filing_html = _cache_text(
                f"sec_filing_{cik}_{filing['accession_number'].replace('-', '')}.html",
                filing["url"],
                ttl,
            )
            if filing_html:
                inline_data = extract_inline_xbrl_segment_data(
                    filing_html,
                    total_revenue,
                    report_date=filing.get("report_date", ""),
                    preferred_dimension=preferred_dimension,
                )
                segments = inline_data.segments
                segment_dimension = inline_data.dimension
                available_dimensions = inline_data.available_dimensions
                if segments:
                    extraction_note = (
                        "业务拆分由 SEC Inline XBRL 维度事实自动提取并与总收入校验。"
                    )
                else:
                    segments = extract_revenue_table_segments(
                        filing_html,
                        total_revenue,
                        report_date=filing.get("report_date", ""),
                        preferred_dimension=preferred_dimension,
                    )
                    if segments:
                        segment_dimension = str(
                            segments[0].get("segment_dimension", "business")
                        )
                        available_dimensions = sorted(
                            {*available_dimensions, segment_dimension}
                        )
                        extraction_note = (
                            "业务拆分由 10-K/20-F 年度收入表自动提取并与总收入校验。"
                        )

        return DisclosurePacket(
            provider=self.name,
            status="ready",
            company_name=str(submissions.get("name") or company.name),
            symbol=company.symbol,
            market=company.exchange_name or company.exchange,
            fiscal_year=fiscal_year,
            currency=currency,
            total_revenue=total_revenue,
            gross_profit=company_metrics.get("gross_profit"),
            gross_margin=company_metrics.get("gross_margin"),
            net_profit=company_metrics.get("net_profit"),
            net_margin=company_metrics.get("net_margin"),
            segments=segments,
            segment_dimension=segment_dimension,
            available_dimensions=available_dimensions,
            requested_dimension=preferred_dimension or "",
            facts=facts,
            filings=[filing] if filing else [],
            sources=sources,
            notes=[
                "SEC Company Facts 仅聚合实体层面的标准 XBRL 事实；"
                "业务分部会继续从年报 Inline XBRL 或 HTML 表格中自动提取。",
                extraction_note
                or "本次未找到能与总收入校验通过的披露分部，已保留失败状态。",
            ],
        )


class CuratedOfficialDisclosureProvider:
    name = "内置官方快照"

    SNAPSHOTS: dict[str, dict[str, Any]] = {
        "AAPL": {
            "company_name": "Apple Inc.",
            "fiscal_year": "2025",
            "currency": "美元百万元",
            "segment_dimension": "product",
            "total_revenue": 416_161,
            "segments": [
                {
                    "name": "iPhone",
                    "revenue": 209_586,
                    "description": "iPhone 产品系列及相关配件。",
                    "evidence": "FY2025 10-K Note 2 Products and Services Performance 披露净销售额 USD209,586 百万元",
                    "reported_growth": 0.04,
                    "base_growth": 0.04,
                    "base_gross_margin": 0.39,
                    "historical_periods": [
                        {"fiscal_year": "2023", "revenue": 200_583, "gross_margin": None,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
                         "revenue_publication_date": "2025-10-31", "revenue_page_or_table": "Note 2 Products and Services Performance",
                         "revenue_source_name": "Apple FY2025 Form 10-K（含 FY2023 比较期）",
                         "gross_margin_nature": "missing", "gross_margin_channel": "", "gross_margin_url": "",
                         "gross_margin_publication_date": "未记录", "gross_margin_page_or_table": "", "gross_margin_source_name": "",
                         "comparability_key": "iphone_product", "comparability_note": "",
                         "currency": "USD", "unit": "million"},
                        {"fiscal_year": "2024", "revenue": 201_183, "gross_margin": None,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
                         "revenue_publication_date": "2025-10-31", "revenue_page_or_table": "Note 2 Products and Services Performance",
                         "revenue_source_name": "Apple FY2025 Form 10-K（含 FY2024 比较期）",
                         "gross_margin_nature": "missing", "gross_margin_channel": "", "gross_margin_url": "",
                         "gross_margin_publication_date": "未记录", "gross_margin_page_or_table": "", "gross_margin_source_name": "",
                         "comparability_key": "iphone_product", "comparability_note": "",
                         "currency": "USD", "unit": "million"},
                        {"fiscal_year": "2025", "revenue": 209_586, "gross_margin": None,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
                         "revenue_publication_date": "2025-10-31", "revenue_page_or_table": "Note 2 Products and Services Performance",
                         "revenue_source_name": "Apple FY2025 Form 10-K",
                         "gross_margin_nature": "missing", "gross_margin_channel": "", "gross_margin_url": "",
                         "gross_margin_publication_date": "未记录", "gross_margin_page_or_table": "", "gross_margin_source_name": "",
                         "comparability_key": "iphone_product", "comparability_note": "",
                         "currency": "USD", "unit": "million"},
                    ],
                },
                {
                    "name": "Mac",
                    "revenue": 33_708,
                    "description": "MacBook、iMac、Mac mini、Mac Studio 等 Mac 产品。",
                    "evidence": "FY2025 10-K Note 2 披露净销售额 USD33,708 百万元",
                    "reported_growth": 0.12,
                    "base_growth": 0.07,
                    "base_gross_margin": 0.38,
                    "historical_periods": [
                        {"fiscal_year": "2023", "revenue": 29_357, "gross_margin": None,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
                         "revenue_publication_date": "2025-10-31", "revenue_page_or_table": "Note 2 Products and Services Performance",
                         "revenue_source_name": "Apple FY2025 Form 10-K（含 FY2023 比较期）",
                         "gross_margin_nature": "missing", "gross_margin_channel": "", "gross_margin_url": "",
                         "gross_margin_publication_date": "未记录", "gross_margin_page_or_table": "", "gross_margin_source_name": "",
                         "comparability_key": "mac_product", "comparability_note": "",
                         "currency": "USD", "unit": "million"},
                        {"fiscal_year": "2024", "revenue": 29_984, "gross_margin": None,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
                         "revenue_publication_date": "2025-10-31", "revenue_page_or_table": "Note 2 Products and Services Performance",
                         "revenue_source_name": "Apple FY2025 Form 10-K（含 FY2024 比较期）",
                         "gross_margin_nature": "missing", "gross_margin_channel": "", "gross_margin_url": "",
                         "gross_margin_publication_date": "未记录", "gross_margin_page_or_table": "", "gross_margin_source_name": "",
                         "comparability_key": "mac_product", "comparability_note": "",
                         "currency": "USD", "unit": "million"},
                        {"fiscal_year": "2025", "revenue": 33_708, "gross_margin": None,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
                         "revenue_publication_date": "2025-10-31", "revenue_page_or_table": "Note 2 Products and Services Performance",
                         "revenue_source_name": "Apple FY2025 Form 10-K",
                         "gross_margin_nature": "missing", "gross_margin_channel": "", "gross_margin_url": "",
                         "gross_margin_publication_date": "未记录", "gross_margin_page_or_table": "", "gross_margin_source_name": "",
                         "comparability_key": "mac_product", "comparability_note": "",
                         "currency": "USD", "unit": "million"},
                    ],
                },
                {
                    "name": "iPad",
                    "revenue": 28_023,
                    "description": "iPad 产品系列及相关配件。",
                    "evidence": "FY2025 10-K Note 2 披露净销售额 USD28,023 百万元",
                    "reported_growth": 0.05,
                    "base_growth": 0.05,
                    "base_gross_margin": 0.36,
                    "historical_periods": [
                        {"fiscal_year": "2023", "revenue": 28_300, "gross_margin": None,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
                         "revenue_publication_date": "2025-10-31", "revenue_page_or_table": "Note 2 Products and Services Performance",
                         "revenue_source_name": "Apple FY2025 Form 10-K（含 FY2023 比较期）",
                         "gross_margin_nature": "missing", "gross_margin_channel": "", "gross_margin_url": "",
                         "gross_margin_publication_date": "未记录", "gross_margin_page_or_table": "", "gross_margin_source_name": "",
                         "comparability_key": "ipad_product", "comparability_note": "",
                         "currency": "USD", "unit": "million"},
                        {"fiscal_year": "2024", "revenue": 26_694, "gross_margin": None,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
                         "revenue_publication_date": "2025-10-31", "revenue_page_or_table": "Note 2 Products and Services Performance",
                         "revenue_source_name": "Apple FY2025 Form 10-K（含 FY2024 比较期）",
                         "gross_margin_nature": "missing", "gross_margin_channel": "", "gross_margin_url": "",
                         "gross_margin_publication_date": "未记录", "gross_margin_page_or_table": "", "gross_margin_source_name": "",
                         "comparability_key": "ipad_product", "comparability_note": "",
                         "currency": "USD", "unit": "million"},
                        {"fiscal_year": "2025", "revenue": 28_023, "gross_margin": None,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
                         "revenue_publication_date": "2025-10-31", "revenue_page_or_table": "Note 2 Products and Services Performance",
                         "revenue_source_name": "Apple FY2025 Form 10-K",
                         "gross_margin_nature": "missing", "gross_margin_channel": "", "gross_margin_url": "",
                         "gross_margin_publication_date": "未记录", "gross_margin_page_or_table": "", "gross_margin_source_name": "",
                         "comparability_key": "ipad_product", "comparability_note": "",
                         "currency": "USD", "unit": "million"},
                    ],
                },
                {
                    "name": "可穿戴、家居及配件",
                    "revenue": 35_686,
                    "description": "Apple Watch、AirPods、Vision Pro、Apple TV、Beats 等。",
                    "evidence": "FY2025 10-K Note 2 披露净销售额 USD35,686 百万元",
                    "reported_growth": -0.04,
                    "base_growth": 0.03,
                    "base_gross_margin": 0.37,
                    "historical_periods": [
                        {"fiscal_year": "2023", "revenue": 39_845, "gross_margin": None,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
                         "revenue_publication_date": "2025-10-31", "revenue_page_or_table": "Note 2 Products and Services Performance",
                         "revenue_source_name": "Apple FY2025 Form 10-K（含 FY2023 比较期）",
                         "gross_margin_nature": "missing", "gross_margin_channel": "", "gross_margin_url": "",
                         "gross_margin_publication_date": "未记录", "gross_margin_page_or_table": "", "gross_margin_source_name": "",
                         "comparability_key": "wearables_product", "comparability_note": "",
                         "currency": "USD", "unit": "million"},
                        {"fiscal_year": "2024", "revenue": 37_005, "gross_margin": None,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
                         "revenue_publication_date": "2025-10-31", "revenue_page_or_table": "Note 2 Products and Services Performance",
                         "revenue_source_name": "Apple FY2025 Form 10-K（含 FY2024 比较期）",
                         "gross_margin_nature": "missing", "gross_margin_channel": "", "gross_margin_url": "",
                         "gross_margin_publication_date": "未记录", "gross_margin_page_or_table": "", "gross_margin_source_name": "",
                         "comparability_key": "wearables_product", "comparability_note": "",
                         "currency": "USD", "unit": "million"},
                        {"fiscal_year": "2025", "revenue": 35_686, "gross_margin": None,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
                         "revenue_publication_date": "2025-10-31", "revenue_page_or_table": "Note 2 Products and Services Performance",
                         "revenue_source_name": "Apple FY2025 Form 10-K",
                         "gross_margin_nature": "missing", "gross_margin_channel": "", "gross_margin_url": "",
                         "gross_margin_publication_date": "未记录", "gross_margin_page_or_table": "", "gross_margin_source_name": "",
                         "comparability_key": "wearables_product", "comparability_note": "",
                         "currency": "USD", "unit": "million"},
                    ],
                },
                {
                    "name": "服务",
                    "revenue": 109_158,
                    "description": (
                        "广告、AppleCare、云服务、数字内容、支付及其他服务。"
                    ),
                    "evidence": "FY2025 10-K Note 2 披露净销售额 USD109,158 百万元",
                    "reported_growth": 0.14,
                    "base_growth": 0.11,
                    "base_gross_margin": 0.75,
                    "historical_periods": [
                        {"fiscal_year": "2023", "revenue": 85_200, "gross_margin": None,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
                         "revenue_publication_date": "2025-10-31", "revenue_page_or_table": "Note 2 Products and Services Performance",
                         "revenue_source_name": "Apple FY2025 Form 10-K（含 FY2023 比较期）",
                         "gross_margin_nature": "missing", "gross_margin_channel": "", "gross_margin_url": "",
                         "gross_margin_publication_date": "未记录", "gross_margin_page_or_table": "", "gross_margin_source_name": "",
                         "comparability_key": "services_product", "comparability_note": "",
                         "currency": "USD", "unit": "million"},
                        {"fiscal_year": "2024", "revenue": 96_169, "gross_margin": None,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
                         "revenue_publication_date": "2025-10-31", "revenue_page_or_table": "Note 2 Products and Services Performance",
                         "revenue_source_name": "Apple FY2025 Form 10-K（含 FY2024 比较期）",
                         "gross_margin_nature": "missing", "gross_margin_channel": "", "gross_margin_url": "",
                         "gross_margin_publication_date": "未记录", "gross_margin_page_or_table": "", "gross_margin_source_name": "",
                         "comparability_key": "services_product", "comparability_note": "",
                         "currency": "USD", "unit": "million"},
                        {"fiscal_year": "2025", "revenue": 109_158, "gross_margin": None,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
                         "revenue_publication_date": "2025-10-31", "revenue_page_or_table": "Note 2 Products and Services Performance",
                         "revenue_source_name": "Apple FY2025 Form 10-K",
                         "gross_margin_nature": "missing", "gross_margin_channel": "", "gross_margin_url": "",
                         "gross_margin_publication_date": "未记录", "gross_margin_page_or_table": "", "gross_margin_source_name": "",
                         "comparability_key": "services_product", "comparability_note": "",
                         "currency": "USD", "unit": "million"},
                    ],
                },
            ],
            "actuals": {
                "gross_profit": 195_201,
                "selling_marketing_expense": 0,
                "general_admin_expense": 0,
                "operating_expense": 62_151,
                "profit_before_tax": 132_729,
                "income_tax": 20_719,
                "net_profit": 112_010,
            },
            "sources": [
                {
                    "title": "Apple FY2025 Form 10-K",
                    "url": (
                        "https://www.sec.gov/Archives/edgar/data/320193/"
                        "000032019325000079/aapl-20250927.htm"
                    ),
                },
                {
                    "title": "SEC company facts API",
                    "url": (
                        "https://data.sec.gov/api/xbrl/companyfacts/"
                        "CIK0000320193.json"
                    ),
                },
                {
                    "title": "Apple Investor Relations",
                    "url": "https://investor.apple.com/",
                },
            ],
            "published_date": "2025-10-31",
        },
        "0700.HK": {
            "company_name": "腾讯控股有限公司",
            "fiscal_year": "2025",
            "currency": "人民币百万元",
            "segment_dimension": "business",
            "total_revenue": 751_766,
            "segments": [
                {
                    "name": "增值服务",
                    "revenue": 369_281,
                    "description": (
                        "国内游戏、国际游戏及社交网络，包括视频、音乐订阅和直播等。"
                    ),
                    "evidence": "FY2025 年报收入及毛利表披露收入 RMB369,281 百万元",
                    "reported_growth": 0.16,
                    "base_growth": 0.10,
                    "base_gross_margin": 0.60,
                    "historical_periods": [
                        {"fiscal_year": "2023", "revenue": 298_375, "gross_margin": 0.54,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://static.www.tencent.com/uploads/2025/04/08/0706a9085e70140122364ded872455ca.pdf",
                         "revenue_publication_date": "2025-04-08", "revenue_page_or_table": "收入及毛利表",
                         "revenue_source_name": "腾讯 FY2024 年度报告（含 FY2023 比较期）",
                         "gross_margin_nature": "reported", "gross_margin_channel": "snapshot",
                         "gross_margin_url": "https://static.www.tencent.com/uploads/2025/04/08/0706a9085e70140122364ded872455ca.pdf",
                         "gross_margin_publication_date": "2025-04-08", "gross_margin_page_or_table": "收入及毛利表",
                         "gross_margin_source_name": "腾讯 FY2024 年度报告（含 FY2023 比较期）",
                         "comparability_key": "vas_v1", "comparability_note": "",
                         "currency": "CNY", "unit": "million"},
                        {"fiscal_year": "2024", "revenue": 319_168, "gross_margin": 0.57,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://static.www.tencent.com/uploads/2025/04/08/0706a9085e70140122364ded872455ca.pdf",
                         "revenue_publication_date": "2025-04-08", "revenue_page_or_table": "收入及毛利表",
                         "revenue_source_name": "腾讯 FY2024 年度报告",
                         "gross_margin_nature": "reported", "gross_margin_channel": "snapshot",
                         "gross_margin_url": "https://static.www.tencent.com/uploads/2025/04/08/0706a9085e70140122364ded872455ca.pdf",
                         "gross_margin_publication_date": "2025-04-08", "gross_margin_page_or_table": "收入及毛利表",
                         "gross_margin_source_name": "腾讯 FY2024 年度报告",
                         "comparability_key": "vas_v1", "comparability_note": "",
                         "currency": "CNY", "unit": "million"},
                        {"fiscal_year": "2025", "revenue": 369_281, "gross_margin": 0.60,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://static.www.tencent.com/uploads/2026/04/09/62d786fcf3d3c8cb7e54791ee95439ac.pdf",
                         "revenue_publication_date": "2026-04-09", "revenue_page_or_table": "收入及毛利表",
                         "revenue_source_name": "腾讯 FY2025 年度报告",
                         "gross_margin_nature": "reported", "gross_margin_channel": "snapshot",
                         "gross_margin_url": "https://static.www.tencent.com/uploads/2026/04/09/62d786fcf3d3c8cb7e54791ee95439ac.pdf",
                         "gross_margin_publication_date": "2026-04-09", "gross_margin_page_or_table": "收入及毛利表",
                         "gross_margin_source_name": "腾讯 FY2025 年度报告",
                         "comparability_key": "vas_v1", "comparability_note": "",
                         "currency": "CNY", "unit": "million"},
                    ],
                },
                {
                    "name": "营销服务",
                    "revenue": 144_973,
                    "description": (
                        "视频号、微信搜一搜、小程序及其他腾讯生态内的广告和营销服务。"
                    ),
                    "evidence": "FY2025 年报收入及毛利表披露收入 RMB144,973 百万元",
                    "reported_growth": 0.19,
                    "base_growth": 0.13,
                    "base_gross_margin": 0.58,
                    "historical_periods": [
                        {"fiscal_year": "2023", "revenue": 101_482, "gross_margin": 0.51,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://static.www.tencent.com/uploads/2025/04/08/0706a9085e70140122364ded872455ca.pdf",
                         "revenue_publication_date": "2025-04-08", "revenue_page_or_table": "收入及毛利表",
                         "revenue_source_name": "腾讯 FY2024 年度报告（FY2023 口径 Online Advertising）",
                         "gross_margin_nature": "reported", "gross_margin_channel": "snapshot",
                         "gross_margin_url": "https://static.www.tencent.com/uploads/2025/04/08/0706a9085e70140122364ded872455ca.pdf",
                         "gross_margin_publication_date": "2025-04-08", "gross_margin_page_or_table": "收入及毛利表",
                         "gross_margin_source_name": "腾讯 FY2024 年度报告（FY2023 口径 Online Advertising）",
                         "comparability_key": "online_advertising_v1", "comparability_note": "FY2023 名为 Online Advertising，FY2024 起改名 Marketing Services；口径发生变化，不跨口径计算同比/CAGR",
                         "currency": "CNY", "unit": "million"},
                        {"fiscal_year": "2024", "revenue": 121_374, "gross_margin": 0.55,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://static.www.tencent.com/uploads/2025/04/08/0706a9085e70140122364ded872455ca.pdf",
                         "revenue_publication_date": "2025-04-08", "revenue_page_or_table": "收入及毛利表",
                         "revenue_source_name": "腾讯 FY2024 年度报告",
                         "gross_margin_nature": "reported", "gross_margin_channel": "snapshot",
                         "gross_margin_url": "https://static.www.tencent.com/uploads/2025/04/08/0706a9085e70140122364ded872455ca.pdf",
                         "gross_margin_publication_date": "2025-04-08", "gross_margin_page_or_table": "收入及毛利表",
                         "gross_margin_source_name": "腾讯 FY2024 年度报告",
                         "comparability_key": "marketing_services_v2", "comparability_note": "",
                         "currency": "CNY", "unit": "million"},
                        {"fiscal_year": "2025", "revenue": 144_973, "gross_margin": 0.58,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://static.www.tencent.com/uploads/2026/04/09/62d786fcf3d3c8cb7e54791ee95439ac.pdf",
                         "revenue_publication_date": "2026-04-09", "revenue_page_or_table": "收入及毛利表",
                         "revenue_source_name": "腾讯 FY2025 年度报告",
                         "gross_margin_nature": "reported", "gross_margin_channel": "snapshot",
                         "gross_margin_url": "https://static.www.tencent.com/uploads/2026/04/09/62d786fcf3d3c8cb7e54791ee95439ac.pdf",
                         "gross_margin_publication_date": "2026-04-09", "gross_margin_page_or_table": "收入及毛利表",
                         "gross_margin_source_name": "腾讯 FY2025 年度报告",
                         "comparability_key": "marketing_services_v2", "comparability_note": "",
                         "currency": "CNY", "unit": "million"},
                    ],
                },
                {
                    "name": "金融科技及企业服务",
                    "revenue": 229_435,
                    "description": (
                        "商业支付、理财与消费金融服务，以及云服务和企业数字化服务。"
                    ),
                    "evidence": "FY2025 年报收入及毛利表披露收入 RMB229,435 百万元",
                    "reported_growth": 0.08,
                    "base_growth": 0.10,
                    "base_gross_margin": 0.51,
                    "historical_periods": [
                        {"fiscal_year": "2023", "revenue": 203_763, "gross_margin": 0.40,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://static.www.tencent.com/uploads/2025/04/08/0706a9085e70140122364ded872455ca.pdf",
                         "revenue_publication_date": "2025-04-08", "revenue_page_or_table": "收入及毛利表",
                         "revenue_source_name": "腾讯 FY2024 年度报告（含 FY2023 比较期）",
                         "gross_margin_nature": "reported", "gross_margin_channel": "snapshot",
                         "gross_margin_url": "https://static.www.tencent.com/uploads/2025/04/08/0706a9085e70140122364ded872455ca.pdf",
                         "gross_margin_publication_date": "2025-04-08", "gross_margin_page_or_table": "收入及毛利表",
                         "gross_margin_source_name": "腾讯 FY2024 年度报告（含 FY2023 比较期）",
                         "comparability_key": "fintech_v1", "comparability_note": "",
                         "currency": "CNY", "unit": "million"},
                        {"fiscal_year": "2024", "revenue": 211_956, "gross_margin": 0.47,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://static.www.tencent.com/uploads/2025/04/08/0706a9085e70140122364ded872455ca.pdf",
                         "revenue_publication_date": "2025-04-08", "revenue_page_or_table": "收入及毛利表",
                         "revenue_source_name": "腾讯 FY2024 年度报告",
                         "gross_margin_nature": "reported", "gross_margin_channel": "snapshot",
                         "gross_margin_url": "https://static.www.tencent.com/uploads/2025/04/08/0706a9085e70140122364ded872455ca.pdf",
                         "gross_margin_publication_date": "2025-04-08", "gross_margin_page_or_table": "收入及毛利表",
                         "gross_margin_source_name": "腾讯 FY2024 年度报告",
                         "comparability_key": "fintech_v1", "comparability_note": "",
                         "currency": "CNY", "unit": "million"},
                        {"fiscal_year": "2025", "revenue": 229_435, "gross_margin": 0.51,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://static.www.tencent.com/uploads/2026/04/09/62d786fcf3d3c8cb7e54791ee95439ac.pdf",
                         "revenue_publication_date": "2026-04-09", "revenue_page_or_table": "收入及毛利表",
                         "revenue_source_name": "腾讯 FY2025 年度报告",
                         "gross_margin_nature": "reported", "gross_margin_channel": "snapshot",
                         "gross_margin_url": "https://static.www.tencent.com/uploads/2026/04/09/62d786fcf3d3c8cb7e54791ee95439ac.pdf",
                         "gross_margin_publication_date": "2026-04-09", "gross_margin_page_or_table": "收入及毛利表",
                         "gross_margin_source_name": "腾讯 FY2025 年度报告",
                         "comparability_key": "fintech_v1", "comparability_note": "",
                         "currency": "CNY", "unit": "million"},
                    ],
                },
                {
                    "name": "其他",
                    "revenue": 8_077,
                    "description": "不归属于主要业务分部的其他收入。",
                    "evidence": "FY2025 年报收入及毛利表披露收入 RMB8,077 百万元",
                    "reported_growth": 0.041,
                    "base_growth": 0.03,
                    "base_gross_margin": 0.04,
                    "historical_periods": [
                        {"fiscal_year": "2023", "revenue": 5_395, "gross_margin": -0.15,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://static.www.tencent.com/uploads/2025/04/08/0706a9085e70140122364ded872455ca.pdf",
                         "revenue_publication_date": "2025-04-08", "revenue_page_or_table": "收入及毛利表",
                         "revenue_source_name": "腾讯 FY2024 年度报告（含 FY2023 比较期）",
                         "gross_margin_nature": "reported", "gross_margin_channel": "snapshot",
                         "gross_margin_url": "https://static.www.tencent.com/uploads/2025/04/08/0706a9085e70140122364ded872455ca.pdf",
                         "gross_margin_publication_date": "2025-04-08", "gross_margin_page_or_table": "收入及毛利表",
                         "gross_margin_source_name": "腾讯 FY2024 年度报告（含 FY2023 比较期）",
                         "comparability_key": "others_v1", "comparability_note": "",
                         "currency": "CNY", "unit": "million"},
                        {"fiscal_year": "2024", "revenue": 7_759, "gross_margin": 0.08,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://static.www.tencent.com/uploads/2025/04/08/0706a9085e70140122364ded872455ca.pdf",
                         "revenue_publication_date": "2025-04-08", "revenue_page_or_table": "收入及毛利表",
                         "revenue_source_name": "腾讯 FY2024 年度报告",
                         "gross_margin_nature": "reported", "gross_margin_channel": "snapshot",
                         "gross_margin_url": "https://static.www.tencent.com/uploads/2025/04/08/0706a9085e70140122364ded872455ca.pdf",
                         "gross_margin_publication_date": "2025-04-08", "gross_margin_page_or_table": "收入及毛利表",
                         "gross_margin_source_name": "腾讯 FY2024 年度报告",
                         "comparability_key": "others_v1", "comparability_note": "",
                         "currency": "CNY", "unit": "million"},
                        {"fiscal_year": "2025", "revenue": 8_077, "gross_margin": 0.04,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "https://static.www.tencent.com/uploads/2026/04/09/62d786fcf3d3c8cb7e54791ee95439ac.pdf",
                         "revenue_publication_date": "2026-04-09", "revenue_page_or_table": "收入及毛利表",
                         "revenue_source_name": "腾讯 FY2025 年度报告",
                         "gross_margin_nature": "reported", "gross_margin_channel": "snapshot",
                         "gross_margin_url": "https://static.www.tencent.com/uploads/2026/04/09/62d786fcf3d3c8cb7e54791ee95439ac.pdf",
                         "gross_margin_publication_date": "2026-04-09", "gross_margin_page_or_table": "收入及毛利表",
                         "gross_margin_source_name": "腾讯 FY2025 年度报告",
                         "comparability_key": "others_v1", "comparability_note": "",
                         "currency": "CNY", "unit": "million"},
                    ],
                },
            ],
            "geography_segments": [
                {
                    "name": "中国大陆",
                    "revenue": 662_119,
                    "description": "按客户所在地划分的中国大陆收入。",
                    "evidence": (
                        "FY2025 年报 Note 6，Revenues by geographical location: "
                        "Chinese Mainland RMB662,119 百万元"
                    ),
                    "reported_growth": 0.112,
                    "base_growth": 0.08,
                    "base_gross_margin": 0.562,
                },
                {
                    "name": "其他地区",
                    "revenue": 89_647,
                    "description": "按客户所在地划分的中国大陆以外收入。",
                    "evidence": (
                        "FY2025 年报 Note 6，Revenues by geographical location: "
                        "Others RMB89,647 百万元"
                    ),
                    "reported_growth": 0.383,
                    "base_growth": 0.16,
                    "base_gross_margin": 0.562,
                },
            ],
            "actuals": {
                "gross_profit": 422_593,
                "selling_marketing_expense": 41_727,
                "general_admin_expense": 136_127,
                "profit_before_tax": 277_249,
                "income_tax": 47_448,
                "net_profit": 229_801,
            },
            "sources": [
                {
                    "title": "腾讯 FY2024 年度报告",
                    "url": (
                        "https://static.www.tencent.com/uploads/2025/04/08/"
                        "0706a9085e70140122364ded872455ca.pdf"
                    ),
                },
                {
                    "title": "腾讯 FY2025 年度及第四季度业绩公告",
                    "url": (
                        "https://static.www.tencent.com/uploads/2026/03/18/"
                        "e6a646796d0d869acc76271c9ee1a6a5.pdf"
                    ),
                },
                {
                    "title": "腾讯 FY2025 年度报告",
                    "url": (
                        "https://static.www.tencent.com/uploads/2026/04/09/"
                        "62d786fcf3d3c8cb7e54791ee95439ac.pdf"
                    ),
                },
                {
                    "title": "腾讯投资者关系：财务新闻",
                    "url": "https://www.tencent.com/zh-cn/investors/financial-news.html",
                },
                {
                    "title": "HKEXnews 披露易",
                    "url": (
                        "https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=zh"
                    ),
                },
            ],
            "published_date": "2026-03-18",
        },
        "600519.SS": {
            "company_name": "贵州茅台酒股份有限公司",
            "fiscal_year": "2024",
            "currency": "人民币百万元",
            "segment_dimension": "product",
            "total_revenue": 174_144,
            "segments": [
                {
                    "name": "茅台酒",
                    "revenue": 145_928.076,
                    "description": "飞天茅台、五星茅台、年份酒、生肖酒等茅台酒产品。",
                    "evidence": "FY2024 年报主营业务分产品表披露茅台酒收入 RMB145,928.076 百万元",
                    "reported_growth": 0.1528,
                    "base_growth": 0.12,
                    "base_gross_margin": 0.9406,
                    "historical_periods": [
                        {"fiscal_year": "2022", "revenue": 107_833.685, "gross_margin": 0.9419,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "http://www.cninfo.com.cn/new/disclosure/detail?stockCode=600519&announcementId=1216281757",
                         "revenue_publication_date": "2023-03-31", "revenue_page_or_table": "主营业务分产品",
                         "revenue_source_name": "贵州茅台 FY2022 年度报告",
                         "gross_margin_nature": "reported", "gross_margin_channel": "snapshot",
                         "gross_margin_url": "http://www.cninfo.com.cn/new/disclosure/detail?stockCode=600519&announcementId=1216281757",
                         "gross_margin_publication_date": "2023-03-31", "gross_margin_page_or_table": "主营业务分产品",
                         "gross_margin_source_name": "贵州茅台 FY2022 年度报告",
                         "comparability_key": "moutai_product_v1", "comparability_note": "",
                         "currency": "CNY", "unit": "million"},
                        {"fiscal_year": "2023", "revenue": 126_589.067, "gross_margin": 0.9412,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "http://www.cninfo.com.cn/new/disclosure/detail?stockCode=600519&announcementId=1219506510",
                         "revenue_publication_date": "2024-04-03", "revenue_page_or_table": "主营业务分产品",
                         "revenue_source_name": "贵州茅台 FY2023 年度报告",
                         "gross_margin_nature": "reported", "gross_margin_channel": "snapshot",
                         "gross_margin_url": "http://www.cninfo.com.cn/new/disclosure/detail?stockCode=600519&announcementId=1219506510",
                         "gross_margin_publication_date": "2024-04-03", "gross_margin_page_or_table": "主营业务分产品",
                         "gross_margin_source_name": "贵州茅台 FY2023 年度报告",
                         "comparability_key": "moutai_product_v1", "comparability_note": "",
                         "currency": "CNY", "unit": "million"},
                        {"fiscal_year": "2024", "revenue": 145_928.076, "gross_margin": 0.9406,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "http://www.cninfo.com.cn/new/disclosure/detail?stockCode=600519&announcementId=1222993920",
                         "revenue_publication_date": "2025-04-03", "revenue_page_or_table": "主营业务分产品",
                         "revenue_source_name": "贵州茅台 FY2024 年度报告",
                         "gross_margin_nature": "reported", "gross_margin_channel": "snapshot",
                         "gross_margin_url": "http://www.cninfo.com.cn/new/disclosure/detail?stockCode=600519&announcementId=1222993920",
                         "gross_margin_publication_date": "2025-04-03", "gross_margin_page_or_table": "主营业务分产品",
                         "gross_margin_source_name": "贵州茅台 FY2024 年度报告",
                         "comparability_key": "moutai_product_v1", "comparability_note": "",
                         "currency": "CNY", "unit": "million"},
                    ],
                },
                {
                    "name": "系列酒",
                    "revenue": 24_683.762,
                    "description": "茅台王子酒、迎宾酒、赖茅、汉酱等系列酒产品。",
                    "evidence": "FY2024 年报主营业务分产品表披露系列酒收入 RMB24,683.762 百万元",
                    "reported_growth": 0.1966,
                    "base_growth": 0.15,
                    "base_gross_margin": 0.7987,
                    "historical_periods": [
                        {"fiscal_year": "2022", "revenue": 15_938.647, "gross_margin": 0.7722,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "http://www.cninfo.com.cn/new/disclosure/detail?stockCode=600519&announcementId=1216281757",
                         "revenue_publication_date": "2023-03-31", "revenue_page_or_table": "主营业务分产品",
                         "revenue_source_name": "贵州茅台 FY2022 年度报告",
                         "gross_margin_nature": "reported", "gross_margin_channel": "snapshot",
                         "gross_margin_url": "http://www.cninfo.com.cn/new/disclosure/detail?stockCode=600519&announcementId=1216281757",
                         "gross_margin_publication_date": "2023-03-31", "gross_margin_page_or_table": "主营业务分产品",
                         "gross_margin_source_name": "贵州茅台 FY2022 年度报告",
                         "comparability_key": "series_wine_v2", "comparability_note": "FY2022-FY2024 年报称「系列酒」，F10 口径称「其他系列酒」；已通过显式别名合并，口径基本可比",
                         "currency": "CNY", "unit": "million"},
                        {"fiscal_year": "2023", "revenue": 20_629.930, "gross_margin": 0.7976,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "http://www.cninfo.com.cn/new/disclosure/detail?stockCode=600519&announcementId=1219506510",
                         "revenue_publication_date": "2024-04-03", "revenue_page_or_table": "主营业务分产品",
                         "revenue_source_name": "贵州茅台 FY2023 年度报告",
                         "gross_margin_nature": "reported", "gross_margin_channel": "snapshot",
                         "gross_margin_url": "http://www.cninfo.com.cn/new/disclosure/detail?stockCode=600519&announcementId=1219506510",
                         "gross_margin_publication_date": "2024-04-03", "gross_margin_page_or_table": "主营业务分产品",
                         "gross_margin_source_name": "贵州茅台 FY2023 年度报告",
                         "comparability_key": "series_wine_v2", "comparability_note": "",
                         "currency": "CNY", "unit": "million"},
                        {"fiscal_year": "2024", "revenue": 24_683.762, "gross_margin": 0.7987,
                         "revenue_nature": "reported", "revenue_channel": "snapshot",
                         "revenue_url": "http://www.cninfo.com.cn/new/disclosure/detail?stockCode=600519&announcementId=1222993920",
                         "revenue_publication_date": "2025-04-03", "revenue_page_or_table": "主营业务分产品",
                         "revenue_source_name": "贵州茅台 FY2024 年度报告",
                         "gross_margin_nature": "reported", "gross_margin_channel": "snapshot",
                         "gross_margin_url": "http://www.cninfo.com.cn/new/disclosure/detail?stockCode=600519&announcementId=1222993920",
                         "gross_margin_publication_date": "2025-04-03", "gross_margin_page_or_table": "主营业务分产品",
                         "gross_margin_source_name": "贵州茅台 FY2024 年度报告",
                         "comparability_key": "series_wine_v2", "comparability_note": "",
                         "currency": "CNY", "unit": "million"},
                    ],
                },
                {
                    "name": "其他业务",
                    "revenue": 3_561,
                    "description": "不归属于茅台酒和系列酒的其他收入。",
                    "evidence": "FY2024 年报披露其他业务收入",
                    "reported_growth": 0.05,
                    "base_growth": 0.03,
                    "base_gross_margin": 0.30,
                },
            ],
            "actuals": {
                "gross_profit": 162_447,
                "selling_marketing_expense": 0,
                "general_admin_expense": 0,
                "operating_expense": 0,
                "profit_before_tax": 119_617,
                "income_tax": 30_098,
                "net_profit": 89_519,
            },
            "sources": [
                {
                    "title": "贵州茅台 FY2024 年度报告",
                    "url": "http://www.cninfo.com.cn/new/disclosure/detail?stockCode=600519&announcementId=1222993920",
                },
                {
                    "title": "贵州茅台 FY2023 年度报告",
                    "url": "http://www.cninfo.com.cn/new/disclosure/detail?stockCode=600519&announcementId=1219506510",
                },
                {
                    "title": "贵州茅台 FY2022 年度报告",
                    "url": "http://www.cninfo.com.cn/new/disclosure/detail?stockCode=600519&announcementId=1216281757",
                },
                {
                    "title": "巨潮资讯网",
                    "url": "http://www.cninfo.com.cn/new/disclosure/stock?stockCode=600519&orgId=gssh0600519",
                },
            ],
            "published_date": "2025-04-03",
        }
    }

    def supports(self, company: CompanyCandidate) -> bool:
        return company.symbol.upper() in self.SNAPSHOTS

    def fetch(
        self,
        company: CompanyCandidate,
        preferred_dimension: str | None = None,
    ) -> DisclosurePacket:
        snapshot = self.SNAPSHOTS[company.symbol.upper()]
        actuals = snapshot["actuals"]
        default_dimension = (
            "product" if company.symbol.upper() == "AAPL" else "business"
        )
        available_dimensions = [default_dimension]
        if "geography_segments" in snapshot:
            available_dimensions.append("geography")
        segment_dimension = (
            "geography"
            if preferred_dimension == "geography"
            and "geography_segments" in snapshot
            else default_dimension
        )
        segments = (
            snapshot["geography_segments"]
            if segment_dimension == "geography"
            else snapshot["segments"]
        )

        # Phase 13：根据 historical_periods 中的 gross_margin_nature 设置
        # segment 级别的 gross_margin_basis 和 reported_gross_margin。
        # 腾讯年报披露分部毛利率（gross_margin_nature="reported"），
        # 应标记为 reported；Apple 10-K 不披露产品分部毛利率（missing），
        # 应标记为 estimated。
        processed_segments = []
        for seg in segments:
            seg_copy = dict(seg)
            historical = seg_copy.get("historical_periods", [])
            latest_gm_nature = None
            latest_gm_value = None
            if historical:
                latest = historical[-1]
                latest_gm_nature = latest.get("gross_margin_nature")
                latest_gm_value = latest.get("gross_margin")
            if (latest_gm_nature == "reported"
                    and latest_gm_value is not None
                    and 0.0 <= float(latest_gm_value) <= 1.0):
                seg_copy["gross_margin_basis"] = "reported"
                seg_copy["reported_gross_margin"] = float(latest_gm_value)
            else:
                seg_copy.setdefault("gross_margin_basis", "estimated")
            processed_segments.append(seg_copy)

        # 根据公司特征生成不同的 notes
        if company.symbol.upper() == "0700.HK":
            segment_margin_note = (
                "收入分部和分部毛利率均为公司年报披露。"
            )
        else:
            segment_margin_note = (
                "收入分部为公司披露；分部毛利率未在 10-K 中披露，"
                "模型中的分部毛利率属于估算。"
            )

        return DisclosurePacket(
            provider=self.name,
            status="ready",
            company_name=snapshot["company_name"],
            symbol=company.symbol,
            market=company.exchange_name or company.exchange,
            fiscal_year=snapshot["fiscal_year"],
            currency=snapshot["currency"],
            total_revenue=snapshot["total_revenue"],
            gross_profit=actuals["gross_profit"],
            gross_margin=actuals["gross_profit"] / snapshot["total_revenue"],
            net_profit=actuals["net_profit"],
            net_margin=actuals["net_profit"] / snapshot["total_revenue"],
            segments=processed_segments,
            segment_dimension=segment_dimension,
            available_dimensions=available_dimensions,
            requested_dimension=preferred_dimension or "",
            facts=[
                {
                    "label": "毛利",
                    "value": actuals["gross_profit"],
                    "unit": snapshot["currency"],
                    "period": snapshot["fiscal_year"],
                },
                {
                    "label": "税前利润",
                    "value": actuals["profit_before_tax"],
                    "unit": snapshot["currency"],
                    "period": snapshot["fiscal_year"],
                },
                {
                    "label": "净利润",
                    "value": actuals["net_profit"],
                    "unit": snapshot["currency"],
                    "period": snapshot["fiscal_year"],
                },
            ],
            sources=snapshot["sources"],
            notes=[
                f"官方快照发布日期：{snapshot['published_date']}。",
                segment_margin_note,
            ],
        )


class AnnualReportPdfDisclosureProvider:
    def __init__(
        self,
        name: str,
        announcement_provider: Any,
        source_title: str,
        source_url: str,
    ) -> None:
        self.name = name
        self.announcement_provider = announcement_provider
        self.source_title = source_title
        self.source_url = source_url

    def supports(self, company: CompanyCandidate) -> bool:
        return self.announcement_provider.supports(company)

    @staticmethod
    def _fiscal_year(announcement: OfficialAnnouncement) -> str:
        title_years = re.findall(r"20\d{2}", announcement.title)
        if title_years:
            return title_years[-1]
        published_years = re.findall(r"20\d{2}", announcement.published_date)
        if published_years:
            return str(int(published_years[0]) - 1)
        return ""

    def fetch(
        self,
        company: CompanyCandidate,
        preferred_dimension: str | None = None,
    ) -> DisclosurePacket:
        announcement = self.announcement_provider.latest_annual(company)
        sources = [{"title": self.source_title, "url": self.source_url}]
        if not announcement:
            diagnostics = getattr(
                self.announcement_provider, "last_diagnostics", []
            )
            return DisclosurePacket(
                provider=self.name,
                status="unavailable",
                company_name=company.name,
                symbol=company.symbol,
                market=company.exchange_name or company.exchange,
                sources=sources,
                notes=[
                    "未能从官方公告查询中定位最新年度报告；"
                    "已尝试证券代码、公司名称及交易所公告回退。",
                    *diagnostics,
                ],
            )

        sources.insert(
            0,
            {
                "title": announcement.title,
                "url": announcement.url,
            },
        )
        effective_provider = announcement.provider or self.name
        text, fetch_method = fetch_document_text(announcement)
        if not text:
            return DisclosurePacket(
                provider=effective_provider,
                status="document_unavailable",
                company_name=company.name,
                symbol=company.symbol,
                market=company.exchange_name or company.exchange,
                fiscal_year=self._fiscal_year(announcement),
                sources=sources,
                notes=[
                    "已找到官方年度报告，但直接 PDF 与公共文本回退均未能读取；"
                    "请检查网络或稍后重试。"
                ],
            )

        fiscal_year = self._fiscal_year(announcement)
        company_metrics = extract_pdf_company_metrics(text)
        extracted = extract_pdf_revenue_segments(
            text,
            report_date=fiscal_year,
            preferred_dimension=preferred_dimension,
        )
        if not extracted:
            if company_metrics:
                return DisclosurePacket(
                    provider=effective_provider,
                    status="actuals_only",
                    company_name=company.name,
                    symbol=company.symbol,
                    market=company.exchange_name or company.exchange,
                    fiscal_year=fiscal_year,
                    currency=str(company_metrics.get("currency", "")),
                    total_revenue=company_metrics.get("total_revenue"),
                    gross_profit=company_metrics.get("gross_profit"),
                    gross_margin=company_metrics.get("gross_margin"),
                    net_profit=company_metrics.get("net_profit"),
                    net_margin=company_metrics.get("net_margin"),
                    sources=sources,
                    notes=[
                        "已读取合并利润表并提取公司合计指标，"
                        "但未找到能与总收入校验通过的业务收入表。"
                    ],
                )
            return DisclosurePacket(
                provider=effective_provider,
                status="unparsed",
                company_name=company.name,
                symbol=company.symbol,
                market=company.exchange_name or company.exchange,
                fiscal_year=fiscal_year,
                sources=sources,
                notes=[
                    "已读取官方年度报告，但未找到能与总收入校验通过的业务收入表；"
                    "当前不会把未经校验的数字写入模型。"
                ],
            )

        return DisclosurePacket(
            provider=effective_provider,
            status="ready",
            company_name=company.name,
            symbol=company.symbol,
            market=company.exchange_name or company.exchange,
            fiscal_year=fiscal_year,
            currency=extracted["currency"],
            total_revenue=extracted["total_revenue"],
            gross_profit=extracted.get("gross_profit"),
            gross_margin=extracted.get("gross_margin"),
            net_profit=extracted.get("net_profit"),
            net_margin=extracted.get("net_margin"),
            segments=extracted["segments"],
            segment_dimension=str(extracted.get("dimension", "")),
            available_dimensions=list(extracted.get("available_dimensions", [])),
            requested_dimension=preferred_dimension or "",
            sources=sources,
            notes=[
                f"已从 {announcement.provider} 自动定位并读取最新年度报告。",
                (
                    "官方 PDF 由本机直接下载解析。"
                    if fetch_method == "direct_pdf"
                    else "官方 PDF 通过公共 URL 文本读取服务解析。"
                ),
                "业务收入由 PDF 收入表自动提取，且分部合计已与表内总收入校验。",
                "已优先提取公司披露的分部毛利率和分部利润指标；"
                "未披露部分仍为可修改建模假设。",
            ],
        )


HKEX_PROVIDER = AnnualReportPdfDisclosureProvider(
    name="香港交易所披露易",
    announcement_provider=HkexAnnouncementProvider(),
    source_title="HKEXnews 披露易",
    source_url="https://www1.hkexnews.hk/search/titlesearch.xhtml?lang=zh",
)
CN_PROVIDER = AnnualReportPdfDisclosureProvider(
    name="巨潮资讯",
    announcement_provider=CninfoAnnouncementProvider(),
    source_title="巨潮资讯网",
    source_url="https://www.cninfo.com.cn/new/index",
)
CN_STRUCTURED_PROVIDER = AShareBusinessCompositionProvider()


def _normalized_segment_name(value: str) -> str:
    return re.sub(r"[\s（）()：:、·•*+\-_/]", "", value).lower()


# 货币代码归一化：将显示层货币标签（如"人民币百万元"）映射为 ISO 代码（如"CNY"）。
# 用于 _merge_historical_periods_from_curated 的硬约束校验。
_CURRENCY_NORMALIZATION: dict[str, str] = {
    "人民币": "CNY",
    "人民币百万元": "CNY",
    "rmb": "CNY",
    "cny": "CNY",
    "美元": "USD",
    "美元百万元": "USD",
    "usd": "USD",
    "港元": "HKD",
    "港元百万元": "HKD",
    "hkd": "HKD",
}


def _normalize_currency(value: str) -> str:
    """将货币标签归一化为 ISO 代码，便于跨层比较。"""
    if not value:
        return ""
    key = value.strip().lower()
    return _CURRENCY_NORMALIZATION.get(key, value.strip())


# 显式分部别名字典：人工审核过的跨口径/跨年度名称映射。
# key = F10 或实时披露的分部名（归一化后），value = 快照中的 segment_key。
# 只有命中此字典或精确名称匹配时才合并 historical_periods。
SEGMENT_ALIAS_DICT: dict[str, str] = {
    "其他系列酒": "series_wine_v2",
    "系列酒": "series_wine_v2",
}


def _merge_historical_periods_from_curated(
    segments: list[dict[str, Any]],
    curated_segments: list[dict[str, Any]],
    *,
    symbol: str = "",
    dimension: str = "",
    expected_dimension: str = "",
    currency: str = "",
    unit: str = "",
) -> list[dict[str, Any]]:
    """将内置官方快照的 historical_periods 合并到 F10 分部（严格匹配，无 fallback）。

    函数内硬约束（全部必须满足，任一不确定都保持无历史数据）：
    1. company/symbol 完全一致（由 symbol 参数校验，非空时必须与 curated segment 的 historical_periods 中的 symbol 一致）；
    2. dimension 完全一致（由 dimension 和 expected_dimension 参数校验，两者非空且不一致时跳过合并）；
    3. currency 一致（由 currency 参数校验，非空时归一化后必须与 historical_periods 中的 currency 一致）；
    4. unit 一致（由 unit 参数校验，非空时必须与 historical_periods 中的 unit 一致）；
    5. segment_key 完全一致（精确名称归一化匹配），或命中显式别名字典 SEGMENT_ALIAS_DICT；
    6. comparability_key 已确认（historical_periods 中每条都有非空 comparability_key）。

    严禁：收入差异匹配、子串模糊匹配、未知名称自动匹配。
    """
    # 硬约束 1：dimension 不一致时直接返回，不合并任何历史数据
    if dimension and expected_dimension and dimension != expected_dimension:
        return segments
    curated_with_hp = [
        cseg for cseg in curated_segments
        if cseg.get("historical_periods")
    ]
    if not curated_with_hp:
        return segments

    # 构建 segment_key → curated_segment 索引
    curated_by_key: dict[str, dict[str, Any]] = {}
    for cseg in curated_with_hp:
        # 从 historical_periods 的 comparability_key 推断 segment_key
        hp = cseg.get("historical_periods", [])
        if hp:
            first_hp = hp[0]
            ck = first_hp.get("comparability_key", "")
            if ck:
                curated_by_key[ck] = cseg

    for seg in segments:
        if seg.get("historical_periods"):
            continue
        seg_name = str(seg.get("name", ""))
        seg_name_norm = _normalized_segment_name(seg_name)

        # 1. 精确名称匹配：归一化后完全一致
        matched_key = None
        for ck, cseg in curated_by_key.items():
            cname_norm = _normalized_segment_name(str(cseg.get("name", "")))
            if seg_name_norm and seg_name_norm == cname_norm:
                matched_key = ck
                break

        # 2. 显式别名字典匹配
        if matched_key is None:
            alias_key = SEGMENT_ALIAS_DICT.get(seg_name)
            if alias_key and alias_key in curated_by_key:
                matched_key = alias_key

        if matched_key is not None:
            candidate_hps = curated_by_key[matched_key]["historical_periods"]

            # 硬约束校验：currency 一致（归一化后比较，支持"人民币百万元" vs "CNY"）
            if currency:
                normalized_input = _normalize_currency(currency)
                hp_currencies = {
                    _normalize_currency(hp.get("currency", "")) for hp in candidate_hps
                }
                if hp_currencies and normalized_input not in hp_currencies:
                    continue

            # 硬约束校验：unit 一致
            if unit:
                hp_units = {hp.get("unit", "") for hp in candidate_hps}
                if hp_units and unit not in hp_units:
                    continue

            seg["historical_periods"] = candidate_hps
    return segments


def _merge_f10_historical_periods(
    segments: list[dict[str, Any]],
    f10_historical: dict[str, list[dict[str, Any]]],
    *,
    dimension: str = "",
) -> list[dict[str, Any]]:
    """Phase 12B-1：将 F10 多年度 historical_periods 合并到已解析分部。

    严格匹配规则：
    1. 仅对未已有 historical_periods 的分部合并（不覆盖 curated 快照数据）；
    2. 精确名称匹配（归一化后完全一致）；
    3. 严禁模糊匹配、收入差异匹配。

    Args:
        segments: 已解析的分部列表（来自 parse_business_composition）
        f10_historical: {segment_name: [historical_period, ...]}
        dimension: 当前分部口径（用于校验，不强制）
    """
    if not f10_historical:
        return segments

    # 构建 F10 名称 → historical_periods 索引（归一化名称）
    f10_by_norm_name: dict[str, list[dict[str, Any]]] = {}
    for f10_name, periods in f10_historical.items():
        norm = _normalized_segment_name(f10_name)
        if norm and norm not in f10_by_norm_name:
            f10_by_norm_name[norm] = periods

    for seg in segments:
        if seg.get("historical_periods"):
            continue  # 已有数据（可能来自 curated），不覆盖
        seg_name = str(seg.get("name", ""))
        norm_name = _normalized_segment_name(seg_name)
        if norm_name and norm_name in f10_by_norm_name:
            seg["historical_periods"] = f10_by_norm_name[norm_name]

    return segments


def _merge_reported_segment_metrics(
    primary: list[dict[str, Any]],
    secondary: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    secondary_metrics = [
        (
            _normalized_segment_name(str(segment.get("name", ""))),
            segment.get("reported_gross_margin"),
            segment.get("reported_profit"),
            segment.get("reported_profit_margin"),
            segment.get("profit_metric_name", ""),
            segment.get("profit_metric_basis", ""),
            segment.get("supplemental_metrics", []),
        )
        for segment in secondary
        if segment.get("reported_gross_margin") is not None
        or segment.get("reported_profit") is not None
    ]
    merged: list[dict[str, Any]] = []
    for segment in primary:
        item = dict(segment)
        normalized = _normalized_segment_name(str(item.get("name", "")))
        matches = [
            (
                len(name),
                margin,
                profit,
                profit_margin,
                metric_name,
                metric_basis,
                supplemental_metrics,
            )
            for (
                name,
                margin,
                profit,
                profit_margin,
                metric_name,
                metric_basis,
                supplemental_metrics,
            ) in secondary_metrics
            if name == normalized
            or (
                min(len(name), len(normalized)) >= 4
                and (
                    name.startswith(normalized)
                    or normalized.startswith(name)
                )
            )
        ]
        if matches:
            (
                _,
                margin,
                profit,
                profit_margin,
                metric_name,
                metric_basis,
                supplemental_metrics,
            ) = max(matches, key=lambda value: value[0])
            if item.get("reported_gross_margin") is None and margin is not None:
                if 0.0 <= float(margin) <= 1.0:
                    item["reported_gross_margin"] = margin
                    item["base_gross_margin"] = margin
                    item["gross_margin_basis"] = "reported"
                else:
                    item["invalid_reported_gross_margin"] = margin
            if item.get("reported_profit") is None and profit is not None:
                item["reported_profit"] = profit
                item["reported_profit_margin"] = profit_margin
                item["profit_metric_name"] = metric_name
                item["profit_metric_basis"] = metric_basis
                item["supplemental_metrics"] = supplemental_metrics
        merged.append(item)
    return merged


def configured_disclosure_providers() -> list[DisclosureProvider]:
    return [
        SecEdgarDisclosureProvider(),
        CuratedOfficialDisclosureProvider(),
        HKEX_PROVIDER,
        CN_PROVIDER,
    ]


def parse_uploaded_annual_report(
    company: CompanyCandidate,
    pdf_bytes: bytes,
    filename: str = "",
) -> DisclosurePacket:
    text = extract_pdf_text(pdf_bytes)
    source = {
        "title": filename or "用户上传的官方年度报告",
        "url": "",
    }
    if not text:
        return DisclosurePacket(
            provider="上传的官方年度报告",
            status="unparsed",
            company_name=company.name,
            symbol=company.symbol,
            market=company.exchange_name or company.exchange,
            sources=[source],
            notes=["PDF 未提取到文本；文件可能是扫描版或已加密。"],
        )

    year_matches = re.findall(
        r"(20\d{2})(?:\s*年)?(?:年度报告|年度報告|annual report)",
        f"{filename}\n{text[:100000]}",
        flags=re.IGNORECASE,
    )
    fiscal_year = max(year_matches) if year_matches else ""
    company_metrics = extract_pdf_company_metrics(text)
    extracted = extract_pdf_revenue_segments(text, report_date=fiscal_year)
    if not extracted:
        if company_metrics:
            return DisclosurePacket(
                provider="上传的官方年度报告",
                status="actuals_only",
                company_name=company.name,
                symbol=company.symbol,
                market=company.exchange_name or company.exchange,
                fiscal_year=fiscal_year,
                currency=str(company_metrics.get("currency", "")),
                total_revenue=company_metrics.get("total_revenue"),
                gross_profit=company_metrics.get("gross_profit"),
                gross_margin=company_metrics.get("gross_margin"),
                net_profit=company_metrics.get("net_profit"),
                net_margin=company_metrics.get("net_margin"),
                sources=[source],
                notes=[
                    "PDF 已读取并提取公司合计指标，但未找到可校验的业务收入表。"
                ],
            )
        return DisclosurePacket(
            provider="上传的官方年度报告",
            status="unparsed",
            company_name=company.name,
            symbol=company.symbol,
            market=company.exchange_name or company.exchange,
            fiscal_year=fiscal_year,
            sources=[source],
            notes=[
                "PDF 已读取，但未找到能与总收入校验通过的业务收入表；"
                "可尝试上传完整年度报告，而不是年度报告摘要。"
            ],
        )

    return DisclosurePacket(
        provider="上传的官方年度报告",
        status="ready",
        company_name=company.name,
        symbol=company.symbol,
        market=company.exchange_name or company.exchange,
        fiscal_year=fiscal_year,
        currency=extracted["currency"],
        total_revenue=extracted["total_revenue"],
        gross_profit=extracted.get("gross_profit"),
        gross_margin=extracted.get("gross_margin"),
        net_profit=extracted.get("net_profit"),
        net_margin=extracted.get("net_margin"),
        segments=extracted["segments"],
        sources=[source],
        notes=[
            "业务收入由上传的官方年度报告自动提取，且分部合计已通过校验。",
            "已优先提取披露分部毛利率和分部利润指标；"
            "未披露部分仍为可修改建模假设。",
        ],
    )


def get_company_disclosure(
    company: CompanyCandidate,
    preferred_dimension: str | None = None,
) -> DisclosurePacket:
    curated = CuratedOfficialDisclosureProvider()
    if company.cik:
        sec_packet = SecEdgarDisclosureProvider().fetch(
            company,
            preferred_dimension=preferred_dimension,
        )
        if sec_packet.segments:
            return sec_packet
        if curated.supports(company):
            fallback = curated.fetch(
                company,
                preferred_dimension=preferred_dimension,
            )
            # SEC 未配置时不应把 "SEC EDGAR" 列入 provider，避免冒充实时 SEC 数据
            sec_actually_returned_data = bool(sec_packet.segments)
            if sec_actually_returned_data:
                fallback.provider = f"{sec_packet.provider} + {fallback.provider}"
            else:
                fallback.provider = fallback.provider
            fallback.sources = [
                *sec_packet.sources,
                *[
                    source
                    for source in fallback.sources
                    if source.get("url")
                    not in {item.get("url") for item in sec_packet.sources}
                ],
            ]
            if sec_actually_returned_data:
                fallback.notes = [
                    *sec_packet.notes,
                    "通用 SEC 自动提取未通过分部合计校验，已使用已核验官方快照。",
                    *fallback.notes,
                ]
            else:
                fallback.notes = [
                    *fallback.notes,
                    "未配置 SEC 联系邮箱，使用已核验内置官方快照，不等于实时官方披露。",
                ]
            return fallback
        return sec_packet

    if CN_STRUCTURED_PROVIDER.supports(company):
        structured = CN_STRUCTURED_PROVIDER.fetch(
            company,
            preferred_dimension=preferred_dimension,
        )
        if structured:
            pdf_packet = CN_PROVIDER.fetch(
                company,
                preferred_dimension=preferred_dimension,
            )
            segments = _merge_reported_segment_metrics(
                structured.segments,
                pdf_packet.segments,
            )
            # 合并内置官方快照的多期历史数据（严格匹配，无 fallback）
            if curated.supports(company):
                curated_snapshot = curated.SNAPSHOTS.get(
                    company.symbol.upper(), {}
                )
                curated_segments = curated_snapshot.get("segments", [])
                curated_currency = curated_snapshot.get("currency", "")
                curated_dimension = curated_snapshot.get("segment_dimension", "")
                segments = _merge_historical_periods_from_curated(
                    segments, curated_segments,
                    symbol=company.symbol.upper(),
                    dimension=structured.dimension,
                    expected_dimension=curated_dimension,
                    currency=curated_currency,
                    unit="million",
                )

            # Phase 12B-1：从 F10 缓存构建真实多年度 historical_periods
            # （仅对未已有 historical_periods 的分部，严格名称匹配，不模糊匹配）
            f10_historical = CN_STRUCTURED_PROVIDER.fetch_historical_periods(
                company,
                preferred_dimension=preferred_dimension,
            )
            if f10_historical:
                segments = _merge_f10_historical_periods(
                    segments, f10_historical,
                    dimension=structured.dimension,
                )

            # Phase 12B-1 收口：从 F10 缓存构建分部合计（非公司总收入）
            segment_historical_totals = (
                CN_STRUCTURED_PROVIDER.fetch_segment_historical_totals(
                    company,
                    preferred_dimension=preferred_dimension,
                )
            )

            # Phase 12B-1 收口：从独立来源构建公司总收入（F10 payload 中的"合计"行）
            company_financial_totals = (
                CN_STRUCTURED_PROVIDER.fetch_company_financial_totals(
                    company,
                    preferred_dimension=preferred_dimension,
                )
            )

            # Phase 12B-1 收口：构建原始历史分部池（保留旧口径分部）
            raw_historical_segment_pool = (
                CN_STRUCTURED_PROVIDER.fetch_raw_historical_segment_pool(
                    company,
                    preferred_dimension=preferred_dimension,
                )
            )
            sources = [
                {
                    "title": "公开 F10 主营构成",
                    "url": structured.source_url,
                }
            ]
            seen_urls = {structured.source_url}
            for source in pdf_packet.sources:
                url = source.get("url", "")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                sources.append(source)
            return DisclosurePacket(
                provider=(
                    f"{CN_STRUCTURED_PROVIDER.name} + {pdf_packet.provider}"
                    if pdf_packet.total_revenue
                    else CN_STRUCTURED_PROVIDER.name
                ),
                status="ready",
                company_name=company.name,
                symbol=company.symbol,
                market=company.exchange_name or company.exchange,
                fiscal_year=structured.fiscal_year,
                currency=structured.currency,
                total_revenue=structured.total_revenue,
                gross_profit=pdf_packet.gross_profit or structured.gross_profit,
                gross_margin=(
                    pdf_packet.gross_margin
                    if pdf_packet.gross_margin is not None
                    else structured.gross_margin
                ),
                net_profit=pdf_packet.net_profit or structured.net_profit,
                net_margin=(
                    pdf_packet.net_margin
                    if pdf_packet.net_margin is not None
                    else structured.net_margin
                ),
                segments=segments,
                segment_dimension=structured.dimension,
                available_dimensions=structured.available_dimensions,
                requested_dimension=preferred_dimension or "",
                sources=sources,
                company_financial_totals=company_financial_totals,
                segment_historical_totals=segment_historical_totals,
                raw_historical_segment_pool=raw_historical_segment_pool,
                notes=[
                    "已按证券代码读取最新完整财年的结构化主营构成。",
                    "系统优先选择产品或业务口径，并校验分部占比或合计。",
                    (
                        "已结合官方年报补充公司合计利润率和可取得的分部毛利率、"
                        "分部利润指标。"
                        if pdf_packet.total_revenue
                        else "未披露的分部毛利率和利润指标仍为可修改建模假设。"
                    ),
                    *pdf_packet.notes,
                ],
            )

    for provider in (HKEX_PROVIDER, CN_PROVIDER):
        if provider.supports(company):
            packet = provider.fetch(
                company,
                preferred_dimension=preferred_dimension,
            )
            if packet.segments:
                return packet
            if curated.supports(company):
                fallback = curated.fetch(
                    company,
                    preferred_dimension=preferred_dimension,
                )
                fallback.provider = f"{packet.provider} + {fallback.provider}"
                fallback.sources = [
                    *packet.sources,
                    *[
                        source
                        for source in fallback.sources
                        if source.get("url")
                        not in {item.get("url") for item in packet.sources}
                    ],
                ]
                fallback.notes = [
                    *packet.notes,
                    f"通用 {packet.provider} 自动提取未完成，已使用已核验官方快照。",
                    *fallback.notes,
                ]
                return fallback
            return packet
    if curated.supports(company):
        return curated.fetch(company, preferred_dimension=preferred_dimension)
    return DisclosurePacket(
        provider="无匹配 Provider",
        status="unsupported",
        company_name=company.name,
        symbol=company.symbol,
        market=company.exchange_name or company.exchange,
        notes=["当前市场尚未配置自动披露数据源。"],
    )
