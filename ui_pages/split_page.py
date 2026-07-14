"""业务拆分与基期数据页 — Step 3 分部收入、毛利率与拆分口径。

从原 app.py 的 show_initial_split() 迁移，保持业务逻辑不变。
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from modeling.company_data import CompanyCandidate
from modeling.disclosures import parse_uploaded_annual_report
from modeling.generator import fallback_company_assumptions

from ui_pages.constants import SPLIT_DIMENSION_LABELS
from ui_pages.state import (
    get_assumptions,
    render_next_step_button,
    replace_assumptions_state,
    require_assumptions,
)


def render_split_page() -> None:
    """渲染业务拆分页。"""
    if not require_assumptions():
        return

    assumptions = get_assumptions()
    if not assumptions:
        return

    from ui_pages.theme import render_page_header
    render_page_header("Step 3", "业务拆分与基期数据", "查看分部收入、毛利率与拆分口径，确认业务拆分质量。")

    data_quality = assumptions.get("data_quality", "初始估算")
    is_estimated = data_quality not in {"公司披露分部 + 建模假设"}
    quality_display = (
        "公司披露分部" if data_quality == "公司披露分部 + 建模假设" else data_quality
    )
    source_category = assumptions.get("source_category", "未配置")
    disclosure_provider = assumptions.get("disclosure_provider", "未配置")
    requested_basis = assumptions.get("requested_split_basis") or "自动选择"
    actual_dimension = assumptions.get("actual_split_dimension")
    split_basis_display = (
        requested_basis
        if assumptions.get("split_basis_force_estimated")
        else SPLIT_DIMENSION_LABELS.get(actual_dimension, requested_basis)
    )

    # 来源信息（已在顶部状态条显示公司名称和基期，此处只显示拆分相关属性）
    st.caption(
        f"**来源类型**：{source_category}　|　"
        f"**披露数据源**：{disclosure_provider}　|　"
        f"**拆分口径**：{split_basis_display}"
    )

    if is_estimated:
        st.warning(
            f"⚠ 当前业务拆分为{quality_display}，不等同于公司披露。"
            "所有分部数据需结合官方资料进一步确认。"
        )

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
        (
            f"{float(actual_gross_profit):,.1f}"
            if actual_gross_profit is not None
            else "未取得"
        ),
    )
    actuals[2].metric(
        "公司实际毛利率",
        (
            f"{float(actual_gross_margin):.1%}"
            if actual_gross_margin is not None
            else "未取得"
        ),
    )
    actuals[3].metric(
        "公司实际净利率",
        (
            f"{float(actual_net_margin):.1%}"
            if actual_net_margin is not None
            else "未取得"
        ),
    )
    st.caption(
        f"公司合计金额单位：{assumptions.get('currency', '人民币百万元')}；"
        "净利率按取得的公司披露净利润 ÷ 营业收入计算。"
    )

    # 分部表格（使用四类信息身份标签标注数据性质）
    from ui_pages.theme import (
        info_disclosure,
        info_ai_estimate,
        info_user_confirmed,
        info_risk,
    )

    # 图例说明（颜色 + 文字双重表达）
    st.markdown(
        f"**数据性质图例**："
        f"{info_disclosure('公司披露')} "
        f"{info_ai_estimate('模型估算')} "
        f"{info_user_confirmed('用户定义')}",
        unsafe_allow_html=True,
    )

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

        # 数据性质文字标签（颜色在上方图例统一说明）
        if basis == "reported":
            nature_label = "公司披露"
        elif basis == "user_defined":
            nature_label = "用户定义"
        else:
            nature_label = "模型估算"

        # 毛利率性质文字标签
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
                "披露利润指标": segment.get("profit_metric_name") or "—",
                "披露利润": (
                    f"{float(segment['reported_profit']):,.1f}"
                    if segment.get("reported_profit") is not None
                    else "未披露"
                ),
                "披露利润率": (
                    f"{float(segment['reported_profit_margin']):.1%}"
                    if segment.get("reported_profit_margin") is not None
                    else "未披露"
                ),
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
            "披露利润": st.column_config.TextColumn(width="medium"),
            "披露利润率": st.column_config.TextColumn(width="medium"),
            "业务说明": st.column_config.TextColumn(width="large"),
            "资料依据": st.column_config.TextColumn(width="large"),
        },
    )
    st.caption(assumptions.get("rationale", ""))

    # Phase 12B-0：对账状态提示
    _render_profit_metric_reconciliation_status(assumptions)

    # 年报上传解析（原 Step 2 中的逻辑）
    symbol = assumptions.get("symbol") or assumptions.get("ticker", "")
    _render_annual_report_upload(assumptions, symbol)

    render_next_step_button("split")


def _render_profit_metric_reconciliation_status(assumptions: dict) -> None:
    """Phase 12B-0：对被拦截的利润指标给出清晰页面提示。

    如果 evidence 中包含对账失败/警告信息，在表格下方明确显示，
    不只写入隐藏的 evidence 文本。
    """
    intercepted_segments = []
    warning_segments = []

    for seg in assumptions.get("segments", []):
        evidence = seg.get("evidence", "") or ""
        name = seg.get("name", "")
        profit_metric = seg.get("profit_metric_name", "")

        if "未通过对账校验" in evidence:
            # 提取错误原因
            reason = evidence.split("未通过对账校验：")[1].split("。")[0] if "未通过对账校验：" in evidence else "指标异常"
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


def _render_annual_report_upload(assumptions: dict, symbol: str) -> None:
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
                # 上传年报：保留当前公司，清除旧的内置快照/估算提示
                replace_assumptions_state(
                    refreshed,
                    "上传的官方年度报告 + 建模假设",
                    fallback_message=None,
                )
                st.rerun()
            else:
                st.error("；".join(packet.notes))
