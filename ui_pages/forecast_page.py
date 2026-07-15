"""预测与情景页 — Step 4 财务报表式表格、图表、情景对比。

Phase 12B-3 重构：
- 情景对比表改为「指标为行、年度为列」的财务报表结构
- 单情景明细表改为财务报表式（分部收入→总收入→毛利→利润）
- 历史期在最左侧，预测年度依次排列
- 数据直接来自 build_forecast() 与基期数据函数，不在 UI 中重新计算
"""
from __future__ import annotations

import logging

import altair as alt
import streamlit as st

from modeling.engine import (
    baseline_metrics,
    build_forecast,
    scenario_chart_table,
    summary_table,
)
from modeling.statement import (
    build_base_period_footnote,
    build_scenario_comparison_statement,
    build_scenario_detail_statement,
    format_forecast_statement,
)

from ui_pages.state import (
    get_assumptions,
    render_next_step_button,
    require_assumptions_for_forecast,
)

logger = logging.getLogger("tickerdna.app")


def render_forecast_page(years: list[int]) -> None:
    """渲染预测结果页。"""
    if not require_assumptions_for_forecast():
        return

    assumptions = get_assumptions()
    if not assumptions:
        return

    from ui_pages.theme import render_page_header
    render_page_header(
        "Step 4", "预测与情景",
        "查看 Bull/Base/Bear 三情景预测结果和图表对比。",
    )

    try:
        forecasts = build_forecast(assumptions, years)
        summary = summary_table(forecasts)

        has_nan = False
        has_inf = False
        for scenario, df in forecasts.items():
            if df.isna().any().any():
                has_nan = True
                break
            if (df == float('inf')).any().any() or (df == float('-inf')).any().any():
                has_inf = True
                break
        if has_nan:
            st.warning("⚠ 预测中存在缺失值，请检查输入数据。")
        if has_inf:
            st.warning("⚠ 预测中存在无穷大值，请检查增长率设置。")
    except ValueError as exc:
        logger.warning("预测计算 ValueError: %s", exc)
        st.warning("预测计算出现问题，请检查输入数据或假设设置。")
        return
    except Exception as exc:
        logger.exception("预测计算异常")
        st.error("预测计算出现意外错误。请检查输入数据或稍后重试。")
        return

    # 将 forecasts/summary 存入 session_state，供导出页使用
    st.session_state["_forecast_results"] = forecasts
    st.session_state["_forecast_summary"] = summary

    base = forecasts["Base"]
    first, last = base.iloc[0], base.iloc[-1]
    base_actual = baseline_metrics(assumptions)
    cagr = (
        (last["收入"] / base_actual["收入"]) ** (1 / max(len(base), 1)) - 1
        if base_actual["收入"] > 0
        else 0
    )

    # Base 情景关键指标：卡片用于扫读，原摘要行继续保留为完整语义证据。
    from ui_pages.theme import (
        info_ai_estimate,
        render_section_header,
    )
    render_section_header(
        "Base 情景概览",
        "首个预测年度与完整预测期的四项核心结果。",
    )
    summary_cols = st.columns(4)
    summary_cols[0].metric(
        f"FY{int(first['年度'])}E 收入",
        f"{first['收入']:,.0f}",
    )
    summary_cols[1].metric(
        f"FY{int(first['年度'])}E 毛利率",
        f"{first['毛利率']:.1%}",
    )
    summary_cols[2].metric(
        f"FY{int(first['年度'])}E 净利润",
        f"{first['净利润']:,.0f}",
    )
    summary_cols[3].metric("预测期收入 CAGR", f"{cagr:.1%}")
    st.markdown(
        '<div class="td-note-line">'
        f"<strong>Base 摘要：</strong>{int(first['年度'])}年收入 {first['收入']:,.0f}"
        f" ｜ 毛利率 {first['毛利率']:.1%}"
        f" ｜ 净利润 {first['净利润']:,.0f}"
        f" ｜ CAGR {cagr:.1%}"
        f" ｜ 预测数据 {info_ai_estimate('不等同于公司披露')}"
        '</div>',
        unsafe_allow_html=True,
    )

    with st.expander("预测说明", expanded=False):
        st.markdown(
            "- **基期数据**：来自公司披露或模型估算，标注为「基期」或「实际」\n"
            "- **预测数据**：基于您设定的假设计算，**不等同于公司披露**\n"
            "- **情景规则**：Bull = Base + 振幅，Bear = Base - 振幅\n"
            "- **税率规则**：仅当税前利润 > 0 时计算所得税，亏损年份不确认税收收益"
        )

    # ── 两个 Tab：情景对比 + 单情景明细 ──────────────────
    tab_comparison, tab_detail = st.tabs(["情景对比", "单情景明细"])

    # ════════════════════════════════════════════════════════
    # Tab 1：情景对比（图表 + 财务报表式对比表）
    # ════════════════════════════════════════════════════════
    with tab_comparison:
        render_section_header(
            "情景走势",
            "Base 为主结论；Bull 与 Bear 用于观察上下行区间。",
        )
        # 图表指标选择器放在图表上方，独占整行
        chart_metric = st.radio(
            "图表指标",
            ["收入", "毛利", "毛利率", "净利润", "净利率"],
            key="forecast_chart_metric",
            horizontal=True,
        )

        # 图表数据
        chart_data = scenario_chart_table(
            assumptions,
            forecasts,
            chart_metric,
        )
        period_order = (
            chart_data[["期间", "期间顺序"]]
            .drop_duplicates()
            .sort_values("期间顺序")["期间"]
            .tolist()
        )
        is_ratio = chart_metric in {"毛利率", "净利率"}

        # 自动计算纵轴范围，覆盖所有数据并保留合理留白
        values = chart_data["数值"].dropna()
        if len(values) > 0:
            v_min = float(values.min())
            v_max = float(values.max())
            if v_min == v_max:
                if is_ratio:
                    domain = [max(0.0, v_min - 0.05), min(1.0, v_max + 0.05)]
                else:
                    pad = abs(v_min) * 0.1 + 1.0
                    domain = [v_min - pad, v_max + pad]
            else:
                pad = (v_max - v_min) * 0.1
                if is_ratio:
                    domain = [max(0.0, v_min - pad), min(1.0, v_max + pad)]
                    if domain[0] > v_min:
                        domain[0] = v_min
                    if domain[1] < v_max:
                        domain[1] = v_max
                else:
                    domain = [v_min - pad, v_max + pad]
        else:
            domain = [0, 1]

        # 图表标题（含单位）
        currency = assumptions.get("currency", "人民币百万元")
        if is_ratio:
            chart_title = f"{chart_metric}预测"
            y_format = ".1%"
        else:
            chart_title = f"{chart_metric}预测（{currency}）"
            y_format = ",.1f"

        y_axis = alt.Axis(
            title=chart_metric,
            format=y_format,
            grid=True,
        )

        # Base 主结论：深蓝色、最粗、实线
        # Bull：绿色虚线；Bear：红色虚线
        chart = (
            alt.Chart(chart_data)
            .mark_line(point=alt.OverlayMarkDef(size=55))
            .encode(
                x=alt.X(
                    "期间:N",
                    sort=period_order,
                    axis=alt.Axis(title="年度", labelAngle=0),
                ),
                y=alt.Y(
                    "数值:Q",
                    axis=y_axis,
                    scale=alt.Scale(domain=domain, zero=False),
                ),
                color=alt.Color(
                    "情景:N",
                    scale=alt.Scale(
                        domain=["Bull", "Base", "Bear"],
                        range=["#10b981", "#1e40af", "#ef4444"],
                    ),
                    legend=alt.Legend(
                        title="情景",
                        orient="bottom",
                        direction="horizontal",
                    ),
                ),
                strokeDash=alt.StrokeDash(
                    "情景:N",
                    scale=alt.Scale(
                        domain=["Bull", "Base", "Bear"],
                        range=[[5, 4], [1, 0], [5, 4]],
                    ),
                    legend=None,
                ),
                strokeWidth=alt.StrokeWidth(
                    "情景:N",
                    scale=alt.Scale(
                        domain=["Bull", "Base", "Bear"],
                        range=[2, 4, 2],
                    ),
                    legend=None,
                ),
                shape=alt.Shape(
                    "期间类型:N",
                    scale=alt.Scale(
                        domain=["实际", "基期估算", "预测"],
                        range=["circle", "triangle-up", "diamond"],
                    ),
                    legend=alt.Legend(
                        title="期间类型",
                        orient="bottom",
                        direction="horizontal",
                    ),
                ),
                tooltip=[
                    alt.Tooltip("期间:N"),
                    alt.Tooltip("期间类型:N"),
                    alt.Tooltip("情景:N"),
                    alt.Tooltip(
                        "数值:Q",
                        title=chart_metric,
                        format=y_format,
                    ),
                ],
            )
            .properties(
                height=410,
                title=alt.TitleParams(
                    text=chart_title,
                    anchor="middle",
                    fontSize=14,
                ),
            )
            .configure_view(stroke=None)
            .configure_axis(
                labelColor="#667085",
                titleColor="#475467",
                gridColor="#e8edf4",
                domainColor="#d8dee8",
                labelFontSize=11,
                titleFontSize=12,
            )
            .configure_legend(
                labelColor="#475467",
                titleColor="#667085",
                orient="bottom",
            )
        )
        st.altair_chart(chart, use_container_width=True)

        # ── 情景对比表（财务报表式：指标为行、年度为列）──
        render_section_header(
            "情景对比表",
            "指标为行、年度为列，便于横向比较三种情景。",
        )

        comparison_stmt = build_scenario_comparison_statement(
            assumptions, forecasts, years,
        )
        # 百分比行：含「毛利率」「净利率」的行
        ratio_rows = {
            row for row in comparison_stmt["指标"]
            if "毛利率" in row or "净利率" in row
        }
        formatted_comparison = format_forecast_statement(
            comparison_stmt, ratio_rows=ratio_rows,
        )
        st.dataframe(
            formatted_comparison,
            hide_index=True,
            use_container_width=True,
        )

        # 基期数据性质脚注
        footnote = build_base_period_footnote(assumptions)
        st.caption(footnote)

    # ════════════════════════════════════════════════════════
    # Tab 2：单情景明细（Bull / Base / Bear 各自财务报表式）
    # ════════════════════════════════════════════════════════
    with tab_detail:
        detail_scenario = st.radio(
            "选择情景",
            ["Bull", "Base", "Bear"],
            key="forecast_detail_scenario",
            horizontal=True,
        )

        render_section_header(
            f"{detail_scenario} 情景明细",
            "从分部收入到公司利润，按财务报表阅读顺序展示。",
        )

        detail_stmt = build_scenario_detail_statement(
            assumptions, forecasts, detail_scenario, years,
        )
        # 百分比行：含「率」或「增长率」的行
        detail_ratio_rows = {
            row for row in detail_stmt["指标"]
            if "率" in row or "增长率" in row
        }
        formatted_detail = format_forecast_statement(
            detail_stmt, ratio_rows=detail_ratio_rows,
        )
        st.dataframe(
            formatted_detail,
            hide_index=True,
            use_container_width=True,
        )

        # 基期数据性质脚注
        st.caption(footnote)

    render_next_step_button("forecast")
