"""历史业务与财务资料页 — Step 2（Phase 12B-1）。

合并旧"资料与证据"页与"业务拆分与基期数据"页，展示：
- 多历史财年的分部收入、收入占比、毛利率
- 财年性质标注（FY2025（公司财年，截至 2025-12-31））
- 口径映射与可比性状态（direct / sum_of_components / residual / unmapped）
- 可展开的来源、单位、资料性质、口径与对账状态
- 年报上传解析
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from modeling.company_data import CompanyCandidate
from modeling.disclosures import parse_uploaded_annual_report
from modeling.generator import fallback_company_assumptions
from modeling.historical_comparability import (
    build_comparability_matrix,
    can_compute_cagr,
    get_available_historical_years,
    get_comparability_summary,
    select_historical_years,
    COMPARABILITY_LABELS,
    DIRECT,
    SUM_OF_COMPONENTS,
    RESIDUAL,
    UNMAPPED,
)

from ui_pages.constants import SPLIT_DIMENSION_LABELS
from ui_pages.state import (
    get_assumptions,
    get_selected_company,
    render_next_step_button,
    replace_assumptions_state,
    require_assumptions,
)


def render_history_page() -> None:
    """渲染历史业务与财务资料页。"""
    if not require_assumptions():
        return

    assumptions = get_assumptions()
    if not assumptions:
        return

    from ui_pages.theme import render_page_header, render_section_header
    render_page_header(
        "Step 2",
        "历史业务与财务资料",
        "查看分部收入、毛利率、历史趋势与口径可比性。",
    )

    segments = assumptions.get("segments", [])

    # ── 来源与质量信息（紧凑展示） ──────────────────────────
    _render_source_summary(assumptions)

    # ── 公司合计指标 ──────────────────────────────────────
    render_section_header(
        "公司财务概览",
        "先确认公司合计口径，再向下核对分部历史表现。",
    )
    _render_company_totals(assumptions)

    # ── 历史期间选择 ──────────────────────────────────────
    all_historical_years = get_available_historical_years(segments)
    render_section_header(
        "选择历史期间",
        "默认展示最近三个可比财年；选择后向下查看分部表现。",
    )
    selected_years, selection_label = _render_period_selector(all_historical_years)

    if not selected_years:
        st.warning("当前数据源未提供历史多期数据，仅展示最近一期分部数据。")
        _render_single_period_table(assumptions)
        _render_source_details(assumptions)
        _render_annual_report_upload(assumptions)
        _render_reconciliation_status(assumptions)
        render_next_step_button("source")
        return

    # ── 财年性质标注 ────────────────────────────────────────
    _render_fiscal_year_nature(assumptions, selected_years)

    # ── 历史分部表格（多年度） ─────────────────────────────
    _render_historical_table(assumptions, segments, selected_years)

    # ── 可比性矩阵 ────────────────────────────────────────
    _render_comparability_matrix(segments, selected_years, assumptions)

    # ── 来源备查（可展开） ─────────────────────────────────
    _render_source_details(assumptions)

    # ── 对账状态 ──────────────────────────────────────────
    _render_reconciliation_status(assumptions)

    # ── 年报上传解析 ──────────────────────────────────────
    _render_annual_report_upload(assumptions)

    render_next_step_button("source")


# ── 子组件 ───────────────────────────────────────────────


def _render_source_summary(assumptions: dict) -> None:
    """渲染来源与质量摘要（紧凑）。"""
    from ui_pages.theme import (
        info_disclosure,
        info_ai_estimate,
        info_risk,
    )

    source_category = assumptions.get("source_category", "未配置")
    data_quality = assumptions.get("data_quality", "初始估算")
    disclosure_provider = assumptions.get("disclosure_provider", "未配置")
    requested_basis = assumptions.get("requested_split_basis") or "自动选择"
    actual_dimension = assumptions.get("actual_split_dimension")
    split_basis_display = (
        requested_basis
        if assumptions.get("split_basis_force_estimated")
        else SPLIT_DIMENSION_LABELS.get(actual_dimension, requested_basis)
    )

    is_disclosed = "披露" in source_category or "快照" in source_category
    is_estimated = "估算" in source_category or "无匹配" in source_category or "占位" in source_category

    if is_disclosed:
        source_tag = info_disclosure("公司披露")
    elif is_estimated:
        source_tag = info_ai_estimate("模型估算")
    else:
        source_tag = info_risk("待核验")

    import html as _html
    safe_source_category = _html.escape(str(source_category))
    safe_data_quality = _html.escape(str(data_quality))
    safe_split_basis_display = _html.escape(str(split_basis_display))
    st.markdown(
        '<div class="td-meta-strip">'
        f'<span><strong>来源</strong>　{safe_source_category} {source_tag}</span>'
        '<span class="td-meta-divider">/</span>'
        f'<span><strong>资料质量</strong>　{safe_data_quality}</span>'
        '<span class="td-meta-divider">/</span>'
        f'<span><strong>拆分口径</strong>　{safe_split_basis_display}</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    # 公司身份核验状态
    saved_company = get_selected_company()
    if saved_company:
        saved_candidate = CompanyCandidate.from_dict(saved_company)
        status = saved_candidate.effective_verification_status
        if status == "user_confirmed_pending_verification":
            st.warning("⚠ 公司身份待核验：该证券由用户确认但未经官方目录核验，研究结果不等于公司披露。")
        elif status == "unresolved":
            st.warning("⚠ 公司身份未确认：该证券尚未通过核验。")

    # fallback 持久提示
    fallback_msg = st.session_state.get("fallback_message")
    if fallback_msg:
        if "内置官方快照" in fallback_msg:
            st.info(fallback_msg)
        elif "估算" in fallback_msg:
            st.warning(fallback_msg)


def _render_company_totals(assumptions: dict) -> None:
    """渲染公司合计指标卡片。"""
    total = sum(float(item.get("base_revenue", 0)) for item in assumptions["segments"])
    actual_total = assumptions.get("actual_total_revenue") or total
    actual_gross_profit = assumptions.get("actual_gross_profit")
    actual_gross_margin = assumptions.get("actual_gross_margin")
    actual_net_margin = assumptions.get("actual_net_margin")

    actuals = st.columns(4)
    actuals[0].metric(
        "公司合计收入",
        f"{float(actual_total):,.1f}" if actual_total is not None else "未取得",
    )
    actuals[1].metric(
        "公司合计毛利",
        f"{float(actual_gross_profit):,.1f}" if actual_gross_profit is not None else "未取得",
    )
    actuals[2].metric(
        "公司毛利率",
        f"{float(actual_gross_margin):.1%}" if actual_gross_margin is not None else "未取得",
    )
    actuals[3].metric(
        "公司净利率",
        f"{float(actual_net_margin):.1%}" if actual_net_margin is not None else "未取得",
    )
    st.caption(f"金额单位：{assumptions.get('currency', '人民币百万元')}")


def _render_period_selector(all_years: list[str]) -> tuple[list[str], str]:
    """渲染历史期间选择器，返回选中的年度列表和标签。"""
    if not all_years:
        return [], ""

    # 默认选中"最近 3 年"（index=1）。选择器独占整行，避免窄列换行。
    selection = st.radio(
        "历史期间",
        ["最近 1 年", "最近 3 年", "最近 5 年", "自定义起止财年"],
        index=1,
        label_visibility="collapsed",
        horizontal=True,
        key="_history_period_selection",
    )

    selection_map = {
        "最近 1 年": "recent_1",
        "最近 3 年": "recent_3",
        "最近 5 年": "recent_5",
        "自定义起止财年": "custom",
    }
    selection_key = selection_map.get(selection, "recent_3")

    custom_start = None
    custom_end = None
    if selection_key == "custom":
        col_start, col_end = st.columns(2)
        with col_start:
            custom_start = st.text_input(
                "起始财年",
                value=all_years[0] if all_years else "",
                placeholder="如 2022",
                key="_history_custom_start",
            )
        with col_end:
            custom_end = st.text_input(
                "结束财年",
                value=all_years[-1] if all_years else "",
                placeholder="如 2025",
                key="_history_custom_end",
            )

    selected = select_historical_years(
        all_years, selection_key, custom_start, custom_end
    )

    if selected:
        year_labels = [f"FY{y}" for y in selected]
        st.caption(f"已选期间：{', '.join(year_labels)}")
        # 不足 3 年时明确提示
        if selection_key == "recent_3" and len(selected) < 3:
            st.caption(
                f"⚠ 当前仅取得 {len(selected)} 个历史财年"
                f"（{', '.join(year_labels)}），已展示全部可用年度。"
            )
        elif selection_key == "recent_5" and len(selected) < 5:
            st.caption(
                f"⚠ 当前仅取得 {len(selected)} 个历史财年"
                f"（{', '.join(year_labels)}），已展示全部可用年度。"
            )
    else:
        st.caption("未匹配到历史年度")

    return selected, selection


def _render_fiscal_year_nature(assumptions: dict, selected_years: list[str]) -> None:
    """渲染财年性质标注。

    Section 6：必须从真实 period_end_date 读取，没有截止日期时显示"截止日期未取得"。
    """
    fiscal_year = assumptions.get("fiscal_year", "")
    currency = assumptions.get("currency", "人民币百万元")

    from modeling.flow_contract import format_fiscal_year

    # 从 historical_periods 中读取真实 period_end_date（取最近年度的记录）
    period_end_date = _get_latest_period_end_date(assumptions.get("segments", []))

    # 最近披露年度：传入真实 period_end_date
    if fiscal_year:
        if period_end_date:
            latest_label = format_fiscal_year(fiscal_year, period_end_date)
        else:
            # 没有截止日期时明确提示，不默认猜测 12 月 31 日
            latest_label = f"FY{fiscal_year}（公司财年，截止日期未取得）"
    else:
        latest_label = "未确定财年"

    # 预测首年 — Phase 12B-2：使用侧边栏 year_count 而非硬编码 5
    from modeling.engine import assumption_forecast_years
    from ui_pages.state import get_year_count
    year_count = get_year_count()
    forecast_years = assumption_forecast_years(assumptions, year_count)
    first_forecast = f"FY{forecast_years[0]}E" if forecast_years else "未确定"

    import html as _html
    st.markdown(
        '<div class="td-note-line">'
        f'<strong>最近披露财年</strong>　{_html.escape(str(latest_label))}'
        '　<span class="td-meta-divider">/</span>　'
        f'<strong>预测首年</strong>　{_html.escape(str(first_forecast))}'
        '　<span class="td-meta-divider">/</span>　'
        f'<strong>币种 / 单位</strong>　{_html.escape(str(currency))}'
        '</div>',
        unsafe_allow_html=True,
    )


def _render_historical_table(
    assumptions: dict,
    segments: list[dict],
    selected_years: list[str],
) -> None:
    """渲染多历史年度分部表格。

    列为财年或报告期，行为业务分部和指标。
    """
    from ui_pages.theme import (
        info_disclosure,
        info_ai_estimate,
        info_user_confirmed,
    )

    # 图例
    st.markdown(
        f"**数据性质图例**："
        f"{info_disclosure('公司披露')} "
        f"{info_ai_estimate('模型估算')} "
        f"{info_user_confirmed('用户定义')}",
        unsafe_allow_html=True,
    )

    # 构建表格数据：行为分部+指标，列为年度
    rows = []

    # 每个分部展示：收入、收入性质、收入占比、毛利率、毛利率性质
    for segment in segments:
        seg_name = segment["name"]
        historical_periods = segment.get("historical_periods", [])

        # 收入行（数值）
        revenue_row = {"业务分部 / 指标": f"{seg_name} — 收入", "数据性质": ""}
        for year in selected_years:
            period = _find_period(historical_periods, year)
            if period:
                revenue = period.get("revenue")
                revenue_row[f"FY{year}"] = (
                    f"{float(revenue):,.1f}" if revenue is not None else "—"
                )
            else:
                revenue_row[f"FY{year}"] = "—"
        rows.append(revenue_row)

        # 收入性质行（逐年度显示 revenue_nature）
        revenue_nature_row = {
            "业务分部 / 指标": f"{seg_name} — 收入性质",
            "数据性质": "逐年度",
        }
        for year in selected_years:
            period = _find_period(historical_periods, year)
            if period:
                nature = str(period.get("revenue_nature", "")).strip().lower()
                channel = str(period.get("revenue_channel", "")).strip()
                label = _nature_label(nature)
                if channel and channel != "—":
                    label = f"{label}（{channel}）"
                revenue_nature_row[f"FY{year}"] = label
            else:
                revenue_nature_row[f"FY{year}"] = "无数据"
        rows.append(revenue_nature_row)

        # 收入占比行
        share_row = {"业务分部 / 指标": f"{seg_name} — 收入占比", "数据性质": ""}
        for year in selected_years:
            period = _find_period(historical_periods, year)
            if period:
                revenue = period.get("revenue")
                if revenue is not None:
                    year_total = _get_year_total(segments, year)
                    if year_total > 0:
                        share = float(revenue) / year_total
                        share_row[f"FY{year}"] = f"{share:.1%}"
                    else:
                        share_row[f"FY{year}"] = "—"
                else:
                    share_row[f"FY{year}"] = "—"
            else:
                share_row[f"FY{year}"] = "—"
        rows.append(share_row)

        # 毛利率行（数值）
        margin_row = {"业务分部 / 指标": f"{seg_name} — 毛利率", "数据性质": ""}
        for year in selected_years:
            period = _find_period(historical_periods, year)
            if period:
                gross_margin = period.get("gross_margin")
                gm_nature = str(period.get("gross_margin_nature", "")).lower()
                if gross_margin is not None:
                    margin_row[f"FY{year}"] = f"{float(gross_margin):.1%}"
                elif gm_nature == "missing":
                    margin_row[f"FY{year}"] = "未披露"
                else:
                    margin_row[f"FY{year}"] = "—"
            else:
                margin_row[f"FY{year}"] = "—"
        rows.append(margin_row)

        # 毛利率性质行（逐年度显示 gross_margin_nature）
        gm_nature_row = {
            "业务分部 / 指标": f"{seg_name} — 毛利率性质",
            "数据性质": "逐年度",
        }
        for year in selected_years:
            period = _find_period(historical_periods, year)
            if period:
                gm_nature = str(period.get("gross_margin_nature", "")).strip().lower()
                gm_source = str(period.get("gross_margin_source_name", "")).strip()
                label = _nature_label(gm_nature)
                if gm_source and gm_source != "—":
                    label = f"{label}（{gm_source}）"
                gm_nature_row[f"FY{year}"] = label
            else:
                gm_nature_row[f"FY{year}"] = "无数据"
        rows.append(gm_nature_row)

    # 公司合计行
    total_row = {"业务分部 / 指标": "公司合计收入", "数据性质": ""}
    for year in selected_years:
        year_total = _get_year_total(segments, year)
        total_row[f"FY{year}"] = f"{year_total:,.1f}" if year_total > 0 else "—"
    rows.append(total_row)

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "业务分部 / 指标": st.column_config.TextColumn(width="medium"),
            "数据性质": st.column_config.TextColumn(width="small"),
        },
    )

    # 可展开的每分部详情
    for segment in segments:
        seg_name = segment["name"]
        historical_periods = segment.get("historical_periods", [])
        if not historical_periods:
            continue

        with st.expander(f"{seg_name} — 历史详情与来源"):
            _render_segment_historical_details(segment, selected_years)


def _render_comparability_matrix(
    segments: list[dict],
    selected_years: list[str],
    assumptions: dict | None = None,
) -> None:
    """渲染口径映射与可比性矩阵。"""
    st.markdown("**口径映射与可比性**")

    # 从 assumptions 获取按财年索引的独立公司总收入
    company_financial_totals = {}
    segment_historical_totals = {}
    if assumptions:
        company_financial_totals = assumptions.get(
            "company_financial_totals", {}
        ) or {}
        segment_historical_totals = assumptions.get(
            "segment_historical_totals", {}
        ) or {}

    # 从 assumptions 获取原始历史分部池和已核验映射记录
    raw_historical_segment_pool = []
    if assumptions:
        raw_historical_segment_pool = assumptions.get(
            "raw_historical_segment_pool", []
        ) or []

    verified_mapping_records = []
    if assumptions:
        verified_mapping_records = assumptions.get(
            "verified_mapping_records", []
        ) or []

    # 当前公司 symbol，用于公司隔离
    symbol = ""
    if assumptions:
        symbol = assumptions.get("symbol") or assumptions.get("ticker", "")

    matrix = build_comparability_matrix(
        segments, selected_years,
        company_financial_totals=company_financial_totals or None,
        raw_historical_segment_pool=raw_historical_segment_pool or None,
        verified_mapping_records=verified_mapping_records or None,
        symbol=symbol or "",
    )

    # 构建可比性表格
    rows = []
    for segment in segments:
        seg_name = segment["name"]
        row = {"业务分部": seg_name}
        cagr_eligible = True

        for year in selected_years:
            mapping = matrix.get((seg_name, str(year)))
            if mapping:
                row[f"FY{year}"] = mapping.label
                if not mapping.can_enter_trend:
                    cagr_eligible = False
            else:
                row[f"FY{year}"] = "无数据"
                cagr_eligible = False

        row["可计算 CAGR"] = "✓ 可计算" if cagr_eligible else "✗ 不可比"

        # 备注
        notes = []
        for year in selected_years:
            mapping = matrix.get((seg_name, str(year)))
            if mapping and mapping.comparability_note:
                notes.append(f"FY{year}: {mapping.comparability_note}")
        row["口径说明"] = " | ".join(notes) if notes else "—"

        rows.append(row)

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "业务分部": st.column_config.TextColumn(width="medium"),
            "口径说明": st.column_config.TextColumn(width="large"),
        },
    )

    # 可比性状态图例
    st.caption(
        f"**可比性状态**：直接可比（可进入趋势计算）| "
        f"组成项加总后可比（可进入趋势计算）| "
        f"公司合计倒算 / 补充项（不可进入趋势）| "
        f"无法可靠映射（不可进入趋势）"
    )

    # Section 12B-1：对账详情展开（区分公司财务报表总收入与分部合计，诚实展示空状态）
    _render_reconciliation_details(
        matrix,
        selected_years,
        company_financial_totals,
        segment_historical_totals,
        verified_mapping_records,
        symbol=symbol or "",
    )


def _render_source_details(assumptions: dict) -> None:
    """渲染来源备查内容（可展开）。"""
    sources = assumptions.get("sources", [])
    disclosure_notes = [
        note for note in assumptions.get("disclosure_notes", []) if note
    ]

    has_content = bool(sources) or bool(disclosure_notes)
    if not has_content:
        return

    with st.expander("来源与披露资料备查", expanded=False):
        if sources:
            st.markdown(f"**公开资料来源（{len(sources)}）**")
            for source in sources:
                title = source.get("title") or source.get("url")
                if source.get("url"):
                    st.markdown(f"- [{title}]({source.get('url')})")
                else:
                    st.write(f"- {title}")

        if disclosure_notes:
            st.markdown("**自动提取说明**")
            for note in disclosure_notes:
                st.write(f"- {note}")

        st.info(
            "TickerDNA 依据公开监管披露和交易所公告，不替代 Bloomberg/Wind 等专业数据终端。\n"
            "- **实时官方披露抓取**：直接读取 SEC EDGAR、HKEXnews、巨潮资讯等官方源\n"
            "- **内置官方快照**：已核验的官方快照，不等于实时官方披露\n"
            "- **结构化 F10 / 公告平台**：东方财富等结构化数据\n"
            "- **模型估算**：依据不足时的保守初始假设"
        )


def _render_reconciliation_status(assumptions: dict) -> None:
    """Phase 12B-0：对被拦截的利润指标给出清晰页面提示。"""
    intercepted_segments = []
    warning_segments = []

    for seg in assumptions.get("segments", []):
        evidence = seg.get("evidence", "") or ""
        name = seg.get("name", "")
        profit_metric = seg.get("profit_metric_name", "")

        if "未通过对账校验" in evidence:
            reason = (
                evidence.split("未通过对账校验：")[1].split("。")[0]
                if "未通过对账校验：" in evidence
                else "指标异常"
            )
            intercepted_segments.append((name, reason))
        elif "指标口径未核验" in evidence:
            warning_segments.append((name, profit_metric or "利润指标"))

    if intercepted_segments:
        st.warning(
            "⚠ **分部利润指标口径校验未通过，已拦截，不用于预测依据**\n\n"
            + "\n".join(
                f"- **{name}**：{reason}（已清除该指标，未用于毛利率预测）"
                for name, reason in intercepted_segments
            )
        )

    if warning_segments:
        st.info(
            "ℹ **部分分部利润指标口径未核验，未用于预测依据**\n\n"
            + "\n".join(
                f"- **{name}**：{metric}（指标定义或口径未确认，未用于预测）"
                for name, metric in warning_segments
            )
        )


def _render_annual_report_upload(assumptions: dict) -> None:
    """渲染年报上传解析逻辑。"""
    disclosure_status = assumptions.get("disclosure_status")
    if disclosure_status in {"document_unavailable", "parser_required"}:
        st.warning(
            "已找到官方年报，但当前数据进程无法自动读取 PDF。"
            "可以从上方官方来源下载完整年报，再在这里上传。"
        )
    elif disclosure_status == "unparsed":
        st.warning(
            "官方年报 PDF 已成功读取，但暂未识别出能与总收入校验通过的业务收入表。"
            "可以上传其他版本的完整年报重新解析。"
        )

    if disclosure_status not in {
        "document_unavailable",
        "parser_required",
        "unparsed",
    }:
        return

    symbol = assumptions.get("symbol") or assumptions.get("ticker", "")
    uploaded_report = st.file_uploader(
        "上传官方年度报告 PDF",
        type=["pdf"],
        key=f"annual_report_upload_{symbol}",
    )
    if uploaded_report and st.button(
        "解析上传年报并替换占位拆分",
        type="primary",
        use_container_width=True,
        key=f"parse_uploaded_report_{symbol}",
    ):
        selected = st.session_state.get("selected_company")
        if not selected:
            st.error("当前没有已选择的公司，请重新搜索后再上传。")
        else:
            company = CompanyCandidate.from_dict(selected)
            with st.spinner("正在解析年报收入表..."):
                packet = parse_uploaded_annual_report(
                    company,
                    uploaded_report.getvalue(),
                    uploaded_report.name,
                )
            if packet.segments:
                refreshed = fallback_company_assumptions(
                    company,
                    disclosure=packet,
                )
                replace_assumptions_state(
                    refreshed,
                    "上传的官方年度报告 + 建模假设",
                    fallback_message=None,
                )
                st.rerun()
            else:
                st.error("；".join(packet.notes))


def _render_single_period_table(assumptions: dict) -> None:
    """无历史多期数据时，展示最近一期分部表格。"""
    from ui_pages.theme import (
        info_disclosure,
        info_ai_estimate,
        info_user_confirmed,
    )

    st.markdown(
        f"**数据性质图例**："
        f"{info_disclosure('公司披露')} "
        f"{info_ai_estimate('模型估算')} "
        f"{info_user_confirmed('用户定义')}",
        unsafe_allow_html=True,
    )

    total = sum(float(item.get("base_revenue", 0)) for item in assumptions["segments"])
    rows = []
    for segment in assumptions["segments"]:
        basis = str(segment.get("basis", "estimated")).lower()
        reported_gross_margin = segment.get("reported_gross_margin")
        margin_basis = str(segment.get("gross_margin_basis", "estimated"))
        gross_margin = (
            float(reported_gross_margin)
            if reported_gross_margin is not None
            else float(segment.get("base_gross_margin", 0))
        )

        if basis == "reported":
            nature_label = "公司披露"
        elif basis == "user_defined":
            nature_label = "用户定义"
        else:
            nature_label = "模型估算"

        margin_basis_map = {
            "reported": "公司披露",
            "derived": "按公司合计反推",
            "estimated": "模型估算",
            "user_defined": "用户定义",
        }
        margin_basis_label = margin_basis_map.get(margin_basis, "模型估算")

        rows.append(
            {
                "业务分部": segment["name"],
                "收入性质": nature_label,
                "基期收入": segment["base_revenue"],
                "收入占比": segment["base_revenue"] / total if total else 0,
                "基期毛利率": gross_margin,
                "毛利率性质": margin_basis_label,
                "业务说明": segment.get("description") or "—",
                "资料依据": segment.get("evidence") or "—",
            }
        )
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
        column_config={
            "基期收入": st.column_config.NumberColumn(format="%.1f"),
            "收入占比": st.column_config.NumberColumn(format="percent"),
            "基期毛利率": st.column_config.NumberColumn(format="percent"),
            "业务说明": st.column_config.TextColumn(width="large"),
            "资料依据": st.column_config.TextColumn(width="large"),
        },
    )
    st.caption(assumptions.get("rationale", ""))


def _render_segment_historical_details(
    segment: dict,
    selected_years: list[str],
) -> None:
    """渲染单个分部的历史详情与来源。"""
    historical_periods = segment.get("historical_periods", [])
    seg_name = segment.get("name", "")

    for year in selected_years:
        period = _find_period(historical_periods, year)
        if not period:
            st.write(f"**FY{year}**：无数据")
            continue

        revenue = period.get("revenue")
        gross_margin = period.get("gross_margin")
        revenue_nature = period.get("revenue_nature", "—")
        revenue_channel = period.get("revenue_channel", "—")
        revenue_source_name = period.get("revenue_source_name", "—")
        revenue_url = period.get("revenue_url", "")
        revenue_publication_date = period.get("revenue_publication_date", "—")
        gm_nature = period.get("gross_margin_nature", "—")
        gm_source_name = period.get("gross_margin_source_name", "—")
        gm_url = period.get("gross_margin_url", "")
        comp_key = period.get("comparability_key", "—")
        comp_note = period.get("comparability_note", "")

        st.markdown(f"**FY{year}**")
        col1, col2 = st.columns(2)
        with col1:
            st.write(f"- 收入：{float(revenue):,.1f}" if revenue is not None else "- 收入：未披露")
            st.write(f"- 收入性质：{_nature_label(revenue_nature)}（{revenue_channel}）")
            st.write(f"- 收入来源：{revenue_source_name}")
            if revenue_url:
                st.write(f"- 收入链接：[查看]({revenue_url})")
            # Section 4.3：发布日期为空时显示"发布日期未取得"
            pub_date_text = revenue_publication_date if revenue_publication_date and revenue_publication_date != "—" else "发布日期未取得"
            st.write(f"- 发布日期：{pub_date_text}")
        with col2:
            if gross_margin is not None:
                st.write(f"- 毛利率：{float(gross_margin):.1%}")
            elif gm_nature == "missing":
                st.write("- 毛利率：未披露")
            else:
                st.write("- 毛利率：—")
            st.write(f"- 毛利率性质：{_nature_label(gm_nature)}")
            st.write(f"- 毛利率来源：{gm_source_name}")
            if gm_url:
                st.write(f"- 毛利率链接：[查看]({gm_url})")
            st.write(f"- 可比性标记：{comp_key}")
        if comp_note:
            st.caption(f"口径说明：{comp_note}")
        st.divider()


# ── 工具函数 ─────────────────────────────────────────────


def _find_period(historical_periods: list[dict], year: str) -> dict | None:
    """在 historical_periods 中查找指定年度的记录。"""
    for period in historical_periods:
        if str(period.get("fiscal_year", "")) == str(year):
            return period
    return None


def _get_year_total(segments: list[dict], year: str) -> float:
    """计算某年度所有分部收入合计。"""
    total = 0.0
    for segment in segments:
        period = _find_period(segment.get("historical_periods", []), year)
        if period and period.get("revenue") is not None:
            total += float(period["revenue"])
    return total


def _get_latest_period_end_date(segments: list[dict]) -> str:
    """从所有分部的 historical_periods 中获取最近年度的 period_end_date。

    Section 6：必须从真实 period_end_date 读取，不默认猜测 12 月 31 日。
    """
    latest_date = ""
    latest_year = ""
    for segment in segments:
        for period in segment.get("historical_periods", []):
            year = str(period.get("fiscal_year", ""))
            end_date = str(period.get("period_end_date", "")).strip()
            if not year or not end_date:
                continue
            # 取年度最大的 period_end_date
            if year > latest_year or (year == latest_year and end_date > latest_date):
                latest_year = year
                latest_date = end_date
    return latest_date


def _nature_label(value: str) -> str:
    """将 revenue_nature / gross_margin_nature 值转为用户友好标签。

    Section 3：逐年度显示数据性质。
    Section 4.5：区分 F10 结构化数据与公司直接披露。
    """
    nature_map = {
        "snapshot": "官方快照",
        "reported": "公司披露",
        "f10_structured": "公开结构化数据（F10）",
        "derived": "模型推算",
        "residual": "公司合计倒算",
        "missing": "未披露",
        "estimated": "模型估算",
        "user_defined": "用户定义",
    }
    if not value:
        return "—"
    return nature_map.get(str(value).strip().lower(), str(value))


def _render_reconciliation_details(
    matrix: dict,
    selected_years: list[str],
    company_financial_totals: dict,
    segment_historical_totals: dict,
    verified_mapping_records: list[dict],
    symbol: str = "",
) -> None:
    """Phase 12B-1：渲染对账详情展开区域。

    明确区分：
    - 公司财务报表总收入（独立来源，如合并利润表营业收入）；
    - 主营构成分部合计（F10 主营构成表的合计行或分部明细之和）；
    - 两者差额；
    - 是否具备 residual 条件。

    没有独立公司总收入时显示明确空状态。
    没有 verified mapping 时显示"暂无已核验的跨年度口径映射案例"。
    不让整个对账区域静默消失。
    """
    with st.expander("对账详情（公司总收入 / 分部合计 / 口径映射）", expanded=False):
        # ── 1. 独立公司总收入 vs 分部合计 对账 ──────────────────
        st.markdown("**公司财务报表总收入与主营构成分部合计对账**")

        has_company_total = bool(company_financial_totals)
        has_segment_total = bool(segment_historical_totals)

        if not has_company_total and not has_segment_total:
            st.info(
                "缺少独立公司财务总收入，暂无法倒算其他业务；"
                "同时缺少主营构成分部合计。当前阶段尚未接入独立合并利润表/财务报表接口。"
            )
        elif not has_company_total:
            st.warning(
                "缺少独立公司财务总收入，暂无法倒算其他业务。"
                "当前仅取得主营构成分部合计（来自 F10 主营构成表，"
                "标记为 segment_table_total / segment_sum），"
                "不得用于 residual 倒算。"
            )
            # 仍展示分部合计供参考
            if has_segment_total:
                for year in selected_years:
                    seg_total = segment_historical_totals.get(str(year), {})
                    if seg_total:
                        _render_total_row(
                            f"FY{year} 主营构成分部合计",
                            seg_total,
                            is_company_total=False,
                        )

        elif not has_segment_total:
            st.caption("缺少主营构成分部合计，仅有独立公司财务总收入。")
            for year in selected_years:
                comp_total = company_financial_totals.get(str(year), {})
                if comp_total:
                    _render_total_row(
                        f"FY{year} 公司财务报表总收入",
                        comp_total,
                        is_company_total=True,
                    )

        else:
            # 两者都有，逐年度展示差额与 residual 条件
            for year in selected_years:
                comp_total = company_financial_totals.get(str(year), {})
                seg_total = segment_historical_totals.get(str(year), {})
                if not comp_total and not seg_total:
                    continue
                _render_year_reconciliation(year, comp_total, seg_total)

        # ── 2. residual 条件说明 ────────────────────────────────
        st.markdown("**Residual 倒算条件**")
        if not has_company_total:
            st.caption("缺少独立公司财务总收入，暂无法倒算其他业务。")
        else:
            residual_ok = any(
                _check_residual_conditions(matrix, year)
                for year in selected_years
            )
            if residual_ok:
                st.caption("已具备 residual 条件（独立公司总收入 + 完整已知分部字段）。")
            else:
                st.caption(
                    "已取得独立公司总收入，但已知分部字段不完整或存在重叠，"
                    "暂不满足 residual 倒算条件。"
                )

        # ── 3. 已核验映射案例 ───────────────────────────────────
        st.markdown("**跨年度口径映射（已核验）**")
        verified_mappings = _filter_verified_mappings(
            verified_mapping_records, selected_years, symbol=symbol
        )
        if not verified_mappings:
            st.caption("暂无已核验的跨年度口径映射案例。")
        else:
            for record in verified_mappings:
                st.write(
                    f"- {record.get('target_segment', '—')} — FY{record.get('fiscal_year', '—')}："
                    f"由 {', '.join(record.get('source_segments', []))} 加总"
                    f"（来源：{record.get('mapping_source', '—')}，"
                    f"证据：{record.get('evidence', '—')}）"
                )

        # ── 4. 组成项加总 / 残差倒算 明细 ───────────────────────
        has_component_details = any(
            mapping.status in ("sum_of_components", "residual")
            or (mapping.status == "unmapped" and mapping.component_details)
            for mapping in matrix.values()
        )
        if has_component_details:
            st.markdown("**组成项加总 / 残差倒算明细**")
            for (seg_name, year), mapping in sorted(matrix.items()):
                if year not in [str(y) for y in selected_years]:
                    continue
                if not mapping.component_details and not mapping.target_value:
                    continue
                _render_mapping_detail(seg_name, year, mapping)


def _render_total_row(
    label: str,
    total: dict,
    *,
    is_company_total: bool,
) -> None:
    """渲染单行总收入/合计行。"""
    revenue = total.get("revenue")
    source_type = total.get("source_type", "—")
    source_name = total.get("source_name", "—")
    rev_str = f"{float(revenue):,.1f}" if revenue is not None else "未取得"
    tag = "独立来源" if is_company_total else "分部合计"
    st.write(
        f"- {label}：{rev_str}（{tag}，{source_type}，来源：{source_name}）"
    )


def _render_year_reconciliation(
    year: str,
    company_total: dict,
    segment_total: dict,
) -> None:
    """渲染单年度公司总收入与分部合计的对账。"""
    comp_rev = company_total.get("revenue")
    seg_rev = segment_total.get("revenue")
    comp_src = company_total.get("source_name", "—")
    seg_src_type = segment_total.get("source_type", "—")

    comp_str = f"{float(comp_rev):,.1f}" if comp_rev is not None else "未取得"
    seg_str = f"{float(seg_rev):,.1f}" if seg_rev is not None else "未取得"

    if comp_rev is not None and seg_rev is not None:
        diff = float(comp_rev) - float(seg_rev)
        diff_str = f"{diff:,.1f}"
    else:
        diff_str = "无法计算"

    st.write(
        f"- FY{year}：公司财务报表总收入 {comp_str}（来源：{comp_src}）"
        f" | 主营构成分部合计 {seg_str}（{seg_src_type}）"
        f" | 两者差额 {diff_str}"
    )


def _check_residual_conditions(matrix: dict, year: str) -> bool:
    """检查指定年度是否具备 residual 倒算条件。"""
    for (seg_name, yr), mapping in matrix.items():
        if str(yr) == str(year) and mapping.status == "residual":
            return True
    return False


def _filter_verified_mappings(
    verified_mapping_records: list[dict],
    selected_years: list[str],
    symbol: str = "",
) -> list[dict]:
    """筛选出选中年度内、当前公司的已核验映射记录。

    Phase 12B-1 收口（页面展示层公司隔离）：
    只返回同时满足以下条件的记录：
    - symbol 与当前公司完全一致；
    - fiscal_year 在当前选择范围内；
    - mapping_source 为 built_in_reviewed 或 user_confirmed；
    - verification_status 为 verified；
    - evidence 非空；
    - source_segments 为非空列表。

    symbol 为空时不得展示任何已核验映射。
    不同公司的同名分部、同年度映射不得出现在当前页面。
    """
    if not verified_mapping_records:
        return []
    # symbol 为空时不得展示任何已核验映射
    if not symbol or not str(symbol).strip():
        return []
    norm_symbol = str(symbol).strip()
    year_set = {str(y) for y in selected_years}
    result = []
    for record in verified_mapping_records:
        rec_symbol = str(record.get("symbol", "")).strip()
        # symbol 必须与当前公司完全一致
        if rec_symbol != norm_symbol:
            continue
        fy = str(record.get("fiscal_year", "")).strip()
        if fy not in year_set:
            continue
        mapping_source = str(record.get("mapping_source", "")).strip().lower()
        verification_status = str(
            record.get("verification_status", "")
        ).strip().lower()
        evidence = str(record.get("evidence", "")).strip()
        source_segments = record.get("source_segments", [])
        if not isinstance(source_segments, list) or not source_segments:
            continue
        if (
            mapping_source in ("built_in_reviewed", "user_confirmed")
            and verification_status == "verified"
            and evidence
        ):
            result.append(record)
    return result


def _render_mapping_detail(seg_name: str, year: str, mapping) -> None:
    """渲染单条映射对账明细。"""
    st.markdown(f"**{seg_name} — FY{year}**")

    if mapping.status == "sum_of_components":
        st.write("对账结论：✓ 组成项加总后可比")
    elif mapping.status == "residual":
        st.write("对账结论：公司合计倒算（不可进入趋势）")
    elif mapping.status == "unmapped" and mapping.component_details:
        st.write("对账结论：✗ 对账失败，已降级为不可比")
    else:
        return

    if mapping.component_details:
        st.write("**组成项明细：**")
        for comp in mapping.component_details:
            rev = comp.get("revenue", 0)
            src = comp.get("source", "—")
            st.write(f"- {comp['name']}：{rev:,.1f}（来源：{src}）")
        if mapping.computed_sum is not None:
            st.write(f"**加总值**：{mapping.computed_sum:,.1f}")

    if mapping.target_value is not None:
        st.write(f"**目标值**：{mapping.target_value:,.1f}")
    if mapping.difference is not None:
        st.write(f"**差额**：{mapping.difference:,.1f}")
    if mapping.error_ratio is not None:
        st.write(f"**误差比例**：{mapping.error_ratio:.2%}")
    if mapping.currency:
        unit_str = f" / {mapping.unit}" if mapping.unit else ""
        st.write(f"**币种/单位**：{mapping.currency}{unit_str}")
    if mapping.reconciliation_detail:
        st.caption(f"对账说明：{mapping.reconciliation_detail}")
    st.divider()
