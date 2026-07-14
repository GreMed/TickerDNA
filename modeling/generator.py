from __future__ import annotations

import json
import os
import re
from typing import Any

import requests

from modeling.company_data import CompanyCandidate
from modeling.disclosures import DisclosurePacket, get_company_disclosure
from modeling.engine import normalize_assumptions
from modeling.rationale import generate_rationale_items

STANDARD_SPLIT_DIMENSIONS = {"product", "geography", "industry", "business"}
DISCLOSURE_ACCESS_BLOCKED_STATUSES = {
    "configuration_required",
    "unavailable",
    "document_unavailable",
    "parser_required",
}
SPLIT_DIMENSION_LABELS = {
    "product": "按产品",
    "geography": "按地区",
    "industry": "按行业",
    "business": "按产品",
}


SYSTEM_PROMPT = """你是一名严谨的FP&A建模分析师。根据用户的中文建模思路，生成一个简化、
可编辑、可审计的五年收入和利润预测假设。只返回JSON，不要Markdown。
要求：
1. 收入拆分为1-4个互不重叠的业务分部。
2. 金额单位统一为人民币百万元。
3. 每个分部给出基期收入、Bull/Base/Bear年增长率和毛利率。
4. 给出基期经营费用率、基期其他损益率，以及统一所得税率；这两项不区分情景。
5. Bull应优于Base，Base应优于Bear，数值需合理。
JSON字段必须严格为：
{
  "company_name": "string",
  "currency": "人民币百万元",
  "rationale": "string",
  "segments": [{
    "name": "string", "base_revenue": number,
    "bull_growth": number, "base_growth": number, "bear_growth": number,
    "bull_gross_margin": number, "base_gross_margin": number,
    "bear_gross_margin": number
  }],
  "base_opex_ratio": number, "base_other_ratio": number,
  "opex_ratio_annual_change": 0, "other_ratio_annual_change": 0,
  "tax_rate": number
}
所有比例使用小数，例如15%写成0.15。"""

COMPANY_RESEARCH_PROMPT = """你是一名严谨的股票研究和FP&A分析师。研究指定上市公司，
为收入预测模型建立一个“可修改的初始业务拆分”。

研究要求：
- 优先使用公司官网、投资者关系网站、最新年报/10-K/20-F、交易所或监管机构文件。
- 识别公司最新完整财年总收入、币种、财年，以及公司正式披露的收入分部。
- 业务分部应互斥且合计覆盖公司收入，控制在1-6个。
- 如果公司未披露分部收入，可根据产品或业务构成估算，但必须标记为estimated。
- reported分部的收入和占比应尽可能贴近公开披露，estimated分部需说明估算逻辑。
- 为预测提供Bull/Base/Bear年增长率与毛利率；经营费用率和其他损益率只提供基期值，
  不区分情景，默认每年变动均为0。
- Bull应优于Base，Base应优于Bear。不要把推断写成已披露事实。
- 金额统一换算为“百万”单位，currency写清币种，例如“美元百万元”。
- rationale用中文简要解释拆分逻辑、财年和主要不确定性。
"""


COMPANY_RESEARCH_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "company_name",
        "symbol",
        "currency",
        "fiscal_year",
        "total_revenue",
        "research_summary",
        "rationale",
        "data_quality",
        "segments",
        "base_opex_ratio",
        "base_other_ratio",
        "opex_ratio_annual_change",
        "other_ratio_annual_change",
        "tax_rate",
    ],
    "properties": {
        "company_name": {"type": "string"},
        "symbol": {"type": "string"},
        "currency": {"type": "string"},
        "fiscal_year": {"type": "string"},
        "total_revenue": {"type": "number", "minimum": 0},
        "research_summary": {"type": "string"},
        "rationale": {"type": "string"},
        "data_quality": {"type": "string"},
        "segments": {
            "type": "array",
            "minItems": 1,
            "maxItems": 6,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "name",
                    "description",
                    "base_revenue",
                    "revenue_share",
                    "basis",
                    "evidence",
                    "bull_growth",
                    "base_growth",
                    "bear_growth",
                    "bull_gross_margin",
                    "base_gross_margin",
                    "bear_gross_margin",
                ],
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "base_revenue": {"type": "number", "minimum": 0},
                    "revenue_share": {"type": "number", "minimum": 0, "maximum": 1},
                    "basis": {
                        "type": "string",
                        "enum": ["reported", "estimated"],
                    },
                    "evidence": {"type": "string"},
                    "bull_growth": {"type": "number"},
                    "base_growth": {"type": "number"},
                    "bear_growth": {"type": "number"},
                    "bull_gross_margin": {"type": "number"},
                    "base_gross_margin": {"type": "number"},
                    "bear_gross_margin": {"type": "number"},
                },
            },
        },
        "base_opex_ratio": {"type": "number"},
        "base_other_ratio": {"type": "number"},
        "opex_ratio_annual_change": {"type": "number"},
        "other_ratio_annual_change": {"type": "number"},
        "tax_rate": {"type": "number"},
    },
}


def _response_text(payload: dict[str, Any]) -> str:
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                return str(content.get("text", ""))
    return ""


def _create_response(
    *,
    model: str,
    input_messages: list[dict[str, str]],
    tools: list[dict[str, Any]] | None = None,
    json_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    api_key = os.environ["OPENAI_API_KEY"]
    body: dict[str, Any] = {"model": model, "input": input_messages}
    if tools:
        body["tools"] = tools
    if json_schema:
        body["text"] = {
            "format": {
                "type": "json_schema",
                "name": "company_research_result",
                "strict": True,
                "schema": json_schema,
            }
        }

    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=180,
    )
    if not response.ok:
        detail = response.text[:500]
        raise RuntimeError(f"OpenAI API 请求失败（{response.status_code}）：{detail}")
    return response.json()


def _extract_citations(response: dict[str, Any]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            for annotation in content.get("annotations", []):
                if annotation.get("type") != "url_citation":
                    continue
                url = str(annotation.get("url", "")).strip()
                if not url or url in seen:
                    continue
                seen.add(url)
                sources.append(
                    {
                        "title": str(annotation.get("title", url)),
                        "url": url,
                    }
                )
    return sources


def _balance_segment_revenue(result: dict[str, Any]) -> dict[str, Any]:
    segments = result.get("segments", [])
    total = float(result.get("total_revenue", 0))
    if not segments or total <= 0:
        return result

    segment_total = sum(max(float(item.get("base_revenue", 0)), 0) for item in segments)
    if segment_total <= 0:
        shares = [max(float(item.get("revenue_share", 0)), 0) for item in segments]
        share_total = sum(shares) or len(segments)
        for item, share in zip(segments, shares):
            item["base_revenue"] = total * (share or 1) / share_total
        return result

    if abs(segment_total - total) / total > 0.03:
        scale = total / segment_total
        for item in segments:
            item["base_revenue"] = float(item["base_revenue"]) * scale
    return result


def _calibrate_missing_segment_margins(
    segments: list[dict[str, Any]],
    total_revenue: float | None,
    company_gross_margin: float | None,
) -> None:
    if not segments or not total_revenue or company_gross_margin is None:
        return

    target_gross_profit = total_revenue * company_gross_margin
    reported_gross_profit = sum(
        float(segment.get("base_revenue", 0))
        * float(segment.get("reported_gross_margin", 0))
        for segment in segments
        if segment.get("reported_gross_margin") is not None
    )
    missing = [
        segment
        for segment in segments
        if segment.get("reported_gross_margin") is None
        and float(segment.get("base_revenue", 0)) > 0
    ]
    missing_revenue = sum(float(segment["base_revenue"]) for segment in missing)
    if not missing or missing_revenue <= 0:
        return

    current_missing_gross_profit = sum(
        float(segment["base_revenue"])
        * float(segment.get("base_gross_margin", company_gross_margin))
        for segment in missing
    )
    adjustment = (
        target_gross_profit
        - reported_gross_profit
        - current_missing_gross_profit
    ) / missing_revenue

    for segment in missing:
        original_base = float(
            segment.get("base_gross_margin", company_gross_margin)
        )
        bull_uplift = max(
            float(segment.get("bull_gross_margin", original_base)) - original_base,
            0,
        )
        bear_reduction = max(
            original_base - float(segment.get("bear_gross_margin", original_base)),
            0,
        )
        calibrated = min(
            max(original_base + adjustment, 0.01),
            1.0,
        )
        segment["bull_gross_margin"] = min(calibrated + bull_uplift, 1.0)
        segment["base_gross_margin"] = calibrated
        segment["bear_gross_margin"] = max(calibrated - bear_reduction, 0.01)
        segment["gross_margin_basis"] = "derived"
        evidence = str(segment.get("evidence", "")).strip()
        segment["evidence"] = (
            f"{evidence}；未披露分部毛利率，按公司合计毛利率反推"
            if evidence
            else "未披露分部毛利率，按公司合计毛利率反推"
        )


def _split_mode(split_basis: dict[str, str] | None) -> str:
    return str((split_basis or {}).get("mode", "auto")).strip()


def _split_label(split_basis: dict[str, str] | None) -> str:
    mode = _split_mode(split_basis)
    label = str((split_basis or {}).get("label", "")).strip()
    if label:
        return label
    return SPLIT_DIMENSION_LABELS.get(mode, "自动选择已披露口径")


def _preferred_dimension(split_basis: dict[str, str] | None) -> str | None:
    mode = _split_mode(split_basis)
    return mode if mode in STANDARD_SPLIT_DIMENSIONS else None


def _split_satisfied(
    split_basis: dict[str, str] | None,
    disclosure: DisclosurePacket,
) -> bool:
    mode = _split_mode(split_basis)
    if mode in {"", "auto"}:
        return True
    if mode == "product" and disclosure.segment_dimension == "business":
        return True
    if mode in STANDARD_SPLIT_DIMENSIONS:
        return disclosure.segment_dimension == mode
    return False


def _estimated_segments_for_split_basis(
    *,
    total_revenue: float,
    split_basis: dict[str, str] | None,
    company_context: str = "",
) -> list[dict[str, Any]]:
    mode = _split_mode(split_basis)
    label = _split_label(split_basis)
    text = f"{label} {company_context}".lower()
    if mode == "geography" or any(keyword in text for keyword in ("地区", "区域", "海外", "境外")):
        names = ["中国大陆", "境外"]
        weights = [0.82, 0.18]
    elif mode == "industry" or any(keyword in text for keyword in ("行业", "客户", "下游", "应用", "场景")):
        names = ["企业客户", "运营商", "政府及公共事业", "其他行业"]
        weights = [0.45, 0.25, 0.20, 0.10]
    elif mode == "product" or "产品" in text:
        names = ["核心产品", "成长产品", "其他产品"]
        weights = [0.65, 0.25, 0.10]
    else:
        names = _segment_names(label or company_context)
        weights = [0.65, 0.35] if len(names) == 2 else [1 / len(names)] * len(names)
    if len(weights) != len(names):
        weights = [1 / len(names)] * len(names)
    weights[-1] += 1 - sum(weights)

    segments: list[dict[str, Any]] = []
    for index, (name, weight) in enumerate(zip(names, weights)):
        base_growth = max(0.04, 0.12 - index * 0.02)
        base_margin = max(0.20, min(0.60, 0.38 + index * 0.025))
        segments.append(
            {
                "name": name,
                "base_revenue": total_revenue * weight,
                "bull_growth": base_growth + 0.05,
                "base_growth": base_growth,
                "bear_growth": max(base_growth - 0.07, -0.05),
                "bull_gross_margin": min(base_margin + 0.03, 0.90),
                "base_gross_margin": base_margin,
                "bear_gross_margin": max(base_margin - 0.04, 0.05),
                "basis": "estimated",
                "reported_gross_margin": None,
                "gross_margin_basis": "estimated",
                "description": f"用户坚持使用“{label}”，当前未取得可校验披露。",
                "evidence": "基于公开总收入和用户指定口径生成的估算拆分，需人工确认。",
            }
        )
    return segments


def fallback_company_assumptions(
    company: CompanyCandidate,
    user_context: str = "",
    disclosure: DisclosurePacket | None = None,
    split_basis: dict[str, str] | None = None,
    force_custom_split: bool = False,
) -> dict[str, Any]:
    context = " ".join(
        part
        for part in [
            company.name,
            company.sector,
            company.industry,
            user_context,
        ]
        if part
    )
    assumptions = fallback_assumptions(context)
    assumptions["company_name"] = company.name
    assumptions["symbol"] = company.symbol
    disclosure = disclosure or get_company_disclosure(
        company,
        preferred_dimension=_preferred_dimension(split_basis),
    )
    assumptions["disclosure_provider"] = disclosure.provider
    assumptions["source_category"] = disclosure.source_category
    assumptions["disclosure_status"] = disclosure.status
    assumptions["disclosure_notes"] = disclosure.notes
    assumptions["sources"] = disclosure.sources
    assumptions["actual_total_revenue"] = disclosure.total_revenue
    assumptions["actual_gross_profit"] = disclosure.gross_profit
    assumptions["actual_gross_margin"] = disclosure.gross_margin
    assumptions["actual_net_profit"] = disclosure.net_profit
    assumptions["actual_net_margin"] = disclosure.net_margin
    assumptions["company_financial_totals"] = disclosure.company_financial_totals
    assumptions["segment_historical_totals"] = disclosure.segment_historical_totals
    assumptions["raw_historical_segment_pool"] = disclosure.raw_historical_segment_pool
    requested_split_label = _split_label(split_basis)
    split_satisfied = _split_satisfied(split_basis, disclosure)
    assumptions["requested_split_basis"] = requested_split_label
    assumptions["requested_split_mode"] = _split_mode(split_basis)
    assumptions["actual_split_dimension"] = disclosure.segment_dimension
    assumptions["available_split_dimensions"] = disclosure.available_dimensions
    assumptions["split_basis_satisfied"] = split_satisfied
    disclosure_access_blocked = (
        disclosure.status in DISCLOSURE_ACCESS_BLOCKED_STATUSES
        and not disclosure.segments
        and not disclosure.available_dimensions
    )
    assumptions["disclosure_access_blocked"] = disclosure_access_blocked
    assumptions["split_basis_unavailable"] = (
        _split_mode(split_basis) not in {"", "auto"}
        and not split_satisfied
        and not disclosure_access_blocked
    )
    assumptions["split_basis_force_estimated"] = force_custom_split

    if force_custom_split and assumptions["split_basis_unavailable"]:
        total = disclosure.total_revenue or sum(
            float(segment.get("base_revenue", 0))
            for segment in assumptions["segments"]
        )
        assumptions["segments"] = _estimated_segments_for_split_basis(
            total_revenue=float(total or 1000),
            split_basis=split_basis,
            company_context=context,
        )
        assumptions["currency"] = disclosure.currency or assumptions["currency"]
        assumptions["fiscal_year"] = disclosure.fiscal_year or "待确认"
        assumptions["research_summary"] = (
            f"未在可结构化披露中找到“{requested_split_label}”，"
            "已按用户坚持的口径生成估算拆分。该结果不应视为公司披露。"
        )
        assumptions["data_quality"] = "用户定义口径估算"
        assumptions["split_basis_satisfied"] = True
        assumptions["split_basis_unavailable"] = False
    elif disclosure.segments:
        assumptions["segments"] = []
        for segment in disclosure.segments:
            base_growth = float(segment.get("base_growth", 0.08))
            raw_base_margin = float(segment.get("base_gross_margin", 0.5))
            reported_gross_margin = segment.get("reported_gross_margin")
            invalid_reported = segment.get("invalid_reported_gross_margin")

            # 毛利率合法性校验：必须在 0%~100%
            # 如果原始披露值非法，不标记为 reported，改用模型估算
            margin_is_valid = 0.0 <= raw_base_margin <= 1.0
            if margin_is_valid:
                base_margin = raw_base_margin
            else:
                base_margin = 0.40

            reported_is_valid = (
                reported_gross_margin is not None
                and 0.0 <= float(reported_gross_margin) <= 1.0
            )
            if not reported_is_valid:
                reported_gross_margin = None

            raw_gross_margin_basis = segment.get(
                "gross_margin_basis",
                (
                    "reported"
                    if reported_gross_margin is not None
                    else "estimated"
                ),
            )
            if margin_is_valid and raw_gross_margin_basis == "reported":
                gross_margin_basis = "reported"
            elif invalid_reported is not None or not margin_is_valid:
                gross_margin_basis = "estimated"
            else:
                gross_margin_basis = raw_gross_margin_basis

            evidence = segment.get("evidence", "")
            if invalid_reported is not None and not margin_is_valid:
                invalid_pct = float(invalid_reported) * 100
                evidence = (
                    f"原始披露毛利率 {invalid_pct:.1f}% 超出合法范围（0%~100%），"
                    f"已改用模型估算。{evidence}"
                )

            reported_profit = segment.get("reported_profit")
            reported_profit_margin = segment.get("reported_profit_margin")
            profit_metric_name = segment.get("profit_metric_name", "")

            # Phase 12B-0：分部利润指标语义校验
            # 防止成本字段被误用为利润，防止不可对账的利润指标进入毛利率预测依据
            from modeling.metric_validation import (
                reconcile_segment_profit,
                can_use_as_margin_basis,
                PROFIT_METRIC_UNKNOWN,
            )
            profit_reconciliation = reconcile_segment_profit(
                segment_name=segment["name"],
                reported_profit=reported_profit,
                reported_profit_margin=reported_profit_margin,
                profit_metric_name=profit_metric_name,
                segment_revenue=float(segment["revenue"]),
                company_gross_profit=disclosure.gross_profit,
                company_gross_margin=disclosure.gross_margin,
                company_total_revenue=disclosure.total_revenue,
            )
            if not profit_reconciliation.is_valid:
                # 对账失败：利润指标不可信，清除以防止进入预测依据
                reported_profit = None
                reported_profit_margin = None
                profit_metric_name = ""
                supplemental_metrics = []
                profit_metric_basis = ""
                evidence = (
                    f"披露利润指标未通过对账校验："
                    f"{'；'.join(profit_reconciliation.errors)}。"
                    f"已清除该指标，不用于毛利率预测。{evidence}"
                )
            elif profit_reconciliation.warnings:
                # 对账通过但有警告：保留数据但标注警告
                supplemental_metrics = segment.get("supplemental_metrics", [])
                profit_metric_basis = segment.get("profit_metric_basis", "")
                evidence = (
                    f"{'；'.join(profit_reconciliation.warnings)}。"
                    f"指标口径未核验，未用于预测。{evidence}"
                )
            else:
                supplemental_metrics = segment.get("supplemental_metrics", [])
                profit_metric_basis = segment.get("profit_metric_basis", "")

            bull_gross_margin = min(base_margin + 0.025, 0.95)
            bear_gross_margin = max(base_margin - 0.04, 0.05)
            assumptions["segments"].append(
                {
                    "name": segment["name"],
                    "base_revenue": float(segment["revenue"]),
                    "bull_growth": min(base_growth + 0.05, 0.50),
                    "base_growth": base_growth,
                    "bear_growth": max(base_growth - 0.07, -0.10),
                    "bull_gross_margin": bull_gross_margin,
                    "base_gross_margin": base_margin,
                    "bear_gross_margin": bear_gross_margin,
                    "basis": "reported",
                    "reported_gross_margin": reported_gross_margin,
                    "gross_margin_basis": gross_margin_basis,
                    "reported_profit": reported_profit,
                    "reported_profit_margin": reported_profit_margin,
                    "profit_metric_name": profit_metric_name,
                    "profit_metric_basis": profit_metric_basis,
                    "supplemental_metrics": supplemental_metrics,
                    "description": segment.get("description", ""),
                    "evidence": evidence,
                    "historical_periods": segment.get("historical_periods", []),
                }
            )
        _calibrate_missing_segment_margins(
            assumptions["segments"],
            disclosure.total_revenue,
            disclosure.gross_margin,
        )
        assumptions["currency"] = disclosure.currency
        assumptions["fiscal_year"] = disclosure.fiscal_year
        source_description = (
            "公开 F10 汇总的公司主营构成"
            if disclosure.provider == "公开F10主营构成"
            else "官方披露"
        )
        assumptions["research_summary"] = (
            f"已使用 {disclosure.provider} 的 FY{disclosure.fiscal_year} "
            f"{source_description}，按公司报告口径建立收入分部。"
            "已披露分部毛利率直接采用公司数据，未披露部分按公司合计毛利率推算；"
            "若公司披露分部净利润、营业利润或 EBITDA，也会作为分部利润指标保留；"
            "所有预测假设仍可修改。"
        )
        assumptions["data_quality"] = "公司披露分部 + 建模假设"

        if company.symbol.upper() in {"0700.HK", "AAPL"}:
            if company.symbol.upper() == "0700.HK":
                total = disclosure.total_revenue or 751_766
                opex = 41_727 + 136_127
                gross_profit = 422_593
                pretax_profit = 277_249
                income_tax = 47_448
            else:
                total = disclosure.total_revenue or 416_161
                opex = 62_151
                gross_profit = 195_201
                pretax_profit = 132_729
                income_tax = 20_719
            assumptions.update(
                {
                    "base_opex_ratio": opex / total,
                    "base_other_ratio": (pretax_profit - gross_profit + opex)
                    / total,
                    "opex_ratio_annual_change": 0.0,
                    "other_ratio_annual_change": 0.0,
                    "tax_rate": income_tax / pretax_profit,
                }
            )
    elif disclosure.total_revenue and disclosure.total_revenue > 0:
        current_total = sum(
            float(segment["base_revenue"]) for segment in assumptions["segments"]
        )
        scale = disclosure.total_revenue / current_total if current_total else 1
        for segment in assumptions["segments"]:
            segment["base_revenue"] *= scale
        assumptions["currency"] = disclosure.currency or assumptions["currency"]
        assumptions["fiscal_year"] = disclosure.fiscal_year or "待确认"
        assumptions["research_summary"] = (
            f"已通过 {disclosure.provider} 获取 {company.name} 最新完整财年总收入。"
            "当前结果基于已取得的公开资料和通用规则形成，业务分部为初始估算。"
            "补充更完整的公司经营资料后，系统可据此更新研究结果。"
        )
        assumptions["data_quality"] = "官方总收入 + 估算分部"
    else:
        if disclosure.status in {"document_unavailable", "parser_required"}:
            assumptions["research_summary"] = (
                f"已识别 {company.name}（{company.symbol}）并定位官方年度报告，"
                "但当前数据进程未能读取 PDF，因此暂时显示占位拆分。"
                "可从公开资料来源下载完整年报，并使用页面上的 PDF 上传入口自动解析。"
            )
        elif disclosure.status == "unparsed":
            assumptions["research_summary"] = (
                f"已识别 {company.name}（{company.symbol}）并成功读取官方年度报告，"
                "但暂未识别出能与总收入校验通过的业务收入表，因此暂时显示占位拆分。"
            )
        else:
            assumptions["research_summary"] = (
                f"已识别 {company.name}（{company.symbol}），但当前没有取得"
                "可结构化的公司披露，以下业务分部为行业关键词生成的占位拆分。"
            )
        assumptions["data_quality"] = "占位估算"
        assumptions["fiscal_year"] = disclosure.fiscal_year or "待确认"

    if not disclosure.segments and not assumptions.get("split_basis_force_estimated"):
        for segment in assumptions["segments"]:
            segment["basis"] = "estimated"
            segment["description"] = "基于通用规则生成的初始估算，需结合公司资料确认。"
            segment["evidence"] = (
                f"总收入来自 {disclosure.provider}；分部为估算"
                if disclosure.total_revenue
                else "未取得可结构化的分部披露"
            )
    # 生成假设依据 — Phase 12B-2：统一预测年度生成规则
    _years = sorted(int(y) for y in assumptions.get("yearly_profit_assumptions", {}))
    if not _years:
        from modeling.engine import assumption_forecast_years
        _years = assumption_forecast_years(assumptions, 5)
    assumptions["rationale_items"] = generate_rationale_items(assumptions, _years)
    return assumptions


def research_company_assumptions(
    company: CompanyCandidate,
    user_context: str = "",
    split_basis: dict[str, str] | None = None,
    force_custom_split: bool = False,
) -> tuple[dict[str, Any], str]:
    disclosure = get_company_disclosure(
        company,
        preferred_dimension=_preferred_dimension(split_basis),
    )
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        assumptions = fallback_company_assumptions(
            company,
            user_context,
            disclosure=disclosure,
            split_basis=split_basis,
            force_custom_split=force_custom_split,
        )
        source_label = (
            f"{disclosure.provider} + 建模假设"
            if disclosure.segments
            else f"{disclosure.provider} + 占位估算"
        )
        if assumptions.get("split_basis_force_estimated"):
            source_label = f"{disclosure.provider} + 用户定义口径估算"
        return assumptions, source_label

    model = os.getenv("OPENAI_RESEARCH_MODEL") or os.getenv("OPENAI_MODEL", "gpt-5.5")
    requested_split_label = _split_label(split_basis)
    split_instruction = (
        "用户未指定特殊拆分口径，优先采用公司正式披露且可校验的分部。"
        if _split_mode(split_basis) in {"", "auto"}
        else (
            f"用户指定拆分口径：{requested_split_label}。"
            "如果该口径已在财报中披露，请优先使用披露数据；"
            "如果未披露但用户坚持该口径，请基于公开资料整理可解释的估算拆分，"
            "所有 estimated 项必须明确说明估算依据和不确定性。"
            if force_custom_split
            else (
                f"用户指定拆分口径：{requested_split_label}。"
                "如果该口径未在财报中披露，不要强行估算，"
                "请优先保留已披露口径并说明未满足用户请求。"
            )
        )
    )
    identity = (
        f"公司名称：{company.name}\n股票代码：{company.symbol}\n"
        f"交易所：{company.exchange_name or company.exchange or '待确认'}\n"
        f"行业线索：{company.sector} / {company.industry}\n"
        f"用户补充：{user_context or '无'}\n\n"
        f"{split_instruction}\n\n"
        f"已获取的官方事实包：\n{disclosure.to_prompt()}"
    )

    research = _create_response(
        model=model,
        tools=[{"type": "web_search", "search_context_size": "high"}],
        input_messages=[
            {"role": "system", "content": COMPANY_RESEARCH_PROMPT},
            {
                "role": "user",
                "content": (
                    f"{identity}\n\n请联网核实公司身份，研究最新完整财年公开资料，"
                    "输出一份有来源引用的中文研究备忘录，覆盖收入规模、正式披露分部、"
                    "业务描述、可用于建模的拆分方式、用户指定口径是否披露和不确定性。"
                ),
            },
        ],
    )
    sources = [*disclosure.sources, *_extract_citations(research)]
    deduplicated_sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for source in sources:
        url = source.get("url", "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        deduplicated_sources.append(source)
    research_text = _response_text(research)

    parsed = _create_response(
        model=model,
        input_messages=[
            {"role": "system", "content": COMPANY_RESEARCH_PROMPT},
            {
                "role": "user",
                "content": (
                    f"{identity}\n\n以下是联网研究备忘录。请严格基于备忘录生成结构化"
                    f"初始假设，不得补造未出现的公司事实：\n\n{research_text}"
                ),
            },
        ],
        json_schema=COMPANY_RESEARCH_SCHEMA,
    )
    result = json.loads(_response_text(parsed))
    result = _balance_segment_revenue(result)
    result["symbol"] = (
        company.symbol if company.symbol and company.symbol != "待确认"
        else result.get("symbol", "")
    )
    result["sources"] = deduplicated_sources
    result["disclosure_provider"] = disclosure.provider
    result["source_category"] = disclosure.source_category
    result["disclosure_status"] = disclosure.status
    result["actual_total_revenue"] = disclosure.total_revenue
    result["actual_gross_profit"] = disclosure.gross_profit
    result["actual_gross_margin"] = disclosure.gross_margin
    result["actual_net_profit"] = disclosure.net_profit
    result["actual_net_margin"] = disclosure.net_margin
    result["company_financial_totals"] = disclosure.company_financial_totals
    result["segment_historical_totals"] = disclosure.segment_historical_totals
    result["raw_historical_segment_pool"] = disclosure.raw_historical_segment_pool
    result["requested_split_basis"] = requested_split_label
    result["requested_split_mode"] = _split_mode(split_basis)
    result["actual_split_dimension"] = disclosure.segment_dimension
    result["available_split_dimensions"] = disclosure.available_dimensions
    disclosure_access_blocked = (
        disclosure.status in DISCLOSURE_ACCESS_BLOCKED_STATUSES
        and not disclosure.segments
        and not disclosure.available_dimensions
    )
    result["disclosure_access_blocked"] = disclosure_access_blocked
    result["split_basis_satisfied"] = (
        _split_satisfied(split_basis, disclosure) or force_custom_split
    )
    result["split_basis_unavailable"] = (
        _split_mode(split_basis) not in {"", "auto"}
        and not result["split_basis_satisfied"]
        and not disclosure_access_blocked
    )
    result["split_basis_force_estimated"] = force_custom_split
    normalized = normalize_assumptions(result)
    _years = sorted(int(y) for y in normalized.get("yearly_profit_assumptions", {}))
    if not _years:
        from modeling.engine import assumption_forecast_years
        _years = assumption_forecast_years(normalized, 5)
    normalized["rationale_items"] = generate_rationale_items(normalized, _years)
    return normalized, f"{disclosure.provider} + OpenAI 研究"


def _extract_amount(text: str) -> float:
    amount_phrases = re.findall(
        r"(\d+(?:\.\d+)?)\s*(亿元|亿|百万元|百万|万元|万)", text
    )
    match = amount_phrases[-1] if amount_phrases else None
    if not match:
        return 1000.0
    amount = float(match[0])
    unit = match[1]
    if unit in {"亿元", "亿"}:
        return amount * 100
    if unit in {"万元", "万"}:
        return amount / 100
    return amount


def _segment_names(text: str) -> list[str]:
    candidates = [
        ("订阅", "订阅业务"),
        ("SaaS", "订阅业务"),
        ("软件", "软件业务"),
        ("硬件", "硬件业务"),
        ("服务", "服务业务"),
        ("广告", "广告业务"),
        ("电商", "电商业务"),
        ("门店", "线下门店"),
        ("海外", "海外业务"),
        ("国内", "国内业务"),
        ("会员", "会员业务"),
    ]
    names = []
    for keyword, name in candidates:
        if keyword.lower() in text.lower() and name not in names:
            names.append(name)
    return names[:4] or ["核心业务", "成长业务"]


def _company_name_from_idea(text: str) -> str:
    """从用户描述中提取公司名，找不到则返回"示例公司"。"""
    # 优先匹配包含公司/集团/控股后缀的名称
    match = re.search(
        r"([\u4e00-\u9fa5A-Za-z·.\d]{2,20}(?:股份|集团|控股|科技|酒业|制造|公司)有限公司?)",
        text,
    )
    if match:
        return match.group(1)
    match = re.search(r"([\u4e00-\u9fa5]{2,8}(?:集团|控股|科技|酒业|制造))", text)
    if match:
        return match.group(1)
    match = re.search(r"\b([A-Za-z][A-Za-z.&\s]{1,30}(?:Inc|Corp|Ltd|Limited))\b", text)
    if match:
        return match.group(1).strip()
    return "示例公司"


def fallback_assumptions(user_idea: str) -> dict[str, Any]:
    total_revenue = _extract_amount(user_idea)
    names = _segment_names(user_idea)
    weights = [0.65, 0.35] if len(names) == 2 else [1 / len(names)] * len(names)
    if len(names) > 2:
        weights[-1] += 1 - sum(weights)

    segments = []
    for index, (name, weight) in enumerate(zip(names, weights)):
        base_growth = max(0.08, 0.18 - index * 0.035)
        base_margin = min(0.72, 0.42 + index * 0.06)
        segments.append(
            {
                "name": name,
                "base_revenue": total_revenue * weight,
                "bull_growth": base_growth + 0.06,
                "base_growth": base_growth,
                "bear_growth": max(base_growth - 0.09, -0.03),
                "bull_gross_margin": base_margin + 0.04,
                "base_gross_margin": base_margin,
                "bear_gross_margin": max(base_margin - 0.06, 0.15),
            }
        )

    result = {
        "company_name": _company_name_from_idea(user_idea),
        "currency": "人民币百万元",
        "rationale": (
            "当前结果基于通用规则形成初始估算：按用户提到的业务关键词拆分收入，"
            "并以增长、毛利率驱动三种情景；经营费用率和其他损益率按逐年统一假设计算。"
            "补充更完整的公司经营资料后，系统可据此更新研究结果。"
        ),
        "segments": segments,
        "growth_scenario_spread": 0.05,
        "gross_margin_scenario_spread": 0.03,
        "base_opex_ratio": 0.23,
        "base_other_ratio": 0.0,
        "opex_ratio_annual_change": 0.0,
        "other_ratio_annual_change": 0.0,
        "tax_rate": 0.25,
    }
    normalized = normalize_assumptions(result)
    _years = sorted(int(y) for y in normalized.get("yearly_profit_assumptions", {}))
    if not _years:
        from modeling.engine import assumption_forecast_years
        _years = assumption_forecast_years(normalized, 5)
    normalized["rationale_items"] = generate_rationale_items(normalized, _years)
    return normalized


def generate_assumptions(user_idea: str) -> tuple[dict[str, Any], str]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return fallback_assumptions(user_idea), "通用规则初始估算"

    response = _create_response(
        model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        input_messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_idea},
        ],
    )
    content = _response_text(response).strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.MULTILINE).strip()
    normalized = normalize_assumptions(json.loads(content))
    _years = sorted(int(y) for y in normalized.get("yearly_profit_assumptions", {}))
    if not _years:
        from modeling.engine import assumption_forecast_years
        _years = assumption_forecast_years(normalized, 5)
    normalized["rationale_items"] = generate_rationale_items(normalized, _years)
    return normalized, "OpenAI"
