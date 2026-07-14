"""TickerDNA 主应用 — 多页面研究工作台入口。

第十二阶段重构：统一全局视觉框架，保持所有业务逻辑不变。
"""
from __future__ import annotations

import logging
import os

import streamlit as st
from dotenv import load_dotenv

from modeling.engine import assumption_forecast_years

from ui_pages.state import (
    PAGES,
    APP_VERSION,
    APP_RELEASE_DATE,
    current_page,
    get_assumptions,
    navigate_to,
    render_sidebar,
    render_data_source_config,
    render_page_sidebar_extras,
    render_prev_step_button,
)
from ui_pages.theme import (
    render_global_css,
    render_status_bar,
)
from ui_pages.company_page import render_company_page
from ui_pages.history_page import render_history_page
from ui_pages.assumption_page import render_assumption_page
from ui_pages.forecast_page import render_forecast_page
from ui_pages.export_page import render_export_page

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("tickerdna.app")


load_dotenv()


# ── 页面配置 ─────────────────────────────────────────────────


st.set_page_config(
    page_title="TickerDNA",
    page_icon="📈",
    layout="wide",
)

if st.session_state.get("_app_version") != APP_VERSION:
    st.session_state.clear()
    st.session_state["_app_version"] = APP_VERSION

# 渲染全局 CSS（集中管理的视觉规范）
render_global_css()


# ── 侧边栏 ───────────────────────────────────────────────────


with st.sidebar:
    year_count = render_sidebar()
    page = current_page()
    render_page_sidebar_extras(page)
    render_data_source_config()

    # 左侧阶段导航
    st.divider()
    st.markdown("**阶段导航**")
    order = [p[0] for p in PAGES]
    try:
        current_idx = order.index(page)
    except ValueError:
        current_idx = 0

    has_assumptions = get_assumptions() is not None
    for idx, (key, label, _, _) in enumerate(PAGES):
        is_current = idx == current_idx
        is_completed = idx < current_idx and has_assumptions
        prefix = "→ " if is_current else ("✓ " if is_completed else "  ")
        st.button(
            f"{prefix}{label}",
            key=f"_nav_btn_{key}",
            use_container_width=True,
            on_click=navigate_to,
            args=(key,),
        )


# ── 顶部研究状态条 ───────────────────────────────────────────


render_status_bar()


# ── 页面渲染（真实多页面切换：只渲染当前选中的页面）──────────


def _render_current_page(year_count: int) -> None:
    """根据 current_page() 只渲染对应页面，不顺序渲染全部页面。"""
    page = current_page()

    if page == "company":
        render_company_page()
        return

    # 历史业务与财务资料页（Phase 12B-1：合并旧 source + split）
    if page == "source":
        render_history_page()
        render_prev_step_button("source")
        return

    # assumption / forecast / export 页面需要 years 参数
    assumptions = get_assumptions()
    if assumptions is not None:
        model_years = assumption_forecast_years(assumptions, year_count)
    else:
        model_years = []

    if page == "assumption":
        updated = render_assumption_page(model_years)
        if updated is not None:
            st.session_state["assumptions"] = updated
        render_prev_step_button("assumption")
    elif page == "forecast":
        render_forecast_page(model_years)
        render_prev_step_button("forecast")
    elif page == "export":
        render_export_page(model_years)
        render_prev_step_button("export")


_render_current_page(year_count)
