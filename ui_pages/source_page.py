"""资料来源页 — 数据源状态与披露资料质量。

从原 app.py Step 2 中拆分出与来源相关的展示，保持业务逻辑不变。
"""
from __future__ import annotations

import streamlit as st

from ui_pages.constants import SPLIT_DIMENSION_LABELS
from ui_pages.state import (
    get_assumptions,
    get_selected_company,
    render_next_step_button,
    require_assumptions,
)


def render_source_page() -> None:
    """渲染资料来源页。"""
    if not require_assumptions():
        return

    assumptions = get_assumptions()
    if not assumptions:
        return

    from ui_pages.theme import render_page_header
    render_page_header("Step 2", "资料与证据", "查看数据来源、披露资料质量与公司身份核验状态。")

    # 核心来源信息（使用四类信息身份标签）
    from ui_pages.theme import (
        info_disclosure,
        info_ai_estimate,
        info_risk,
        info_missing,
    )

    source_category = assumptions.get("source_category", "未配置")
    disclosure_provider = assumptions.get("disclosure_provider", "未配置")
    data_quality = assumptions.get("data_quality", "初始估算")

    # 判断资料来源性质并使用对应标签
    is_disclosed = "披露" in source_category or "快照" in source_category
    is_estimated = "估算" in source_category or "无匹配" in source_category or "占位" in source_category

    if is_disclosed:
        source_tag = info_disclosure("公司披露")
    elif is_estimated:
        source_tag = info_ai_estimate("模型估算")
    else:
        source_tag = info_risk("待核验")

    # 来源信息（长文本标签用普通文本完整展示，避免 metric 截断）
    import html as _html
    safe_source_category = _html.escape(str(source_category))
    safe_disclosure_provider = _html.escape(str(disclosure_provider))
    safe_data_quality = _html.escape(str(data_quality))
    st.markdown(
        f"**来源类型**：{safe_source_category} {source_tag}  \n"
        f"**披露数据源**：{safe_disclosure_provider}  \n"
        f"**资料质量**：{safe_data_quality}",
        unsafe_allow_html=True,
    )

    # 公司身份核验状态
    saved_company = get_selected_company()
    if saved_company:
        from modeling.company_data import CompanyCandidate
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

    # 拆分口径
    requested_basis = assumptions.get("requested_split_basis") or "自动选择"
    actual_dimension = assumptions.get("actual_split_dimension")
    split_basis_display = (
        requested_basis
        if assumptions.get("split_basis_force_estimated")
        else SPLIT_DIMENSION_LABELS.get(actual_dimension, requested_basis)
    )
    st.caption(f"**拆分口径**：{split_basis_display}")

    # 公开资料来源列表
    sources = assumptions.get("sources", [])
    if sources:
        with st.expander(f"查看公开资料来源（{len(sources)}）"):
            for source in sources:
                title = source.get("title") or source.get("url")
                if source.get("url"):
                    st.markdown(f"- [{title}]({source.get('url')})")
                else:
                    st.write(f"- {title}")
    elif assumptions.get("symbol"):
        st.warning("当前结果没有公开资料来源，属于占位估算，不应直接视为公司披露。")

    # disclosure_notes
    disclosure_notes = [
        note for note in assumptions.get("disclosure_notes", []) if note
    ]
    if disclosure_notes:
        with st.expander("查看自动提取说明"):
            for note in disclosure_notes:
                st.write(f"- {note}")

    # 来源类型说明
    st.info(
        "TickerDNA 依据公开监管披露和交易所公告，不替代 Bloomberg/Wind 等专业数据终端。\n"
        "- **实时官方披露抓取**：直接读取 SEC EDGAR、HKEXnews、巨潮资讯等官方源\n"
        "- **内置官方快照**：已核验的官方快照，不等于实时官方披露\n"
        "- **结构化 F10 / 公告平台**：东方财富等结构化数据\n"
        "- **模型估算**：依据不足时的保守初始假设"
    )

    render_next_step_button("source")
