"""公司与项目页 — Step 1 公司搜索与证券确认。

从原 app.py 迁移，保持业务逻辑不变。
"""
from __future__ import annotations

import logging
import os  # 用于环境变量检查

import streamlit as st

from modeling.company_data import (
    CompanyCandidate,
    LocalSearchProvider,
    candidate_from_ticker,
    can_start_research,
    sec_user_agent_is_configured,
)
from modeling.disclosures import CuratedOfficialDisclosureProvider
from modeling.generator import generate_assumptions, research_company_assumptions
from modeling.workflows import (
    company_switch_cleanup,
    perform_company_search,
    perform_research,
)

from ui_pages.constants import (
    SPLIT_BASIS_OPTIONS,
    available_split_basis_text,
    default_split_choice_for_company,
    split_basis_request,
)
from ui_pages.state import navigate_to, render_next_step_button, replace_assumptions_state

logger = logging.getLogger("tickerdna.app")

# Phase 13：推荐完整体验案例
DEMO_CASES = [
    {
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "label": "Apple（AAPL）",
        "description": "产品分部 · FY2025 · 美元百万元",
    },
    {
        "symbol": "0700.HK",
        "name": "腾讯控股有限公司",
        "label": "腾讯控股（0700.HK）",
        "description": "业务分部 · FY2025 · 人民币百万元",
    },
]


def _render_demo_cases() -> None:
    """渲染推荐完整体验案例区域。

    Phase 13：低干扰的示范案例入口。
    点击只填入搜索查询并触发搜索，不自动开始研究、不自动跳页。
    """
    with st.expander("推荐完整体验案例", expanded=False):
        st.caption(
            "以下案例已内置已核验的官方资料快照，可完整体验搜索→历史→假设→预测→导出全流程。"
            "内置已核验示范案例，不等于实时研究结果。"
        )
        cols = st.columns(len(DEMO_CASES))
        for col, case in zip(cols, DEMO_CASES):
            with col:
                if st.button(
                    case["label"],
                    key=f"demo_case_{case['symbol']}",
                    use_container_width=True,
                ):
                    # 只填入搜索查询并触发搜索，不自动跳页
                    st.session_state["search_query"] = case["symbol"]
                    st.session_state["search_error"] = None
                    st.session_state["search_empty"] = False
                    with st.spinner("正在查找上市公司..."):
                        candidates, error = perform_company_search(case["symbol"])
                        st.session_state["company_candidates"] = [
                            candidate.to_dict() for candidate in candidates
                        ]
                        if error:
                            st.session_state["search_error"] = error
                        elif not candidates:
                            st.session_state["search_empty"] = True
                    st.rerun()
                st.caption(case["description"])


def render_company_page() -> None:
    """渲染公司与项目页。"""
    from ui_pages.theme import render_page_header
    render_page_header("Step 1", "公司与项目", "搜索上市公司并确认证券代码，作为研究起点。")

    # 初始化搜索相关 session state
    if "search_query" not in st.session_state:
        st.session_state["search_query"] = ""
    if "search_error" not in st.session_state:
        st.session_state["search_error"] = None
    if "search_empty" not in st.session_state:
        st.session_state["search_empty"] = False

    with st.form("company_search_form"):
        search_col, button_col = st.columns([5, 1])
        with search_col:
            company_query = st.text_input(
                "公司名称或股票代码",
                placeholder="例如：Apple、AAPL、腾讯控股、0700.HK",
                label_visibility="collapsed",
                value=st.session_state["search_query"],
            )
        with button_col:
            search_clicked = st.form_submit_button(
                "搜索", type="primary", use_container_width=True
            )

    if search_clicked:
        st.session_state["search_query"] = company_query
        st.session_state["search_error"] = None
        st.session_state["search_empty"] = False
        with st.spinner("正在查找上市公司..."):
            candidates, error = perform_company_search(company_query)
            st.session_state["company_candidates"] = [
                candidate.to_dict() for candidate in candidates
            ]
            if error:
                st.session_state["search_error"] = error
            elif not candidates:
                st.session_state["search_empty"] = True

    # ── 推荐完整体验案例 ──────────────────────────────
    # Phase 13：低干扰的示范案例入口，点击只填入搜索框，
    # 不自动开始研究、不自动跳页、不自动打开新窗口。
    _render_demo_cases()

    # 显示搜索结果状态
    search_error = st.session_state.get("search_error")
    search_empty = st.session_state.get("search_empty", False)

    if search_error:
        st.error(search_error)
        if st.button("重试搜索", key="retry_search_error"):
            with st.spinner("正在重新查找上市公司..."):
                candidates, error = perform_company_search(
                    st.session_state.get("search_query", "")
                )
                st.session_state["company_candidates"] = [
                    candidate.to_dict() for candidate in candidates
                ]
                if error:
                    st.session_state["search_error"] = error
                else:
                    st.session_state["search_error"] = None
                    st.session_state["search_empty"] = not candidates
                st.rerun()
    elif search_empty:
        st.warning("没有找到匹配的上市公司，请尝试完整名称或带市场后缀的代码。")
        st.info(
            "💡 修改建议：\n"
            "- 输入股票代码（如 0700.HK、AAPL、600519.SH）\n"
            "- 输入公司完整名称（如「腾讯控股有限公司」）\n"
            "- 如果是港股，尝试带 .HK 后缀；如果是美股，直接用 ticker"
        )
        current_query = st.session_state.get("search_query", "")
        if current_query and "." not in current_query and not current_query.replace(".", "").isdigit():
            st.caption(
                "找不到匹配？你可以改用股票代码搜索，或在下方手工建模。"
            )

    candidate_values = st.session_state.get("company_candidates", [])
    if candidate_values:
        _render_company_selection(candidate_values)

    # 手工建模入口
    with st.expander("找不到公司？改用手工建模描述"):
        idea = st.text_area(
            "输入你的建模思路",
            placeholder=(
                "例如：一家企业软件公司，基期收入约10亿元，收入分为订阅业务和服务业务。"
            ),
            height=100,
        )
        if st.button("根据描述生成初始假设", use_container_width=True):
            if not idea.strip():
                st.warning("请先输入建模思路。")
            else:
                with st.spinner("正在拆分业务并生成假设..."):
                    try:
                        assumptions, source = generate_assumptions(idea)
                        # 手工建模：清除旧 selected_company（避免显示上一家公司）、
                        # 清除旧 fallback_message（避免显示上一家的快照/估算提示）
                        replace_assumptions_state(
                            assumptions, source,
                            fallback_message=None,
                            selected_company=None,
                        )
                        # Phase 12B-1：读取/建模成功后直接进入历史业务与财务资料页
                        navigate_to("source")
                        st.rerun()
                    except Exception:
                        st.error("AI 生成失败，请稍后重试或检查输入内容。")

    # 下一步提示
    if st.session_state.get("assumptions") is not None:
        # 持久显示 fallback 提示（原 app.py 中的全局持久展示）
        fallback_msg = st.session_state.get("fallback_message")
        if fallback_msg:
            if "内置官方快照" in fallback_msg:
                st.info(fallback_msg)
            elif "估算" in fallback_msg:
                st.warning(fallback_msg)

        # 持久展示公司身份核验状态
        saved_company = st.session_state.get("selected_company")
        if saved_company:
            saved_candidate = CompanyCandidate.from_dict(saved_company)
            status = saved_candidate.effective_verification_status
            if status == "user_confirmed_pending_verification":
                st.warning("⚠ 公司身份待核验：该证券由用户确认但未经官方目录核验，研究结果不等于公司披露。")
            elif status == "unresolved":
                st.warning("⚠ 公司身份未确认：该证券尚未通过核验。")

        st.success(f"初始假设已生成 · {st.session_state.get('source', 'AI')}")
        render_next_step_button("company")
    elif st.session_state.get("pending_split_confirmation"):
        st.info("请先确认业务拆分口径，然后前往「历史业务与财务资料」页。")


def _is_exact_match(
    query: str,
    candidate: CompanyCandidate,
    local_provider: LocalSearchProvider,
) -> bool:
    """判断查询是否精确匹配候选项（用于模糊匹配'可能是'提示）。

    规则：
    - 本地索引候选：通过 LocalSearchProvider.is_exact() 检查别名
    - 其他来源候选（SEC/cninfo 等）：规范化后比较名称和代码
    - ticker_fallback / name_fallback：始终为模糊匹配
    """
    if candidate.match_source in ("ticker_fallback", "name_fallback"):
        return False
    if candidate.match_source == "local":
        return local_provider.is_exact(query, candidate)
    # 非本地来源：规范化后比较名称和代码
    from modeling.company_data import _normalize_query
    normalized = _normalize_query(query)
    return normalized in {
        _normalize_query(candidate.name),
        _normalize_query(candidate.symbol),
    }


def _render_company_selection(candidate_values: list) -> None:
    """渲染公司选择和研究执行逻辑。"""
    candidates = [CompanyCandidate.from_dict(value) for value in candidate_values]
    search_query = st.session_state.get("search_query", "")
    _local_provider = LocalSearchProvider()

    def _candidate_label(index: int) -> str:
        """格式化候选项标签，模糊匹配显示'可能是'。"""
        candidate = candidates[index]
        if search_query:
            is_exact = _is_exact_match(search_query, candidate, _local_provider)
            if not is_exact:
                return f"可能是：{candidate.label}"
        return candidate.label

    selected_index = st.selectbox(
        "选择匹配公司",
        options=range(len(candidates)),
        format_func=_candidate_label,
        key="company_select",
    )
    selected_company = candidates[selected_index]

    # 检查是否切换了公司，如果是则清除上一家公司的状态
    if st.session_state.get("last_selected_company_symbol") != selected_company.symbol:
        st.session_state["last_selected_company_symbol"] = selected_company.symbol
        company_switch_cleanup(st.session_state)

    source_messages = {
        "local": "已通过内置上市公司索引识别；后续研究仍会核实公开资料。",
        "sec": "已通过 SEC EDGAR 官方公司目录识别。",
        "cninfo": "已通过巨潮资讯官方证券目录识别。",
        "eastmoney_a_share": "已通过 A 股全市场简称目录识别；后续仍会核实公开披露。",
        "yahoo": "已通过 Yahoo 兼容搜索识别；后续会优先使用官方披露资料。",
        "ticker_fallback": "外部搜索不可用，已按股票代码继续。该代码尚未核验，请确认后才能开始研究。",
        "ticker_override": "已按补充的股票代码识别交易所；后续会核实公开披露。",
        "name_fallback": "外部搜索不可用。请填写有效的六位 A 股代码后才能开始研究。",
    }
    if selected_company.match_source in source_messages:
        st.info(source_messages[selected_company.match_source])

    # name_fallback: 用户必须填写有效的六位A股代码
    if selected_company.symbol == "待确认":
        ticker_override = st.text_input(
            "股票代码",
            placeholder="请填写六位 A 股代码，例如：600519、301165",
            help="系统会根据代码自动识别上海、深圳或北京证券交易所。",
        )
        resolved_company = candidate_from_ticker(
            ticker_override,
            selected_company.name,
        )
        if ticker_override and not resolved_company:
            st.warning("请输入正确的六位 A 股代码；市场后缀可省略。")
        elif resolved_company:
            selected_company = resolved_company
            st.success(
                f"已识别为 {selected_company.name}（{selected_company.symbol}）"
            )

    # ticker_fallback: 需要用户明确确认才能继续研究
    explicit_confirmation_for_ticker_fallback = False
    if selected_company.match_source == "ticker_fallback":
        st.warning("该股票代码未通过官方目录核验。如需继续研究，请先确认。")
        explicit_confirmation_for_ticker_fallback = st.checkbox(
            "我确认该代码对应我要研究的公司",
            help="勾选后可以继续研究，但公司身份仍标记为'待核验'，不会被当作已确认证券。",
        )
        if explicit_confirmation_for_ticker_fallback:
            selected_company = CompanyCandidate(
                symbol=selected_company.symbol,
                name=selected_company.name,
                exchange=selected_company.exchange,
                exchange_name=selected_company.exchange_name,
                quote_type=selected_company.quote_type,
                sector=selected_company.sector,
                industry=selected_company.industry,
                match_source=selected_company.match_source,
                cik=selected_company.cik,
                verification_status="user_confirmed_pending_verification",
            )

    # 证券类型展示
    company_meta = " · ".join(
        part
        for part in [
            selected_company.exchange_name or selected_company.exchange,
            (
                {
                    "EQUITY": "股票",
                    "MUTUALFUND": "基金",
                }.get(selected_company.quote_type, "")
                if selected_company.is_confirmed
                else ""
            ),
            selected_company.sector,
            selected_company.industry,
        ]
        if part
    )
    if company_meta:
        st.caption(company_meta)

    company_context = st.text_input(
        "可选：补充公司或建模背景",
        placeholder="例如：重点关注云业务；以最新完整财年为基期",
    )
    split_options = list(SPLIT_BASIS_OPTIONS)
    default_split_choice = default_split_choice_for_company(selected_company)
    default_split_index = (
        split_options.index(default_split_choice)
        if default_split_choice in split_options
        else 0
    )
    split_choice = st.selectbox(
        "业务拆分方式",
        split_options,
        index=default_split_index,
        key=(
            "split_choice_"
            f"{selected_company.symbol}_{selected_company.cik or selected_company.name}"
        ),
        help="产品、地区、行业会优先读取公司披露数据；自定义口径需在未披露时确认是否坚持。",
    )
    has_curated_snapshot = CuratedOfficialDisclosureProvider().supports(selected_company)
    sec_blocked_for_selected = (
        bool(selected_company.cik)
        and not sec_user_agent_is_configured()
        and not has_curated_snapshot
    )
    sec_uses_snapshot = (
        bool(selected_company.cik)
        and not sec_user_agent_is_configured()
        and has_curated_snapshot
    )
    if sec_blocked_for_selected:
        st.warning(
            "这家公司需要读取美股官方披露。请先在左侧「高级设置」中填写美股官方数据联系方式，"
            "否则无法核验已披露口径。"
        )
    elif sec_uses_snapshot:
        st.info(
            "当前为基于已取得资料形成的初始假设；"
            "如补充更完整的公司经营资料，系统可据此更新研究结果。"
            "未配置美股官方数据联系方式，将使用已核验的内置官方快照继续研究。"
        )
    custom_split_basis = ""
    if SPLIT_BASIS_OPTIONS[split_choice] == "custom":
        custom_split_basis = st.text_input(
            "自定义拆分口径",
            placeholder="例如：按下游应用场景、客户类型、销售渠道拆分",
        )

    # 流程控制
    can_research = can_start_research(
        selected_company,
        explicit_confirmation=explicit_confirmation_for_ticker_fallback,
    )

    if not can_research:
        if selected_company.symbol == "待确认":
            st.warning("请先填写有效的六位 A 股代码，才能开始研究。")
        elif selected_company.match_source == "ticker_fallback":
            st.warning("请确认该股票代码对应正确的公司，才能开始研究。")
        st.button(
            "读取公司资料并生成业务拆分",
            type="primary",
            use_container_width=True,
            disabled=True,
        )
    else:
        research_label = "读取公司资料并生成业务拆分"
        if st.button(research_label, type="primary", use_container_width=True, key="start_research"):
            if sec_blocked_for_selected:
                st.error(
                    "当前未启用 SEC 官方数据源。请在左侧填写 SEC 联系邮箱后再读取，"
                    "系统不会把未核验的占位估算当作公司披露。"
                )
                st.stop()
            split_basis = split_basis_request(split_choice, custom_split_basis)
            if split_basis["mode"] == "custom" and not split_basis["label"].strip():
                st.warning("请输入自定义拆分口径。")
                st.stop()
            st.session_state["pending_split_confirmation"] = None
            st.session_state["research_error"] = None
            st.session_state["fallback_message"] = None
            st.session_state["research_context"] = {
                "company": selected_company.to_dict(),
                "company_context": company_context,
                "split_basis": split_basis,
            }
            with st.spinner("正在核实公司并阅读公开资料，这通常需要几十秒..."):
                assumptions, source, fallback_msg, error_msg, avail_dims = perform_research(
                    selected_company, company_context, split_basis
                )

                if error_msg:
                    st.session_state["research_error"] = error_msg
                elif assumptions is not None and avail_dims is not None:
                    st.session_state["pending_split_confirmation"] = {
                        "company": selected_company.to_dict(),
                        "company_context": company_context,
                        "split_basis": split_basis,
                        "fallback_assumptions": assumptions,
                        "fallback_source": source,
                        "available_dimensions": avail_dims,
                    }
                else:
                    replace_assumptions_state(
                        assumptions,
                        source,
                        fallback_message=fallback_msg,
                        selected_company=selected_company.to_dict(),
                    )
                    # Phase 12B-1：读取成功后直接进入历史业务与财务资料页
                    navigate_to("source")
                st.rerun()

    # 显示研究错误和重试按钮
    research_error = st.session_state.get("research_error")
    if research_error:
        st.error(research_error)
        if st.button("重试研究", key="retry_research_error"):
            ctx = st.session_state.get("research_context", {})
            if ctx:
                company_obj = CompanyCandidate.from_dict(ctx.get("company", {}))
                ctx_context = ctx.get("company_context", "")
                ctx_split = ctx.get("split_basis", {})
                with st.spinner("正在重新核实公司并阅读公开资料..."):
                    assumptions, source, fallback_msg, error_msg, avail_dims = perform_research(
                        company_obj, ctx_context, ctx_split
                    )
                    if error_msg:
                        st.session_state["research_error"] = error_msg
                    elif assumptions is not None and avail_dims is not None:
                        st.session_state["pending_split_confirmation"] = {
                            "company": company_obj.to_dict(),
                            "company_context": ctx_context,
                            "split_basis": ctx_split,
                            "fallback_assumptions": assumptions,
                            "fallback_source": source,
                            "available_dimensions": avail_dims,
                        }
                        st.session_state["research_error"] = None
                    else:
                        replace_assumptions_state(
                            assumptions,
                            source,
                            fallback_message=fallback_msg,
                        )
                        st.session_state["research_error"] = None
                        # Phase 12B-1：重试成功后直接进入历史业务与财务资料页
                        navigate_to("source")
                st.rerun()

    # 拆分口径确认弹窗
    pending_split = st.session_state.get("pending_split_confirmation")
    if pending_split:
        _render_split_confirmation(pending_split)


def _render_split_confirmation(pending_split: dict) -> None:
    """渲染拆分口径确认弹窗。"""
    split_basis = pending_split["split_basis"]
    requested_label = split_basis.get("label", "该拆分口径")
    available_text = available_split_basis_text(
        pending_split.get("available_dimensions")
    )
    pending_company = pending_split.get("company")
    if pending_company:
        pending_candidate = CompanyCandidate.from_dict(pending_company)
        pending_status = pending_candidate.effective_verification_status
        if pending_status == "user_confirmed_pending_verification":
            st.warning(
                "⚠ 公司身份待核验：该证券由用户确认但未经官方目录核验，"
                "后续拆分与估算结果不等于公司披露。"
            )
        elif pending_status == "unresolved":
            st.warning("⚠ 公司身份未确认：该证券尚未通过核验。")

    def confirm_split_content() -> None:
        st.write(
            f"未在当前可结构化财报数据中找到“{requested_label}”。"
            f"已识别到的可用口径为：{available_text}。"
        )
        st.caption(
            "如果坚持该口径，系统会基于公开资料和公司合计数据生成估算拆分，"
            "并在表格中明确标记为估算，不会冒充公司披露。"
        )
        col_use_reported, col_force = st.columns(2)
        with col_use_reported:
            if st.button(
                "改用已披露口径",
                use_container_width=True,
                key="use_reported_split_basis",
            ):
                # 改用已披露口径：清除与新拆分不一致的旧 fallback 提示
                replace_assumptions_state(
                    pending_split["fallback_assumptions"],
                    pending_split["fallback_source"],
                    fallback_message=None,
                    selected_company=pending_split["company"],
                )
                st.session_state["pending_split_confirmation"] = None
                # Phase 12B-1：确认后直接进入历史业务与财务资料页
                navigate_to("source")
                st.rerun()
        with col_force:
            force_label = "坚持该口径，生成估算"
            if st.button(
                force_label,
                type="primary",
                use_container_width=True,
                key="force_custom_split_basis",
            ):
                company = CompanyCandidate.from_dict(pending_split["company"])
                with st.spinner("正在按用户指定口径整理公开资料并生成拆分..."):
                    assumptions, source = research_company_assumptions(
                        company,
                        pending_split.get("company_context", ""),
                        split_basis=split_basis,
                        force_custom_split=True,
                    )
                replace_assumptions_state(
                    assumptions,
                    source,
                    fallback_message=None,
                    selected_company=pending_split["company"],
                )
                st.session_state["pending_split_confirmation"] = None
                # Phase 12B-1：确认后直接进入历史业务与财务资料页
                navigate_to("source")
                st.rerun()

    if hasattr(st, "dialog"):
        @st.dialog("财报未披露该拆分口径")
        def split_basis_dialog() -> None:
            confirm_split_content()

        split_basis_dialog()
    else:
        st.warning("财报未披露该拆分口径")
        confirm_split_content()
