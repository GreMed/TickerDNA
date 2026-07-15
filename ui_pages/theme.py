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
:root {
    --td-ink: #172033;
    --td-muted: #667085;
    --td-soft: #98a2b3;
    --td-primary: #2563eb;
    --td-primary-dark: #1d4ed8;
    --td-primary-soft: #eff6ff;
    --td-surface: #ffffff;
    --td-canvas: #f6f8fc;
    --td-border: #e5eaf1;
    --td-border-strong: #d5dce7;
    --td-success: #15803d;
    --td-warning: #a16207;
    --td-radius: 14px;
    --td-shadow: 0 1px 2px rgba(16, 24, 40, .03), 0 8px 24px rgba(16, 24, 40, .04);
}

html, body, [data-testid="stAppViewContainer"], .stApp {
    background: var(--td-canvas);
    color: var(--td-ink);
}
body, button, input, textarea, select {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
                 "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
}

/* 隐藏面向开发者的装饰元素。
   toolbarMode = "minimal" 已隐藏开发者菜单（部署、分享等）。
   不隐藏 stHeader —— Streamlit 1.50 中它承载收起后的 stExpandSidebarButton。 */
[data-testid="stDecoration"], #MainMenu, footer {
    display: none !important;
}
header[data-testid="stHeader"] {
    height: 2.5rem;
    background: transparent;
}
/* Streamlit 1.50 原生默认 stSidebarCollapseButton visibility:hidden（仅 hover 显示）。
   强制始终可见，确保用户能直接看到向左双箭头。 */
[data-testid="stSidebarCollapseButton"],
[data-testid="stSidebarCollapseButton"] button,
[data-testid="stSidebarCollapseButton"] [data-testid="stIconMaterial"] {
    visibility: visible !important;
    opacity: 1 !important;
}
/* 收起后的展开按钮同样强制可见（向右双箭头）。 */
[data-testid="stExpandSidebarButton"],
[data-testid="stExpandSidebarButton"] button,
[data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"] {
    visibility: visible !important;
    opacity: 1 !important;
}

/* ── 主内容容器 ───────────────────────── */
.block-container,
div[data-testid="stMainBlockContainer"] {
    max-width: 1440px !important;
    padding: 2.7rem 3rem 5rem !important;
}
[data-testid="stMain"] {
    background:
        radial-gradient(circle at 82% 0%, rgba(37, 99, 235, .045), transparent 24rem),
        var(--td-canvas);
}

/* ── 侧边栏与品牌 ─────────────────────── */
[data-testid="stSidebar"] {
    background: #f9fafc;
    border-right: 1px solid var(--td-border);
}
/* 仅在展开时设定宽度，收起时让 Streamlit 原生归零，不强制 min-width。 */
[data-testid="stSidebar"][aria-expanded="true"] {
    min-width: 292px;
    max-width: 292px;
}
/* Streamlit 1.50 收起时仍保留内联 width / flex-basis；显式归零，
   否则侧栏虽然移出屏幕，主内容左侧仍会空出约 256px。 */
[data-testid="stSidebar"][aria-expanded="false"] {
    width: 0 !important;
    min-width: 0 !important;
    max-width: 0 !important;
    flex-basis: 0 !important;
    border-right: 0;
}
[data-testid="stSidebarContent"] {
    padding: 1rem .9rem 1.5rem;
}
.td-sidebar-brand {
    display: flex;
    align-items: center;
    gap: .72rem;
    margin: .15rem .15rem 1.25rem;
    padding: .45rem .35rem .85rem;
    border-bottom: 1px solid var(--td-border);
}
.td-brand-mark {
    display: grid;
    place-items: center;
    width: 36px;
    height: 36px;
    flex: 0 0 36px;
    border-radius: 11px;
    color: #fff;
    background: linear-gradient(145deg, #17365d, #2563eb);
    box-shadow: 0 7px 18px rgba(37, 99, 235, .2);
    font-size: .75rem;
    font-weight: 800;
    letter-spacing: -.03em;
}
.td-brand-name {
    color: #101828;
    font-size: 1rem;
    line-height: 1.15;
    font-weight: 760;
    letter-spacing: -.02em;
}
.td-brand-subtitle {
    color: var(--td-muted);
    font-size: .68rem;
    line-height: 1.35;
    margin-top: .16rem;
}
.td-sidebar-label {
    color: #98a2b3;
    font-size: .66rem;
    font-weight: 700;
    letter-spacing: .11em;
    text-transform: uppercase;
    margin: .2rem .35rem .55rem;
}
[data-testid="stSidebar"] hr {
    margin: .9rem .2rem;
    border-color: var(--td-border);
}
[data-testid="stSidebar"] [data-testid="stButton"] button {
    justify-content: flex-start;
    min-height: 2.55rem;
    padding: .55rem .75rem;
    border-radius: 10px;
    border-color: transparent;
    background: transparent;
    color: #475467;
    box-shadow: none;
    font-size: .85rem;
    font-weight: 540;
}
[data-testid="stSidebar"] [data-testid="stButton"] button:hover {
    color: #1d4ed8;
    background: #eef4ff;
    border-color: #dbe7ff;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"],
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"] {
    color: #1d4ed8 !important;
    background: #eaf2ff !important;
    border: 1px solid #cfe0ff !important;
    box-shadow: inset 3px 0 0 #2563eb !important;
    font-weight: 700 !important;
}
[data-testid="stSidebar"] details {
    background: transparent;
    border-color: var(--td-border);
}
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] li,
[data-testid="stSidebar"] label {
    color: #475467;
    font-size: .8rem;
}

/* ── 顶部研究状态条 ───────────────────── */
.td-status-bar {
    display: flex;
    align-items: stretch;
    gap: 0;
    padding: .76rem .95rem;
    margin-bottom: 1.45rem;
    background: rgba(255, 255, 255, .94);
    border: 1px solid var(--td-border);
    border-radius: var(--td-radius);
    box-shadow: 0 1px 2px rgba(16, 24, 40, .02);
    overflow-x: auto;
}
.td-status-bar.empty {
    align-items: center;
    justify-content: flex-start;
    color: #667085;
    font-size: .82rem;
    padding: .9rem 1rem;
    border-style: dashed;
    box-shadow: none;
}
.td-status-bar.empty::before {
    content: "";
    width: 8px;
    height: 8px;
    margin-right: .55rem;
    border-radius: 999px;
    background: #cbd5e1;
}
.td-status-item {
    display: flex;
    min-width: 0;
    flex-direction: column;
    justify-content: center;
    gap: .15rem;
    padding: 0 .95rem;
}
.td-status-item:first-child { padding-left: .2rem; }
.td-status-label {
    color: #98a2b3;
    font-size: .62rem;
    font-weight: 700;
    letter-spacing: .075em;
    text-transform: uppercase;
    white-space: nowrap;
}
.td-status-value {
    color: #27364b;
    font-size: .83rem;
    font-weight: 650;
    white-space: nowrap;
}
.td-status-divider {
    width: 1px;
    flex: 0 0 1px;
    min-height: 30px;
    background: var(--td-border);
}
.td-status-pill {
    display: inline-flex;
    align-items: center;
    width: fit-content;
    padding: .2rem .55rem;
    border-radius: 999px;
    font-size: .68rem;
    font-weight: 700;
    white-space: nowrap;
}
.td-status-pill.ok { background: #eaf8ef; color: #137333; }
.td-status-pill.progress { background: #eaf2ff; color: #1d4ed8; }
.td-status-pill.warn { background: #fff6df; color: #9a6700; }
.td-status-pill.empty { background: #f2f4f7; color: #667085; }

/* ── 页面标题与分节 ───────────────────── */
.td-page-header {
    margin: 0 0 1.45rem;
    padding: 0 .1rem;
}
.td-page-step {
    color: #2563eb;
    font-size: .68rem;
    font-weight: 800;
    letter-spacing: .115em;
    text-transform: uppercase;
    margin-bottom: .35rem;
}
.td-page-title {
    color: #101828;
    font-size: clamp(1.55rem, 2.25vw, 2rem);
    line-height: 1.2;
    font-weight: 760;
    letter-spacing: -.035em;
    margin-bottom: .42rem;
}
.td-page-desc {
    max-width: 48rem;
    color: var(--td-muted);
    font-size: .9rem;
    line-height: 1.65;
}
.td-section-header {
    margin: 1.65rem 0 .78rem;
}
.td-section-title {
    color: #1d2939;
    font-size: 1rem;
    line-height: 1.35;
    font-weight: 720;
    letter-spacing: -.012em;
}
.td-section-desc {
    color: #7b8798;
    font-size: .78rem;
    line-height: 1.55;
    margin-top: .2rem;
}
.td-meta-strip {
    display: flex;
    align-items: center;
    gap: .55rem;
    flex-wrap: wrap;
    margin: -.25rem 0 1rem;
    padding: .68rem .8rem;
    border: 1px solid var(--td-border);
    border-radius: 10px;
    background: rgba(255, 255, 255, .72);
    color: #667085;
    font-size: .76rem;
    line-height: 1.5;
}
.td-meta-strip strong { color: #344054; font-weight: 680; }
.td-meta-divider { color: #c7ced8; }
.td-note-line {
    margin: .55rem 0 1rem;
    padding: .65rem .8rem;
    border-left: 3px solid #8fb4ff;
    border-radius: 0 8px 8px 0;
    background: #f5f8ff;
    color: #536176;
    font-size: .78rem;
    line-height: 1.6;
}
.td-delivery-card {
    margin: .2rem 0 1rem;
    padding: 1rem 1.05rem;
    border: 1px solid #cfe0ff;
    border-radius: 14px;
    background: linear-gradient(145deg, #ffffff, #f1f6ff);
    box-shadow: 0 8px 26px rgba(37, 99, 235, .055);
}
.td-delivery-title {
    color: #17365d;
    font-size: .95rem;
    font-weight: 740;
    margin-bottom: .3rem;
}
.td-delivery-desc {
    color: #667085;
    font-size: .78rem;
    line-height: 1.6;
}

/* ── 首页推荐完整体验案例 ─────────────── */
.td-demo-section {
    margin: 1.2rem 0 1rem;
}
.td-demo-header {
    display: flex;
    align-items: center;
    gap: .5rem;
    margin-bottom: .7rem;
}
.td-demo-title {
    color: #17365d;
    font-size: 1rem;
    font-weight: 700;
}
.td-demo-tag {
    display: inline-block;
    padding: 1px 7px;
    border-radius: 999px;
    background: #eef4ff;
    color: #2563eb;
    font-size: .66rem;
    font-weight: 600;
}
.td-demo-desc {
    color: #667085;
    font-size: .8rem;
    line-height: 1.55;
    margin-bottom: .8rem;
}
[class*="st-key-demo_card_"] [data-testid="stButton"] button {
    position: relative;
    justify-content: flex-start;
    min-height: 5.4rem;
    padding: .95rem 3.2rem .95rem 1.1rem;
    border: 1px solid #cfe0ff;
    border-radius: 12px;
    background: linear-gradient(155deg, #ffffff, #f3f8ff);
    color: #101828;
    box-shadow: 0 1px 2px rgba(16, 24, 40, .02);
    transition: border-color .16s ease, box-shadow .16s ease,
                transform .16s ease, background .16s ease;
}
[class*="st-key-demo_card_"] [data-testid="stButton"] button:hover {
    border-color: #8eb7ff;
    background: linear-gradient(155deg, #ffffff, #edf5ff);
    box-shadow: 0 8px 22px rgba(37, 99, 235, .09);
    transform: translateY(-1px);
}
[class*="st-key-demo_card_"] [data-testid="stButton"] button:focus-visible {
    outline: 3px solid rgba(37, 99, 235, .28);
    outline-offset: 2px;
}
[class*="st-key-demo_card_"] [data-testid="stButton"] button p {
    width: 100%;
    margin: 0;
    color: #101828;
    font-size: .92rem;
    font-weight: 700;
    line-height: 1.65;
    text-align: left;
    white-space: pre-line;
}
[class*="st-key-demo_card_"] [data-testid="stButton"] button p::first-line {
    color: #101828;
    font-size: .92rem;
    font-weight: 700;
}
[class*="st-key-demo_card_"] [data-testid="stButton"] button::after {
    content: "→";
    position: absolute;
    right: 1.15rem;
    top: 50%;
    transform: translateY(-50%);
    color: #2563eb;
    font-size: 1.15rem;
    font-weight: 700;
}
[class*="st-key-demo_card_"] [data-testid="stButton"] button p {
    color: #667085;
}
.td-demo-note {
    margin-top: .8rem;
    color: #98a2b3;
    font-size: .72rem;
    line-height: 1.5;
}
.td-demo-compact {
    margin-top: .8rem;
}

/* ── 常用 Streamlit 组件 ──────────────── */
[data-testid="stVerticalBlockBorderWrapper"] {
    border-color: var(--td-border) !important;
    border-radius: var(--td-radius) !important;
    background: var(--td-surface);
}
div[data-testid="stForm"] {
    padding: .8rem .9rem;
    border: 1px solid var(--td-border);
    border-radius: var(--td-radius);
    background: var(--td-surface);
    box-shadow: var(--td-shadow);
}
[data-baseweb="input"] > div,
[data-baseweb="select"] > div,
[data-baseweb="textarea"] > div {
    border-color: var(--td-border-strong) !important;
    border-radius: 10px !important;
    background: #fff !important;
}
[data-baseweb="input"] > div:focus-within,
[data-baseweb="select"] > div:focus-within,
[data-baseweb="textarea"] > div:focus-within {
    border-color: #7aa7ff !important;
    box-shadow: 0 0 0 3px rgba(37, 99, 235, .10) !important;
}
button[kind="primary"], [data-testid="stBaseButton-primary"],
[data-testid="stDownloadButton"] button {
    border: 1px solid #2563eb !important;
    border-radius: 10px !important;
    background: linear-gradient(180deg, #2f6fed, #2563eb) !important;
    color: #fff !important;
    box-shadow: 0 5px 14px rgba(37, 99, 235, .16) !important;
    font-weight: 700 !important;
}
button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover,
[data-testid="stDownloadButton"] button:hover {
    background: #1d4ed8 !important;
    border-color: #1d4ed8 !important;
    box-shadow: 0 7px 18px rgba(37, 99, 235, .22) !important;
}
button[kind="secondary"], [data-testid="stBaseButton-secondary"] {
    border-color: var(--td-border-strong);
    border-radius: 10px;
    background: #fff;
    color: #344054;
    box-shadow: 0 1px 2px rgba(16, 24, 40, .02);
}
button:focus-visible {
    outline: 3px solid rgba(37, 99, 235, .22) !important;
    outline-offset: 2px;
}

details[data-testid="stExpander"] {
    overflow: hidden;
    border: 1px solid var(--td-border);
    border-radius: 12px;
    background: rgba(255, 255, 255, .84);
    box-shadow: none;
}
details[data-testid="stExpander"] summary {
    min-height: 2.8rem;
    color: #344054;
    font-weight: 620;
}
details[data-testid="stExpander"][open] {
    background: #fff;
    box-shadow: 0 7px 20px rgba(16, 24, 40, .035);
}

div[data-testid="stAlert"] {
    border-radius: 12px;
    border-width: 1px;
    box-shadow: none;
}
div[data-testid="stAlert"] p,
div[data-testid="stAlert"] li {
    font-size: .82rem;
    line-height: 1.6;
}

/* ── 指标卡 ───────────────────────────── */
div[data-testid="stMetric"] {
    min-height: 108px;
    padding: 1rem 1.05rem;
    border: 1px solid var(--td-border);
    border-radius: var(--td-radius);
    background: linear-gradient(155deg, #ffffff, #fbfcff);
    box-shadow: 0 1px 2px rgba(16, 24, 40, .025);
}
div[data-testid="stMetric"] label {
    color: #667085 !important;
    font-size: .76rem !important;
    font-weight: 620 !important;
}
div[data-testid="stMetricValue"] {
    color: #172033;
    font-size: clamp(1.45rem, 2.25vw, 2rem);
    font-weight: 720;
    letter-spacing: -.035em;
}

/* ── Tabs、单选与滑杆 ─────────────────── */
button[data-baseweb="tab"] {
    min-height: 2.8rem;
    padding-left: .95rem;
    padding-right: .95rem;
    color: #667085;
    font-weight: 620;
}
button[data-baseweb="tab"][aria-selected="true"] {
    color: #1d4ed8;
}
div[role="radiogroup"][aria-orientation="horizontal"] {
    display: flex;
    gap: .42rem;
    flex-wrap: wrap;
}
div[role="radiogroup"][aria-orientation="horizontal"] > label {
    margin: 0;
    padding: .42rem .72rem;
    border: 1px solid var(--td-border);
    border-radius: 999px;
    background: #fff;
    color: #475467;
}
div[role="radiogroup"][aria-orientation="horizontal"] > label:has(input:checked) {
    border-color: #b8d0ff;
    background: var(--td-primary-soft);
    color: #1d4ed8;
    font-weight: 700;
}
[data-baseweb="slider"] [role="slider"] {
    box-shadow: 0 0 0 3px #fff, 0 0 0 5px rgba(37, 99, 235, .15);
}

/* ── 表格 ─────────────────────────────── */
div[data-testid="stDataFrame"] {
    min-width: 0;
    border: 1px solid var(--td-border);
    border-radius: 12px;
    background: #fff;
    box-shadow: 0 1px 2px rgba(16, 24, 40, .02);
}
div[data-testid="stDataFrame"] table {
    table-layout: auto;
}
[data-testid="stDataEditor"] {
    border: 1px solid var(--td-border);
    border-radius: 12px;
    background: #fff;
}

/* ── 文本节奏与空状态 ─────────────────── */
p, li { line-height: 1.62; }
h2, h3 { color: #1d2939; letter-spacing: -.02em; }
h3 { margin-top: 1.45rem !important; font-size: 1.05rem !important; }
.td-empty-state {
    text-align: center;
    padding: 3.5rem 1rem;
    color: #98a2b3;
}

/* ── 响应式 ───────────────────────────── */
@media (max-width: 1100px) {
    .block-container,
    div[data-testid="stMainBlockContainer"] {
        padding-left: 1.7rem !important;
        padding-right: 1.7rem !important;
    }
    [data-testid="stSidebar"][aria-expanded="true"] {
        min-width: 276px;
        max-width: 276px;
    }
}
@media (max-width: 760px) {
    header[data-testid="stHeader"] { height: 3rem; }
    .block-container,
    div[data-testid="stMainBlockContainer"] {
        padding: 3.35rem 1rem 3.5rem 1.55rem !important;
    }
    .td-status-bar {
        display: grid;
        grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr);
        margin-bottom: 1.1rem;
        padding: .7rem .75rem;
        overflow: visible;
    }
    .td-status-item {
        min-width: 0;
        padding: .35rem .45rem;
    }
    .td-status-item:first-child { padding-left: .45rem; }
    .td-status-divider { display: none; }
    .td-status-value {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }
    .td-status-bar.empty {
        display: flex;
        white-space: normal;
        line-height: 1.5;
    }
    .td-page-header { margin-bottom: 1.1rem; }
    .td-page-title { font-size: 1.55rem; }
    .td-page-desc { font-size: .84rem; }
    div[data-testid="stMetric"] {
        min-height: 92px;
        padding: .8rem;
    }
    div[data-testid="stMetricValue"] { font-size: 1.35rem; }
    div[role="radiogroup"][aria-orientation="horizontal"] > label {
        padding: .36rem .58rem;
        font-size: .76rem;
    }
}
</style>
"""


def render_global_css() -> None:
    """在页面顶部渲染全局 CSS。应在 app.py 中调用一次。"""
    st.markdown(GLOBAL_CSS, unsafe_allow_html=True)


def render_sidebar_brand(version: str = "") -> None:
    """渲染侧边栏品牌区，不创建任何交互状态。"""
    safe_version = html.escape(str(version))
    version_text = f" · {safe_version}" if safe_version else ""
    st.markdown(
        '<div class="td-sidebar-brand">'
        '<div class="td-brand-mark">TD</div>'
        '<div>'
        '<div class="td-brand-name">TickerDNA</div>'
        f'<div class="td-brand-subtitle">可解释财务预测工作台{version_text}</div>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )


def render_section_header(title: str, description: str = "") -> None:
    """渲染统一的页面分节标题。"""
    safe_title = html.escape(str(title))
    safe_description = html.escape(str(description))
    description_html = (
        f'<div class="td-section-desc">{safe_description}</div>'
        if safe_description
        else ""
    )
    st.markdown(
        '<div class="td-section-header">'
        f'<div class="td-section-title">{safe_title}</div>'
        f'{description_html}'
        '</div>',
        unsafe_allow_html=True,
    )


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
            '尚未开始研究 — 请在下方搜索并确认上市公司'
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
        '<div class="td-page-header">'
        f'<div class="td-page-step">{safe_step} / 6</div>'
        f'<div class="td-page-title">{safe_title}</div>'
        f'<div class="td-page-desc">{safe_desc}</div>'
        '</div>',
        unsafe_allow_html=True,
    )
