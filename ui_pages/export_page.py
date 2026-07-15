"""导出与交付页 — Step 6 Excel 下载与限制说明。

从原 app.py 迁移，保持业务逻辑不变。
"""
from __future__ import annotations

import html
import logging

import streamlit as st

from modeling.engine import build_forecast, summary_table
from modeling.workflows import check_export_integrity, perform_export

from ui_pages.state import (
    get_assumptions,
    require_export_ready,
)

logger = logging.getLogger("tickerdna.app")


def render_export_page(years: list[int]) -> None:
    """渲染导出与交付页。"""
    if not require_export_ready():
        return

    assumptions = get_assumptions()
    if not assumptions:
        return

    from ui_pages.theme import render_page_header
    render_page_header("Step 6", "导出与交付", "下载包含基期实际、假设、预测和情景对比的 Excel 模型。")

    # 重新计算 forecasts 和 summary（因为用户可能在假设页修改了假设）
    try:
        forecasts = build_forecast(assumptions, years)
        summary = summary_table(forecasts)
    except Exception as exc:
        logger.exception("导出页预测计算异常")
        st.error("预测计算出现问题，无法生成 Excel。请返回「假设与驱动因子」页检查输入。")
        return

    export_ok, export_problems = check_export_integrity(assumptions, forecasts, summary)

    if not export_ok:
        st.error("导出前数据完整性检查未通过：")
        for problem in export_problems:
            st.warning(f"  • {problem}")
        st.caption(
            "修复建议：请检查上面列出的问题，修正后重试导出。"
            "常见原因包括缺少分部数据、增长率设置异常等。"
        )
        if st.button("重新检查并导出", key="retry_export_check"):
            st.rerun()
    else:
        company_name = html.escape(str(assumptions.get("company_name") or assumptions.get("name") or "当前公司"))
        symbol = html.escape(str(assumptions.get("symbol") or assumptions.get("ticker") or "—"))
        fiscal_year = html.escape(str(assumptions.get("fiscal_year") or "—"))
        forecast_range = (
            f"FY{years[0]}E–FY{years[-1]}E" if years else "未确定"
        )
        st.markdown(
            '<div class="td-delivery-card">'
            '<div class="td-delivery-title">Excel 模型已准备好</div>'
            '<div class="td-delivery-desc">'
            f'{company_name}（{symbol}） · 基期 FY{fiscal_year} · '
            f'预测期 {forecast_range} · 7 个工作表'
            '</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        excel_bytes, export_error, _ = perform_export(assumptions, forecasts, summary)
        if export_error:
            st.error(export_error)
            if st.button("重试导出", key="retry_export_error"):
                st.rerun()
        elif excel_bytes:
            st.download_button(
                "下载 Excel 模型",
                data=excel_bytes,
                file_name="TickerDNA_forecast_model.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True,
            )
        else:
            st.error("Excel 导出失败：生成的文件内容无效。请检查数据完整性或稍后重试。")
            if st.button("重试导出", key="retry_export_invalid"):
                st.rerun()

    # Sheet 说明默认折叠，避免抢过下载主任务。
    st.divider()
    with st.expander("Excel 工作表说明（7 个工作表）", expanded=False):
        st.markdown(
            "**工作表顺序**（共 7 个 Sheet）：\n"
            "1. **基期实际**：公司合计指标、分部基期数据、来源类型、披露数据源、数据质量\n"
            "2. **假设**：基期收入（蓝色可编辑）、Base 增长率、Base 毛利率、Bull/Bear 公式、费用率、税率\n"
            "3. **假设依据**：每个 Base 假设的来源、预测方法、置信度、驱动因子类型、占位标记\n"
            "4. **预测汇总**：Bull/Base/Bear 三情景的收入、毛利、毛利率、净利润、净利率\n"
            "5. **Bull**：Bull 情景分部收入、毛利明细\n"
            "6. **Base**：Base 情景分部收入、毛利明细\n"
            "7. **Bear**：Bear 情景分部收入、毛利明细\n"
            "\n"
            "**颜色约定**：\n"
            "- 蓝色字体：模型输入（可编辑）\n"
            "- 黑色字体：公式计算结果\n"
            "\n"
            "**图表**：包含 Bull/Base/Bear 三情景收入趋势图。"
        )

    # 限制说明
    st.divider()
    with st.expander("⚠ 已知限制"):
        st.markdown(
            "- **Excel 当前暂不导出估值对照**\n"
            "- **产品当前仅提供 Apple、腾讯静态估值演示**\n"
            "- **不提供真实券商一致预期、真实目标价或实时估值数据**\n"
            "- **不做 EPS、现金流、资产负债表、现金流量表**\n"
            "- 预测基于 Base 假设 + 振幅生成 Bull/Bear，不是独立预测\n"
            "- 部分假设为资料不足时的初始假设，尚未接入真实经营数据\n"
            "- 预测逻辑基于规则推断，未实现真实因子拆解\n"
            "- 资料不足的初始假设不能被标记为高置信度"
        )
