"""TickerDNA 全局视觉规范 — 集中管理所有 CSS 样式、颜色和信息身份标签。

本模块是第十二阶段第一小步建立的统一视觉规则中心，避免每个页面各写一套样式。

四类信息身份（颜色 + 文字标签双重表达）：
- 公司披露 / 历史事实：深色中性样式（slate）
- AI 初始判断：琥珀色（amber）
- 用户确认 / 修改后的假设：蓝色（blue）
- 缺失 / 不可比 / 待核验：灰色或琥珀色风险提示
"""
from __future__ import annotations

import html

import streamlit as st

# ── 颜色定义 ─────────────────────────────────────────────────

# 信息身份颜色（不能是唯一表达方式，每项都有文字标签）
COLOR_DISCLOSURE = "#1e293b"       # 公司披露 / 历史事实 — 深石板色
COLOR_AI_ESTIMATE = "#b45309"      # AI 初始判断 — 琥珀色
COLOR_USER_CONFIRMED = "#1d4ed8"   # 用户确认 / 修改 — 蓝色
COLOR_RISK_WARNING = "#92400e"     # 缺失 / 不可比 / 待核验 — 深琥珀
COLOR_RISK_MUTED = "#64748b"       # 缺失（静默）— 灰色
COLOR_MIXED = "#6d28d9"           # 混合性质 — 紫色（Phase 12B-2 收口）

# 品牌色
COLOR_PRIMARY = "#17365d"          # 主品牌深蓝
COLOR_ACCENT = "#0868d7"          # 强调蓝
COLOR_BG_LIGHT = "#f7f9fc"         # 浅背景
COLOR_BORDER = "#e6eaf0"           # 边框

# ── 信息身份标签 ───────────────────────────────────────────────


def info_disclosure(label: str = "公司披露") -> str:
    """公司披露 / 历史事实标签 HTML。"""
    return _tag(label, COLOR_DISCLOSURE, bg="#f1f5f9")


def info_ai_estimate(label: str = "AI 估算") -> str:
    """AI 初始判断标签 HTML。"""
    return _tag(label, COLOR_AI_ESTIMATE, bg="#fef3c7")


def info_user_confirmed(label: str = "用户定义") -> str:
    """用户确认 / 修改后的假设标签 HTML。"""
    return _tag(label, COLOR_USER_CONFIRMED, bg="#dbeafe")


def info_risk(label: str = "待核验") -> str:
    """缺失 / 不可比 / 待核验风险标签 HTML。"""
    return _tag(label, COLOR_RISK_WARNING, bg="#fef3c7")


def info_missing(label: str = "缺失") -> str:
    """数据缺失标签 HTML（静默灰色）。"""
    return _tag(label, COLOR_RISK_MUTED, bg="#f1f5f9")


def info_mixed(label: str = "混合") -> str:
    """混合性质标签 HTML — Phase 12B-2 收口：同一指标包含多种假设性质。"""
    return _tag(label, COLOR_MIXED, bg="#ede9fe")


def _tag(text: str, color: str, bg: str) -> str:
    """生成一个带颜色的小标签 HTML。"""
    safe_text = html.escape(str(text))
    return (
        f'<span style="display:inline-block;padding:2px 8px;'
        f'border-radius:4px;font-size:.72rem;font-weight:600;'
        f'color:{color};background:{bg};border:1px solid {color}33;'
        f'">{safe_text}</span>'
    )


# ── 全局 CSS ─────────────────────────────────────────────────


GLOBAL_CSS = """
<style>
/* ── 容器 ─────────────────────────────── */
/* 保证 Streamlit 顶部栏（stHeader，高 60px）不遮挡研究状态条和 Step 标题 */
.block-container,
div[data-testid="stMainBlockContainer"] {
    max-width: 1280px !important;
    padding-top: 4.5rem !important;
}

/* ── 顶部研究状态条 ───────────────────── */
.td-status-bar {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 8px 14px;
    margin-bottom: .8rem;
    background: #fff;
    border: 1px solid #e6eaf0;
    border-radius: 8px;
    flex-wrap: wrap;
}
.td-status-bar.empty {
    justify-content: center;
    color: #94a3b8;
    font-size: .88rem;
    padding: 10px 14px;
}
.td-status-item {
    display: flex;
    flex-direction: column;
    gap: 1px;
}
.td-status-label {
    font-size: .65rem;
    color: #94a3b8;
    font-weight: 500;
    letter-spacing: .04em;
    text-transform: uppercase;
}
.td-status-value {
    font-size: .88rem;
    color: #1e293b;
    font-weight: 600;
}
.td-status-divider {
    width: 1px;
    height: 24px;
    background: #e6eaf0;
}
.td-status-pill {
    display: inline-block;
    padding: 2px 9px;
    border-radius: 11px;
    font-size: .73rem;
    font-weight: 600;
}
.td-status-pill.ok { background: #dcfce7; color: #166534; }
.td-status-pill.progress { background: #dbeafe; color: #1e40af; }
.td-status-pill.warn { background: #fef3c7; color: #92400e; }
.td-status-pill.empty { background: #f1f5f9; color: #64748b; }

/* ── 左侧导航 ─────────────────────────── */
.td-nav-section { margin-bottom: .3rem; }
.td-nav-status {
    font-size: .65rem;
    color: #94a3b8;
    margin-left: 6px;
}

/* ── 页面标题区 ───────────────────────── */
.td-page-step {
    color: #17365d;
    font-size: .74rem;
    font-weight: 700;
    letter-spacing: .08em;
    text-transform: uppercase;
    margin-bottom: 2px;
}
.td-page-title {
    font-size: 1.28rem;
    font-weight: 700;
    color: #0f172a;
    margin-bottom: 3px;
}
.td-page-desc {
    font-size: .85rem;
    color: #64748b;
    margin-bottom: .8rem;
}

/* ── stMetric 卡片 ────────────────────── */
div[data-testid="stMetric"] {
    background: #f7f9fc;
    border: 1px solid #e6eaf0;
    padding: 12px;
    border-radius: 10px;
}

/* ── 单选按钮（横向）──────────────────── */
div[role="radiogroup"][aria-orientation="horizontal"] > label {
    border: 1px solid #dfe5ee;
    border-radius: 8px;
    padding: .35rem .7rem;
    margin-right: .4rem;
    background: #f7f9fc;
}
div[role="radiogroup"][aria-orientation="horizontal"] > label:has(input:checked) {
    border-color: #0868d7;
    background: #dbeafe;
    color: #1e40af;
    font-weight: 700;
}

/* ── 表格优化 ─────────────────────────── */
div[data-testid="stDataFrame"] {
    min-width: 0;
}
div[data-testid="stDataFrame"] table {
    table-layout: auto;
}

/* ── 空状态 ────────────────────────────── */
.td-empty-state {
    text-align: center;
    padding: 3rem 1rem;
    color: #94a3b8;
}
</style>
"""


def render_global_css() -> None:
    """在页面顶部渲染全局 CSS。应在 app.py 中调用一次。"""
    st.markdown(GLOBAL_CSS, unsafe_allow_html=True)


# ── 状态条渲染 ─────────────────────────────────────────────────


def render_status_bar() -> None:
    """渲染顶部研究状态条。

    显示：公司名称与证券代码、基期年度、币种与单位、当前资料状态、当前研究阶段。
    无公司时显示清晰空状态。
    """
    company = st.session_state.get("selected_company")
    assumptions = st.session_state.get("assumptions")

    if not company and not assumptions:
        st.markdown(
            '<div class="td-status-bar empty">'
            '尚未确认公司 — 请在左侧「公司与项目」中搜索并确认上市公司'
            '</div>',
            unsafe_allow_html=True,
        )
        return

    # 公司名称与代码
    name = "—"
    symbol = "—"
    if company:
        name = company.get("name", "—")
        symbol = company.get("symbol", "—")

    # 基期年度、币种单位
    fiscal_year = "—"
    currency = "—"
    source_status = "待读取资料"
    status_cls = "empty"
    if assumptions:
        fiscal_year = assumptions.get("fiscal_year", "—")
        currency = assumptions.get("currency", "—")
        source_cat = assumptions.get("source_category", "")
        if source_cat:
            source_status = source_cat
            if "披露" in source_cat or "快照" in source_cat:
                status_cls = "ok"
            elif "估算" in source_cat or "无匹配" in source_cat:
                status_cls = "warn"
            else:
                status_cls = "progress"

    # 当前研究阶段（页面标题和左侧导航已表达，不在状态条中重复）
    name = html.escape(str(name))
    symbol = html.escape(str(symbol))
    fiscal_year = html.escape(str(fiscal_year))
    currency = html.escape(str(currency))
    source_status = html.escape(str(source_status))

    status_html = f"""
    <div class="td-status-bar">
        <div class="td-status-item">
            <span class="td-status-label">公司</span>
            <span class="td-status-value">{name}（{symbol}）</span>
        </div>
        <div class="td-status-divider"></div>
        <div class="td-status-item">
            <span class="td-status-label">基期年度</span>
            <span class="td-status-value">{fiscal_year}</span>
        </div>
        <div class="td-status-divider"></div>
        <div class="td-status-item">
            <span class="td-status-label">币种 / 单位</span>
            <span class="td-status-value">{currency}</span>
        </div>
        <div class="td-status-divider"></div>
        <div class="td-status-item">
            <span class="td-status-label">资料状态</span>
            <span class="td-status-pill {status_cls}">{source_status}</span>
        </div>
    </div>
    """
    st.markdown(status_html, unsafe_allow_html=True)


# ── 页面标题区渲染 ─────────────────────────────────────────────


def render_page_header(step: str, title: str, description: str) -> None:
    """渲染统一页面标题区。

    Args:
        step: 阶段编号，如 "Step 1"
        title: 页面名称，如 "公司与项目"
        description: 一句任务说明
    """
    safe_step = html.escape(str(step))
    safe_title = html.escape(str(title))
    safe_desc = html.escape(str(description))
    st.markdown(
        f'<div class="td-page-step">{safe_step}</div>'
        f'<div class="td-page-title">{safe_title}</div>'
        f'<div class="td-page-desc">{safe_desc}</div>',
        unsafe_allow_html=True,
    )
