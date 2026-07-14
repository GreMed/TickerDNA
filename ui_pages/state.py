"""多页面工作台共享状态与导航工具。

提供：
- 页面定义与导航控制
- 上游状态检查与空状态提示
- 每页侧边栏公共信息
- 跨页面共享数据访问
"""
from __future__ import annotations

from typing import Any

import streamlit as st

APP_VERSION = "v0.2.0-beta1"
APP_RELEASE_DATE = "2026-07-15"


# 页面定义（key, 中文名, 描述, 用户友好阶段名）
# Phase 12B-1：五步研究流程
#   1. 公司与项目
#   2. 历史业务与财务资料（合并旧"资料与证据"+"业务拆分"）
#   3. 假设与驱动因子
#   4. 预测与情景
#   5. 导出与交付
# "split" key 保留用于内部兼容（旧 session_state），但不再是独立步骤。
PAGES = [
    ("company", "公司与项目", "公司搜索与证券确认", "公司与项目"),
    ("source", "历史业务与财务资料", "分部收入、毛利率、历史趋势与来源备查", "历史业务与财务资料"),
    ("assumption", "假设与驱动因子", "Base 假设、振幅与预测逻辑", "假设与驱动因子"),
    ("forecast", "预测与情景", "图表、预测结果、情景对比", "预测与情景"),
    ("export", "导出与交付", "Excel 下载与限制说明", "导出与交付"),
]


def current_page() -> str:
    """获取当前页面 key，默认 company。

    Phase 12B-1：旧 "split" key 自动重定向到 "source"。
    """
    page = st.session_state.get("_current_page", "company")
    if page == "split":
        page = "source"
    return page


def navigate_to(page: str) -> None:
    """切换到指定页面。

    Phase 12B-1：旧 "split" key 自动重定向到 "source"（历史业务与财务资料）。
    """
    if page == "split":
        page = "source"
    if any(p[0] == page for p in PAGES):
        st.session_state["_current_page"] = page


def page_order() -> list[str]:
    """返回页面顺序列表。"""
    return [p[0] for p in PAGES]


def next_page(current: str) -> str | None:
    """返回下一页 key，已是最后一页则返回 None。"""
    order = page_order()
    try:
        idx = order.index(current)
    except ValueError:
        return None
    if idx + 1 < len(order):
        return order[idx + 1]
    return None


def prev_page(current: str) -> str | None:
    """返回上一页 key，已是第一页则返回 None。"""
    order = page_order()
    try:
        idx = order.index(current)
    except ValueError:
        return None
    if idx > 0:
        return order[idx - 1]
    return None


# ── 状态检查 ─────────────────────────────────────────────────


# 假设被替换时需要清除的派生状态键
# （_assumptions_reviewed / _forecast_results / _forecast_summary）
# 只有整套 assumptions 被重新生成或替换时才清除，普通页面切换不清除。
_ASSUMPTIONS_DERIVED_KEYS = (
    "_assumptions_reviewed",
    "_forecast_results",
    "_forecast_summary",
)

# 哨兵值：区分"不修改"（_UNSET）与"清除"（None）
_UNSET = object()


def _ss_delete(key: str) -> None:
    """安全删除 session_state 键，兼容真实 Streamlit 和 AppTest。"""
    try:
        st.session_state.pop(key, None)
    except (AttributeError, TypeError):
        if key in st.session_state:
            del st.session_state[key]


def replace_assumptions_state(
    assumptions: dict[str, Any],
    source: Any = _UNSET,
    *,
    fallback_message: Any = _UNSET,
    selected_company: Any = _UNSET,
) -> None:
    """整套 assumptions 被重新生成或替换时的统一状态写入。

    负责：
    - 写入新的 assumptions；
    - 更新 source；
    - 递增 assumption_version；
    - 清除 _assumptions_reviewed（旧确认状态失效）；
    - 清除 _forecast_results（旧预测结果失效）；
    - 清除 _forecast_summary；
    - 按 source / fallback_message / selected_company 参数语义处理对应键：
      * _UNSET（默认）：保留现有状态；
      * 有效值：写入新值；
      * None：删除对应 session_state 键。

    普通页面切换不应调用此函数；只有以下场景使用：
    - 同一公司重新读取资料；
    - 研究失败后重试成功；
    - 改用已披露拆分口径；
    - 坚持用户指定口径并重新生成拆分；
    - 上传年报并替换拆分；
    - 手工建模生成初始假设；
    - 切换公司（切换公司另有 company_switch_cleanup，但如需写入新假设也用此函数）。
    """
    st.session_state["assumptions"] = assumptions

    if source is _UNSET:
        pass  # 保留现有 source
    elif source is None:
        _ss_delete("source")
    else:
        st.session_state["source"] = source

    if fallback_message is _UNSET:
        pass  # 保留现有 fallback_message
    elif fallback_message is None:
        _ss_delete("fallback_message")
    else:
        st.session_state["fallback_message"] = fallback_message

    if selected_company is _UNSET:
        pass  # 保留现有 selected_company
    elif selected_company is None:
        _ss_delete("selected_company")
    else:
        st.session_state["selected_company"] = selected_company

    st.session_state["assumption_version"] = (
        st.session_state.get("assumption_version", 0) + 1
    )
    for key in _ASSUMPTIONS_DERIVED_KEYS:
        _ss_delete(key)


def has_confirmed_company() -> bool:
    """是否已确认公司（selected_company 存在）。"""
    return st.session_state.get("selected_company") is not None


def has_assumptions() -> bool:
    """是否已生成假设。"""
    return st.session_state.get("assumptions") is not None


def has_assumptions_reviewed() -> bool:
    """是否已在假设页确认假设（点击"下一步"进入预测页）。"""
    return st.session_state.get("_assumptions_reviewed", False)


def mark_assumptions_reviewed() -> None:
    """标记假设已确认（用户在假设页点击了"下一步"）。"""
    st.session_state["_assumptions_reviewed"] = True


def get_assumptions() -> dict[str, Any] | None:
    """安全获取 assumptions。"""
    return st.session_state.get("assumptions")


def get_selected_company() -> dict[str, Any] | None:
    """安全获取 selected_company dict。"""
    return st.session_state.get("selected_company")


def get_source() -> str:
    """获取假设来源描述。"""
    return st.session_state.get("source", "")


def get_year_count() -> int:
    """获取预测年数（sidebar 设置）。"""
    return st.session_state.get("_year_count", 5)


def set_year_count(n: int) -> None:
    st.session_state["_year_count"] = n


# ── 空状态提示 ───────────────────────────────────────────────


_PAGE_LABELS = dict((p[0], p[1]) for p in PAGES)


def render_empty_state_with_nav(
    message: str,
    target_page: str,
    button_label: str | None = None,
) -> None:
    """渲染空状态提示 + 导航按钮。

    使用 on_click 回调在脚本重运行前完成导航，
    确保新页面在当前 run 中渲染（无需 st.rerun()）。
    """
    st.info(message)
    default_label = f"前往{_PAGE_LABELS.get(target_page, target_page)}页"
    label = button_label or default_label
    st.button(
        label,
        type="primary",
        use_container_width=True,
        key=f"_empty_nav_{target_page}",
        on_click=navigate_to,
        args=(target_page,),
    )


def require_company() -> bool:
    """检查是否已确认公司，否则显示空状态 + 导航按钮并返回 False。"""
    if has_confirmed_company() or has_assumptions():
        return True
    render_empty_state_with_nav(
        "请先完成公司确认（在「公司与项目」页搜索并选择公司）。",
        "company",
    )
    return False


def require_assumptions() -> bool:
    """检查是否已生成假设（用于 source/split/assumption 页）。"""
    if has_assumptions():
        return True
    if has_confirmed_company():
        render_empty_state_with_nav(
            "请先完成资料读取与业务拆分（在「公司与项目」页读取公司资料）。",
            "company",
        )
    else:
        render_empty_state_with_nav(
            "请先完成公司确认（在「公司与项目」页搜索并选择公司）。",
            "company",
        )
    return False


def require_assumptions_for_forecast() -> bool:
    """检查预测页前置条件。

    顺序：
    - 无公司 → 前往公司页
    - 有公司但无 assumptions（资料读取/拆分未完成）→ 前往公司页
    - 有 assumptions 但未在假设页确认（_assumptions_reviewed 缺失）→ 前往假设页
    - 有 assumptions 且已确认 → 通过
    """
    if has_assumptions():
        if has_assumptions_reviewed():
            return True
        # 拆分已完成但假设编辑状态缺失
        render_empty_state_with_nav(
            "请先完成假设编辑（在「假设与驱动因子」页确认 Base 假设后进入预测）。",
            "assumption",
        )
        return False
    if has_confirmed_company():
        render_empty_state_with_nav(
            "请先完成资料读取与业务拆分（在「公司与项目」页读取公司资料）。",
            "company",
        )
    else:
        render_empty_state_with_nav(
            "请先完成公司确认（在「公司与项目」页搜索并选择公司）。",
            "company",
        )
    return False


def require_forecast_results() -> bool:
    """检查是否已生成预测结果，否则显示空状态 + 导航按钮。"""
    if st.session_state.get("_forecast_results") is not None:
        return True
    if has_assumptions() and has_assumptions_reviewed():
        render_empty_state_with_nav(
            "请先生成预测结果（在「预测与情景」页查看预测）。",
            "forecast",
        )
    elif has_assumptions():
        render_empty_state_with_nav(
            "请先完成假设编辑（在「假设与驱动因子」页确认 Base 假设后进入预测）。",
            "assumption",
        )
    elif has_confirmed_company():
        render_empty_state_with_nav(
            "请先完成资料读取与业务拆分（在「公司与项目」页读取公司资料）。",
            "company",
        )
    else:
        render_empty_state_with_nav(
            "请先完成公司确认（在「公司与项目」页搜索并选择公司）。",
            "company",
        )
    return False


def require_export_ready() -> bool:
    """检查是否已准备好导出，否则显示空状态 + 导航按钮。"""
    if st.session_state.get("_forecast_results") is not None:
        return True
    if has_assumptions() and has_assumptions_reviewed():
        render_empty_state_with_nav(
            "请先生成预测结果后再导出（在「预测与情景」页查看预测）。",
            "forecast",
        )
    elif has_assumptions():
        render_empty_state_with_nav(
            "请先完成假设编辑（在「假设与驱动因子」页确认 Base 假设后进入预测）。",
            "assumption",
        )
    elif has_confirmed_company():
        render_empty_state_with_nav(
            "请先完成资料读取与业务拆分（在「公司与项目」页读取公司资料）。",
            "company",
        )
    else:
        render_empty_state_with_nav(
            "请先完成公司确认（在「公司与项目」页搜索并选择公司）。",
            "company",
        )
    return False


# ── 侧边栏公共信息 ───────────────────────────────────────────


def render_sidebar() -> int:
    """渲染每页侧边栏公共信息，返回预测年数。

    仅保留：预测年数。
    公司名称和阶段已在顶部状态条展示，侧边栏不重复。
    美股官方资料等高级设置移至"高级设置"折叠区。
    """
    st.markdown("**预测年数**")
    year_count = st.slider(
        "预测年数", 3, 10, 5,
        key="_year_count_slider",
        label_visibility="collapsed",
    )
    set_year_count(year_count)

    return year_count


def _assumption_next_callback() -> None:
    """on_click 回调：标记假设已确认并导航到预测页。"""
    mark_assumptions_reviewed()
    nxt = next_page("assumption")
    if nxt:
        navigate_to(nxt)


def render_next_step_button(current: str) -> None:
    """在页面底部渲染"下一步"按钮。

    使用 on_click 回调在脚本重运行前完成导航。
    """
    nxt = next_page(current)
    if nxt is None:
        return
    label = f"下一步：{_PAGE_LABELS.get(nxt, nxt)}"
    st.button(
        label,
        type="primary",
        use_container_width=True,
        key=f"_nav_{current}_to_{nxt}",
        on_click=navigate_to,
        args=(nxt,),
    )


def render_assumption_next_button() -> None:
    """假设页专用"下一步"按钮：点击时标记假设已确认再导航到预测页。

    使用 on_click 回调（_assumption_next_callback）在脚本重运行前
    同时完成 mark_assumptions_reviewed() 和 navigate_to()。
    """
    nxt = next_page("assumption")
    if nxt is None:
        return
    label = f"下一步：{_PAGE_LABELS.get(nxt, nxt)}"
    st.button(
        label,
        type="primary",
        use_container_width=True,
        key="_nav_assumption_to_forecast",
        on_click=_assumption_next_callback,
    )


def render_prev_step_button(current: str) -> None:
    """在页面底部渲染"返回上一步"按钮。

    使用 on_click 回调在脚本重运行前完成导航。
    """
    prev = prev_page(current)
    if prev is None:
        return
    label = f"返回：{_PAGE_LABELS.get(prev, prev)}"
    st.button(
        label,
        use_container_width=True,
        key=f"_prev_{current}_to_{prev}",
        on_click=navigate_to,
        args=(prev,),
    )


def render_page_sidebar_extras(page: str) -> None:
    """在侧边栏渲染当前页面特定的、用户可理解的少量信息。

    每页只展示与当前操作有关的上游信息，不重复堆满所有内容。
    不暴露开发术语。
    """
    assumptions = get_assumptions()
    if not assumptions:
        return

    if page == "source":
        st.markdown(f"- 资料来源：{assumptions.get('source_category', '—')}")
        st.markdown(f"- 资料质量：{assumptions.get('data_quality', '—')}")
        requested = assumptions.get("requested_split_basis") or "自动选择"
        st.markdown(f"- 拆分口径：{requested}")

    elif page == "assumption":
        segments = assumptions.get("segments", [])
        reported = sum(1 for s in segments if s.get("basis") == "reported")
        estimated = sum(1 for s in segments if s.get("basis") == "estimated")
        user_def = sum(1 for s in segments if s.get("basis") == "user_defined")
        st.markdown(f"- 公司披露：{reported} / 模型估算：{estimated} / 用户定义：{user_def}")
        items = assumptions.get("rationale_items", [])
        placeholder_count = sum(1 for it in items if it.get("is_placeholder"))
        if placeholder_count > 0:
            st.caption(f"其中 {placeholder_count} 项为资料不足的初始假设")

    elif page == "forecast":
        growth_spread = assumptions.get("growth_scenario_spread", 0)
        margin_spread = assumptions.get("gross_margin_scenario_spread", 0)
        st.markdown(f"- 增长率振幅：{growth_spread:.1%}")
        st.markdown(f"- 毛利率振幅：{margin_spread:.1%}")
        items = assumptions.get("rationale_items", [])
        low_count = sum(1 for it in items if it.get("confidence") == "low")
        if low_count > 0:
            st.caption(f"其中 {low_count} 项假设依据不足，请谨慎参考预测结果")

    elif page == "export":
        has_forecast = st.session_state.get("_forecast_results") is not None
        st.markdown(f"- 预测结果：{'已生成' if has_forecast else '未生成'}")
        items = assumptions.get("rationale_items", [])
        placeholder_count = sum(1 for it in items if it.get("is_placeholder"))
        if placeholder_count > 0:
            st.caption(f"其中 {placeholder_count} 项为资料不足的初始假设")


def render_data_source_config() -> None:
    """渲染侧边栏高级设置（用户主动展开）。

    包含：美股官方数据联系方式。
    不暴露开发术语。
    """
    import os
    from modeling.company_data import provider_statuses, sec_user_agent_is_configured

    with st.expander("高级设置", expanded=False):
        st.caption("以下设置仅影响美股官方数据获取方式。")
        sec_contact_email = st.text_input(
            "美股官方数据联系方式",
            value=os.getenv("SEC_CONTACT_EMAIL", ""),
            placeholder="name@example.com",
            help="仅用于美股官方数据请求标识，不会发送给任何第三方。",
        ).strip()
        if sec_contact_email and "@" in sec_contact_email:
            os.environ["SEC_USER_AGENT"] = (
                "TickerDNA/1.0 "
                f"{sec_contact_email}"
            )
        for provider in provider_statuses():
            st.markdown(
                f"**{provider['name']}**：{provider['status']}  \n"
                f"{provider['coverage']}"
            )
        if not sec_user_agent_is_configured():
            st.caption("填写联系方式后即可启用美股官方实时数据。")
