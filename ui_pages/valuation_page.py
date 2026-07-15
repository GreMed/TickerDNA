"""估值与市场对照（示范）页 — Step 5。

Phase 15-1：在 Apple 和腾讯两个完整案例中增加可体验的估值对照页，
让用户理解未来如何将 TickerDNA Base 预测与市场一致预期、估值水平进行比较。

本页是演示功能：
- 不接入实时行情或真实券商数据库
- 使用本地 AI 生成的功能演示静态数据
- 不构成投资建议
- 只允许"相对偏低、接近、相对偏高"等条件式表述
- 不使用"买入、卖出、推荐、目标价"等投资建议表达
"""
from __future__ import annotations

import logging

import streamlit as st

from modeling.valuation_engine import (
    NOT_APPLICABLE,
    build_valuation_comparison,
    get_demo_data_for_symbol,
    validate_target_pe,
)

from ui_pages.state import (
    get_assumptions,
    render_next_step_button,
    require_forecast_results,
    navigate_to,
    prev_page,
)

logger = logging.getLogger("tickerdna.app")

# 免责声明文案
DISCLAIMER = (
    "以下市场与一致预期数据为本地 AI 生成的功能演示静态数据，"
    "不是实时行情，不是真实券商一致预期，不构成投资建议。"
)


def render_valuation_page(years: list[int]) -> None:
    """渲染估值与市场对照（示范）页。"""
    if not require_forecast_results():
        return

    assumptions = get_assumptions()
    if not assumptions:
        return

    symbol = assumptions.get("symbol") or assumptions.get("ticker", "")
    demo_data = get_demo_data_for_symbol(symbol)

    from ui_pages.theme import render_page_header, render_section_header
    render_page_header(
        "Step 5", "估值与市场对照",
        "将 TickerDNA Base 预测与模拟一致预期、估值水平进行比较（功能示范）。",
    )

    # ── 1. 醒目的数据性质说明 ──
    st.warning(DISCLAIMER)

    # 非支持公司：先判断是否支持，再显示数据日期和来源
    if demo_data is None:
        st.caption(
            "当前公司尚未配置估值演示数据。"
            "仅 Apple（AAPL）和腾讯控股（0700.HK）提供静态估值演示。"
        )
        render_empty_state(assumptions)
        render_nav_buttons()
        return

    # 支持公司：显示数据日期和来源
    st.caption(
        f"演示数据截至 {demo_data['as_of_date']} ｜ "
        f"数据性质：功能演示静态数据 ｜ "
        f"来源：TickerDNA 功能演示静态数据"
    )

    # ── 目标 PE 输入 ──
    # key 必须包含 symbol，防止切换公司后串数据
    # 注意：使用 key= 时不要同时传 value=（Streamlit 会从 session_state 自动读取），
    # 否则会触发 "widget created with default value but also had value set via Session State" 警告。
    default_pe = demo_data["default_target_pe"]
    target_pe_key = f"_target_pe_{symbol}"

    st.markdown(
        f'<div class="td-section-header">'
        f'<div class="td-section-title">目标 PE 设置</div>'
        f'<div class="td-section-desc">默认值来自静态案例（{default_pe:.0f}），可编辑。修改后隐含市值立即更新。</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    target_pe = st.number_input(
        "目标 PE（倍）",
        min_value=0.0,
        max_value=200.0,
        value=float(default_pe),
        step=1.0,
        key=target_pe_key,
        help="输入您认为合理的目标市盈率。非正数将显示错误，不静默改值。",
    )

    # 验证目标 PE
    pe_error = validate_target_pe(target_pe)
    if pe_error:
        st.error(f"目标 PE 输入有误：{pe_error}")
        st.caption("请修正后查看估值对照结果。")
        render_nav_buttons()
        return

    # ── 构建估值对照数据 ──
    try:
        result = build_valuation_comparison(assumptions, years, target_pe)
    except Exception as exc:
        logger.exception("估值对照计算异常")
        st.error("估值对照计算出现问题，请检查输入数据或稍后重试。")
        render_nav_buttons()
        return

    # 目标 PE 内部验证失败
    if not result.get("target_pe_ok", True):
        st.error(f"目标 PE 输入有误：{result.get('target_pe_error', '')}")
        st.caption("请修正后查看估值对照结果。")
        render_nav_buttons()
        return

    # 币种不一致
    if not result["currency_ok"]:
        st.error(result["currency_error"])
        render_nav_buttons()
        return

    summary = result["summary"]
    comparison_table = result["comparison_table"]
    valuation_table = result["valuation_table"]
    observation = result["observation"]

    # ── 2. 摘要卡（最多4个）──
    render_section_header(
        "摘要",
        "首年模型与模拟一致预期的关键对照指标。",
    )
    col1, col2, col3, col4 = st.columns(4)

    first_year = summary.get("first_year")
    first_year_label = f"FY{first_year}E" if first_year else "—"
    currency_label = summary.get("currency", "")

    col1.metric(
        "参考市值",
        f"{summary['reference_market_cap']:,.0f}",
        help=f"数据性质：功能演示静态数据 ｜ 币种：{currency_label}",
    )
    col1.caption(f"{currency_label}")
    col2.metric(
        f"{first_year_label} 模型 Forward PE",
        _fmt_pe_display(summary.get("first_model_forward_pe")),
    )
    col3.metric(
        f"{first_year_label} 模拟一致预期 Forward PE",
        _fmt_pe_display(summary.get("first_consensus_forward_pe")),
    )
    col4.metric(
        f"{first_year_label} 净利润差异",
        _fmt_diff_display(summary.get("first_profit_diff")),
    )

    # ── 3. 财务预测对照表 ──
    render_section_header(
        "财务预测对照",
        "展示全部模型预测年度；仅在存在模拟一致预期数据的重叠年度计算差异。",
    )
    st.dataframe(
        comparison_table,
        hide_index=True,
        use_container_width=True,
    )
    st.caption("收入差异 = 模型 Base 收入 / 模拟一致预期收入 - 1 ｜ 净利润差异 = 模型 Base 净利润 / 模拟一致预期净利润 - 1")

    # ── 4. 估值对照表 ──
    render_section_header(
        "估值对照",
        "Forward PE、隐含市值与参考市值的比较。",
    )
    st.dataframe(
        valuation_table,
        hide_index=True,
        use_container_width=True,
    )
    st.caption(
        "模型 Forward PE = 参考市值 / 模型 Base 净利润 ｜ "
        "模拟一致预期 Forward PE = 参考市值 / 模拟一致预期净利润 ｜ "
        "模型隐含市值 = 模型 Base 净利润 × 目标 PE ｜ "
        "隐含市值相对参考市值差异 = 模型隐含市值 / 参考市值 - 1"
    )

    # ── 5. 目标 PE 修改提示（已在上方渲染）──
    # 此处仅补充说明
    if target_pe != default_pe:
        st.info(f"目标 PE 已修改为 {target_pe:.1f}（默认值 {default_pe:.0f}），隐含市值已同步更新。")

    # ── 6. 相对估值观察卡 ──
    render_section_header(
        "相对估值观察",
        "仅基于当前假设和目标 PE 的条件式表述，不构成投资建议。",
    )
    st.info(observation)
    st.caption(
        "本观察仅使用「相对偏低、接近、相对偏高」等条件式表述，"
        "不包含「买入、卖出、推荐、目标价、上涨空间」等投资建议。"
    )

    # ── 7. 折叠的"计算口径与限制"说明 ──
    with st.expander("计算口径与限制", expanded=False):
        st.markdown(
            "**数据来源**\n"
            "- 模型列：从当前 Base 假设和预测年数实时计算（`build_forecast()`），修改假设后同步变化。\n"
            "- 模拟一致预期列：本地 AI 生成的功能演示静态数据，不随用户假设变化。\n"
            "- 参考市值：功能演示静态数据，不等于实时市值。\n\n"
            "**计算公式**\n"
            "- 收入差异 = 模型 Base 收入 / 模拟一致预期收入 - 1\n"
            "- 净利润差异 = 模型 Base 净利润 / 模拟一致预期净利润 - 1\n"
            "- 模型隐含市值 = 模型 Base 净利润 × 用户目标 PE\n"
            "- 模型 Forward PE = 参考市值 / 模型 Base 净利润\n"
            "- 模拟一致预期 Forward PE = 参考市值 / 模拟一致预期净利润\n"
            "- 隐含市值相对参考市值差异 = 模型隐含市值 / 参考市值 - 1\n\n"
            "**异常规则**\n"
            "- 币种或金额单位不一致时阻止比较并明确提示；\n"
            "- 净利润 ≤ 0 时 PE 和隐含估值显示「不适用」；\n"
            "- 缺失年份显示「—」，不自行外推；\n"
            "- 分母为 0 时不报错，显示「不适用」。\n\n"
            "**产品限制**\n"
            "- 本页仅为功能演示，不接入实时行情或真实券商数据库；\n"
            "- 模拟一致预期数据不是真实券商一致预期；\n"
            "- 不构成任何投资建议；\n"
            "- 暂不提供目标价、评级、资金流、公告或价格预测。"
        )

    # ── 8. 导航按钮 ──
    render_nav_buttons()


def render_empty_state(assumptions: dict) -> None:
    """非支持公司显示诚实空状态。"""
    company_name = assumptions.get("company_name") or assumptions.get("name") or "当前公司"
    symbol = assumptions.get("symbol") or assumptions.get("ticker", "")
    st.info(
        f"**{company_name}（{symbol}）尚未配置估值演示数据。**\n\n"
        "目前仅 Apple（AAPL）和腾讯控股（0700.HK）两个内置案例提供估值演示。\n\n"
        "- 不自动编造券商预期；\n"
        "- 不接入实时行情或真实券商数据库；\n"
        "- 可返回预测页查看模型结果，或继续导出 Excel。"
    )


def render_nav_buttons() -> None:
    """渲染导航按钮：上一步（预测与情景）+ 下一步（导出与交付）。"""
    from ui_pages.state import render_prev_step_button
    render_prev_step_button("valuation")
    render_next_step_button("valuation")


def _fmt_pe_display(value) -> str:
    """格式化 PE 显示值。"""
    if value is None:
        return "—"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return f"{value:.1f}"
    return str(value)


def _fmt_diff_display(value) -> str:
    """格式化差异显示值。"""
    if value is None:
        return "—"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return f"{value:+.1%}"
    return str(value)
