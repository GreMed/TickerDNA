from __future__ import annotations

from io import BytesIO
from typing import Any

import pandas as pd

from modeling.engine import (
    SCENARIOS,
    baseline_metric_period_type,
    normalize_assumptions,
    profit_year_assumptions,
    segment_year_scenario_assumptions,
)


def _col_letter(col_idx: int) -> str:
    result = ""
    while col_idx >= 0:
        result = chr(65 + (col_idx % 26)) + result
        col_idx = col_idx // 26 - 1
    return result


# 列名 -> 格式类型映射
_COL_FORMAT_TYPES: dict[str, str] = {
    "收入": "amount",
    "毛利": "amount",
    "净利润": "amount",
    "经营费用": "amount",
    "其他损益": "amount",
    "税前利润": "amount",
    "所得税": "amount",
    "总收入": "amount",
    "数值": "amount",
    "基期收入": "amount",
    "披露利润": "amount",
    "收入占比": "percent",
    "毛利率": "percent",
    "净利率": "percent",
    "Base增长率": "percent",
    "Bull增长率": "percent",
    "Bear增长率": "percent",
    "Base收入增长率": "percent",
    "Bull收入增长率": "percent",
    "Bear收入增长率": "percent",
    "Base毛利率": "percent",
    "Bull毛利率": "percent",
    "Bear毛利率": "percent",
    "收入增长率振幅": "percent",
    "毛利率振幅": "percent",
    "经营费用率": "percent",
    "其他损益率": "percent",
    "所得税率": "percent",
    "披露毛利率": "percent",
    "披露利润率": "percent",
    "基期毛利率": "percent",
    "年度": "integer",
}


def _get_format_type(col_name: str) -> str:
    return _COL_FORMAT_TYPES.get(col_name, "text")


def _segment_data_nature(segment: dict) -> str:
    """根据分部的 basis / gross_margin_basis 判断基期性质。

    注意：yearly_assumptions 中某一年是 user_defined 不应影响基期数据性质。
    """
    basis = str(segment.get("basis", "estimated"))
    gm_basis = str(segment.get("gross_margin_basis", "estimated"))

    if basis == "user_defined" or gm_basis == "user_defined":
        return "用户定义"
    if basis == "reported":
        return "公司披露"
    return "模型估算"


def _assumption_data_nature(segment: dict, year: int) -> str:
    """根据 yearly_assumptions[year].basis 和 segment 整体 basis 判断年度假设性质。"""
    basis = str(segment.get("basis", "estimated"))
    if basis == "user_defined":
        return "用户定义"
    yearly = segment.get("yearly_assumptions", {})
    year_entry = yearly.get(str(year), {})
    if str(year_entry.get("basis", "")) == "user_defined":
        return "用户定义"
    return "模型初始假设"


class _Layout:
    """管理所有工作表的行列映射。"""

    def __init__(self, segments: list[dict], years: list[int]):
        self.segments = segments
        self.n_segments = len(segments)
        self.years = years
        self.n_years = len(years)

    # ---- 假设表 ----
    @property
    def asm_title_row(self) -> int:
        return 0

    @property
    def asm_subtitle_row(self) -> int:
        return 1

    @property
    def asm_section1_title_row(self) -> int:
        return 3

    @property
    def asm_header_row(self) -> int:
        return 4

    @property
    def asm_data_start(self) -> int:
        return 5

    def asm_segment_row(self, seg_idx: int, year_idx: int) -> int:
        return self.asm_data_start + seg_idx * self.n_years + year_idx

    @property
    def asm_section2_title_row(self) -> int:
        return self.asm_data_start + self.n_segments * self.n_years + 1

    @property
    def asm_fee_header_row(self) -> int:
        return self.asm_section2_title_row + 1

    @property
    def asm_fee_data_start(self) -> int:
        return self.asm_fee_header_row + 1

    def asm_fee_row(self, year_idx: int) -> int:
        return self.asm_fee_data_start + year_idx

    # 假设表列
    ASM_COL_SEGMENT = 0
    ASM_COL_YEAR = 1
    ASM_COL_BASE_REV = 2
    ASM_COL_BASE_GROWTH = 3
    ASM_COL_BASE_MARGIN = 4
    ASM_COL_GROWTH_SPREAD = 5
    ASM_COL_BULL_GROWTH = 6
    ASM_COL_BEAR_GROWTH = 7
    ASM_COL_MARGIN_SPREAD = 8
    ASM_COL_BULL_MARGIN = 9
    ASM_COL_BEAR_MARGIN = 10

    ASM_FEE_COL_YEAR = 0
    ASM_FEE_COL_OPEX = 1
    ASM_FEE_COL_OTHER = 2
    ASM_FEE_COL_TAX = 3
    ASM_COL_ASSUMPTION_NATURE = 11

    # ---- 基期实际表 ----
    @property
    def bl_title_row(self) -> int:
        return 0

    @property
    def bl_section1_title_row(self) -> int:
        return 4

    @property
    def bl_metrics_header_row(self) -> int:
        return 5

    @property
    def bl_metrics_data_start(self) -> int:
        return 6

    @property
    def bl_section2_title_row(self) -> int:
        return self.bl_metrics_data_start + 5 + 1

    @property
    def bl_segment_header_row(self) -> int:
        return self.bl_section2_title_row + 1

    @property
    def bl_segment_data_start(self) -> int:
        return self.bl_segment_header_row + 1

    # ---- 情景表 (Bull/Base/Bear) ----
    @property
    def sc_title_row(self) -> int:
        return 0

    @property
    def sc_header_row(self) -> int:
        return 3

    @property
    def sc_baseline_row(self) -> int:
        return 4

    def sc_forecast_row(self, year_idx: int) -> int:
        return self.sc_baseline_row + 1 + year_idx

    SC_COL_YEAR = 0

    def sc_segment_col(self, seg_idx: int) -> int:
        return 1 + seg_idx

    @property
    def sc_total_revenue_col(self) -> int:
        return 1 + self.n_segments

    @property
    def sc_gross_profit_col(self) -> int:
        return 2 + self.n_segments

    @property
    def sc_gross_margin_col(self) -> int:
        return 3 + self.n_segments

    @property
    def sc_opex_col(self) -> int:
        return 4 + self.n_segments

    @property
    def sc_other_col(self) -> int:
        return 5 + self.n_segments

    @property
    def sc_pretax_col(self) -> int:
        return 6 + self.n_segments

    @property
    def sc_tax_col(self) -> int:
        return 7 + self.n_segments

    @property
    def sc_net_profit_col(self) -> int:
        return 8 + self.n_segments

    @property
    def sc_net_margin_col(self) -> int:
        return 9 + self.n_segments

    # ---- 预测汇总表 ----
    @property
    def su_title_row(self) -> int:
        return 0

    @property
    def su_header_row(self) -> int:
        return 3

    @property
    def su_data_start(self) -> int:
        return 4

    def su_scenario_start(self, scenario_idx: int) -> int:
        return self.su_data_start + scenario_idx * self.n_years

    SU_COL_SCENARIO = 0
    SU_COL_YEAR = 1
    SU_COL_YEAR_TYPE = 2
    SU_COL_REVENUE = 3
    SU_COL_GP = 4
    SU_COL_GM = 5
    SU_COL_NET = 6
    SU_COL_NM = 7


def export_excel(
    assumptions: dict[str, Any],
    forecasts: dict[str, pd.DataFrame],
    summary: pd.DataFrame,
) -> bytes:
    output = BytesIO()

    years = [int(y) for y in forecasts["Base"]["年度"].tolist()]
    segments = assumptions["segments"]
    layout = _Layout(segments, years)

    company_name = assumptions.get("company_name", "公司")
    ticker = assumptions.get("symbol") or assumptions.get("ticker", "")
    currency = assumptions.get("currency", "")
    fiscal_year = assumptions.get("fiscal_year", "")
    source = assumptions.get("source", "")
    data_quality = assumptions.get("data_quality", "")

    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        workbook = writer.book
        workbook.set_properties(
            {
                "title": "TickerDNA",
                "subject": f"{company_name} 财务预测模型",
                "comments": "Generated by TickerDNA.",
            }
        )

        # ---- 格式定义 ----
        header_fmt = workbook.add_format(
            {
                "bold": True,
                "font_color": "white",
                "bg_color": "#17365D",
                "border": 0,
                "align": "center",
                "valign": "vcenter",
            }
        )
        title_fmt = workbook.add_format(
            {"bold": True, "font_size": 14, "font_color": "#17365D"}
        )
        subtitle_fmt = workbook.add_format(
            {"italic": True, "font_color": "#666666"}
        )
        section_fmt = workbook.add_format(
            {"bold": True, "font_size": 12, "font_color": "#17365D", "bg_color": "#E7E6E6"}
        )
        input_pct_fmt = workbook.add_format(
            {"font_color": "#0000FF", "num_format": "0.0%;[Red]-0.0%;-"}
        )
        input_amt_fmt = workbook.add_format(
            {"font_color": "#0000FF", "num_format": "#,##0.0;[Red]-#,##0.0;-"}
        )
        input_int_fmt = workbook.add_format(
            {"font_color": "#0000FF", "num_format": "0"}
        )
        formula_amt_fmt = workbook.add_format(
            {"num_format": "#,##0.0;[Red]-#,##0.0;-"}
        )
        formula_pct_fmt = workbook.add_format(
            {"num_format": "0.0%;[Red]-0.0%;-"}
        )
        formula_int_fmt = workbook.add_format({"num_format": "0"})
        text_fmt = workbook.add_format({})
        warning_fmt = workbook.add_format(
            {"font_color": "#FF6600", "italic": True}
        )

        def _write_header(sheet, row, col_names):
            for col, name in enumerate(col_names):
                sheet.write(row, col, name, header_fmt)

        def _col_width_by_type(fmt_type):
            if fmt_type == "amount":
                return 16
            if fmt_type == "percent":
                return 12
            if fmt_type == "integer":
                return 10
            return 18

        def _write_input_cell(sheet, row, col, value, col_name):
            ft = _get_format_type(col_name)
            if ft == "amount":
                sheet.write(row, col, value, input_amt_fmt)
            elif ft == "percent":
                sheet.write(row, col, value, input_pct_fmt)
            elif ft == "integer":
                sheet.write(row, col, value, input_int_fmt)
            else:
                sheet.write(row, col, value, input_pct_fmt if isinstance(value, float) and 0 < value < 1 else text_fmt)

        def _write_formula_cell(sheet, row, col, formula, col_name):
            ft = _get_format_type(col_name)
            if ft == "amount":
                sheet.write_formula(row, col, formula, formula_amt_fmt)
            elif ft == "percent":
                sheet.write_formula(row, col, formula, formula_pct_fmt)
            elif ft == "integer":
                sheet.write_formula(row, col, formula, formula_int_fmt)
            else:
                sheet.write_formula(row, col, formula, text_fmt)

        def _write_static_cell(sheet, row, col, value, col_name):
            ft = _get_format_type(col_name)
            if ft == "amount":
                sheet.write(row, col, value, formula_amt_fmt)
            elif ft == "percent":
                sheet.write(row, col, value, formula_pct_fmt)
            elif ft == "integer":
                sheet.write(row, col, value, formula_int_fmt)
            else:
                sheet.write(row, col, value, text_fmt)

        # ==================== Sheet 1: 基期实际 ====================
        ws_bl = workbook.add_worksheet("基期实际")
        ws_bl.hide_gridlines(2)
        ws_bl.write(layout.bl_title_row, 0, f"{company_name} 基期数据", title_fmt)
        ws_bl.write(
            layout.bl_title_row + 1,
            0,
            f"股票代码: {ticker}  |  基期年份: {fiscal_year}  |  币种/单位: {currency}",
            subtitle_fmt,
        )
        ws_bl.write(
            layout.bl_title_row + 2,
            0,
            f"数据质量: {data_quality}  |  来源类型: {assumptions.get('source_category', '') or source}  |  披露数据源: {assumptions.get('disclosure_provider', '')}",
            subtitle_fmt,
        )

        # 公司合计指标
        ws_bl.write(layout.bl_section1_title_row, 0, "公司合计指标", section_fmt)
        metric_col_names = ["指标", "数值", "数据性质", "基期年份"]
        _write_header(ws_bl, layout.bl_metrics_header_row, metric_col_names)
        ws_bl.set_column(0, 0, 12)
        ws_bl.set_column(1, 1, 18)
        ws_bl.set_column(2, 2, 14)
        ws_bl.set_column(3, 3, 12)

        # 指标数据
        baseline_metrics_map = {
            "收入": assumptions.get("actual_total_revenue"),
            "毛利": assumptions.get("actual_gross_profit"),
            "毛利率": assumptions.get("actual_gross_margin"),
            "净利润": assumptions.get("actual_net_profit"),
            "净利率": assumptions.get("actual_net_margin"),
        }
        for idx, metric in enumerate(["收入", "毛利", "毛利率", "净利润", "净利率"]):
            row = layout.bl_metrics_data_start + idx
            period_type = baseline_metric_period_type(assumptions, metric)
            value = baseline_metrics_map[metric]
            if value is None:
                from modeling.engine import baseline_metrics as bm
                value = bm(assumptions)[metric]
                period_type = "基期估算"
            ws_bl.write(row, 0, metric, text_fmt)
            ft = "amount" if metric in ("收入", "毛利", "净利润") else "percent"
            _write_static_cell(ws_bl, row, 1, value, metric)
            ws_bl.write(row, 2, period_type, text_fmt)
            ws_bl.write(row, 3, fiscal_year, text_fmt)

        # 业务分部基期数据
        ws_bl.write(layout.bl_section2_title_row, 0, "业务分部基期数据", section_fmt)
        seg_col_names = [
            "业务分部", "基期收入", "收入占比", "基期毛利率", "毛利率性质", "数据性质",
            "资料依据", "来源类型", "披露数据源", "资料标题", "资料URL",
        ]
        _write_header(ws_bl, layout.bl_segment_header_row, seg_col_names)
        ws_bl.set_column(0, 0, 18)
        ws_bl.set_column(1, 1, 16)
        ws_bl.set_column(2, 2, 12)
        ws_bl.set_column(3, 3, 14)
        ws_bl.set_column(4, 4, 14)
        ws_bl.set_column(5, 5, 12)
        ws_bl.set_column(6, 6, 30)
        ws_bl.set_column(7, 7, 14)
        ws_bl.set_column(8, 8, 16)
        ws_bl.set_column(9, 9, 30)
        ws_bl.set_column(10, 10, 40)

        # 读取真实来源字段（优先使用新字段，兼容旧字段）
        source_category = assumptions.get("source_category", "") or assumptions.get("source_type", "")
        disclosure_provider = assumptions.get("disclosure_provider", "") or assumptions.get("disclosure_source", "")
        sources_list = assumptions.get("sources", [])
        # 从 sources 列表提取第一个 title 和 url（兼容旧字段）
        if sources_list:
            first_source = sources_list[0]
            source_title = first_source.get("title", "") or assumptions.get("source_title", "")
            source_url = first_source.get("url", "") or assumptions.get("source_url", "")
        else:
            source_title = assumptions.get("source_title", "")
            source_url = assumptions.get("source_url", "")

        segment_total = sum(float(s.get("base_revenue", 0)) for s in segments)
        for seg_idx, segment in enumerate(segments):
            row = layout.bl_segment_data_start + seg_idx
            rev = float(segment.get("base_revenue", 0))
            ratio = rev / segment_total if segment_total else 0
            gm = segment.get("reported_gross_margin")
            if gm is None:
                gm = segment.get("base_gross_margin")
            gm_basis_label = {
                "reported": "公司披露",
                "derived": "按公司合计反推",
                "estimated": "模型估算",
                "user_defined": "用户定义",
            }.get(str(segment.get("gross_margin_basis", "estimated")), "模型估算")

            data_nature = _segment_data_nature(segment)

            ws_bl.write(row, 0, segment["name"], text_fmt)
            _write_static_cell(ws_bl, row, 1, rev, "基期收入")
            _write_static_cell(ws_bl, row, 2, ratio, "收入占比")
            _write_static_cell(ws_bl, row, 3, gm, "基期毛利率")
            ws_bl.write(row, 4, gm_basis_label, text_fmt)
            ws_bl.write(row, 5, data_nature, text_fmt)
            ws_bl.write(row, 6, segment.get("evidence", ""), text_fmt)
            ws_bl.write(row, 7, source_category, text_fmt)
            ws_bl.write(row, 8, disclosure_provider, text_fmt)
            ws_bl.write(row, 9, source_title, text_fmt)
            ws_bl.write(row, 10, source_url, text_fmt)

        # 资料来源区域（多来源逐条列出）
        sources_section_row = layout.bl_segment_data_start + len(segments) + 1
        if sources_list:
            ws_bl.write(sources_section_row, 0, "资料来源", section_fmt)
            for src_idx, src in enumerate(sources_list):
                src_row = sources_section_row + 1 + src_idx
                ws_bl.write(src_row, 0, f"  {src_idx + 1}. {src.get('title', '')}", text_fmt)
                ws_bl.write(src_row, 1, src.get("url", ""), text_fmt)

        ws_bl.freeze_panes(layout.bl_metrics_data_start, 1)

        # ==================== Sheet 2: 假设 ====================
        ws_asm = workbook.add_worksheet("假设")
        ws_asm.hide_gridlines(2)
        ws_asm.write(layout.asm_title_row, 0, f"{company_name} 假设参数", title_fmt)
        ws_asm.write(
            layout.asm_subtitle_row,
            0,
            f"预测年数: {len(years)} 年  |  收入增长率振幅: {assumptions.get('growth_scenario_spread', 0):.1%}  |  毛利率振幅: {assumptions.get('gross_margin_scenario_spread', 0):.1%}",
            subtitle_fmt,
        )
        ws_asm.write(layout.asm_section1_title_row, 0, "分部假设", section_fmt)

        asm_col_names = [
            "业务分部", "年度", "基期收入", "Base增长率", "Base毛利率",
            "收入增长率振幅", "Bull增长率", "Bear增长率",
            "毛利率振幅", "Bull毛利率", "Bear毛利率", "假设性质",
        ]
        _write_header(ws_asm, layout.asm_header_row, asm_col_names)
        for col, name in enumerate(asm_col_names):
            ws_asm.set_column(col, col, _col_width_by_type(_get_format_type(name)))

        # 写入分部假设数据
        # 用户只编辑：基期收入（仅每分部第一行）、Base增长率、Base毛利率、两种振幅
        # Bull/Bear 增长率和毛利率由 Excel 公式自动计算
        for seg_idx, segment in enumerate(segments):
            seg_name = segment["name"]
            base_rev = segment["base_revenue"]
            for year_idx, year in enumerate(years):
                row = layout.asm_segment_row(seg_idx, year_idx)
                excel_row = row + 1  # Excel 1-based 行号

                base_growth, base_margin = segment_year_scenario_assumptions(
                    assumptions, segment, year, "Base"
                )

                # 业务名称和年份为普通黑色文本
                ws_asm.write(row, layout.ASM_COL_SEGMENT, seg_name, text_fmt)
                ws_asm.write(row, layout.ASM_COL_YEAR, year, formula_int_fmt)

                # 基期收入：仅每分部第一行为蓝色输入，后续为黑色公式引用
                if year_idx == 0:
                    _write_input_cell(ws_asm, row, layout.ASM_COL_BASE_REV, base_rev, "基期收入")
                else:
                    first_row_excel = layout.asm_segment_row(seg_idx, 0) + 1
                    base_rev_formula = f"=C{first_row_excel}"
                    _write_formula_cell(ws_asm, row, layout.ASM_COL_BASE_REV, base_rev_formula, "基期收入")

                # 蓝色输入：Base增长率、Base毛利率
                _write_input_cell(ws_asm, row, layout.ASM_COL_BASE_GROWTH, base_growth, "Base增长率")
                _write_input_cell(ws_asm, row, layout.ASM_COL_BASE_MARGIN, base_margin, "Base毛利率")
                _write_input_cell(ws_asm, row, layout.ASM_COL_GROWTH_SPREAD, assumptions["growth_scenario_spread"], "收入增长率振幅")

                # Bull增长率 = Base增长率 + 收入增长率振幅  — 黑色公式
                # Phase 12B-2 收口：不使用 MIN(...,2) 旧边界截断，Bull = Base + 振幅
                base_growth_cl = _col_letter(layout.ASM_COL_BASE_GROWTH)
                growth_spread_cl = _col_letter(layout.ASM_COL_GROWTH_SPREAD)
                bull_growth_formula = f"={base_growth_cl}{excel_row}+{growth_spread_cl}{excel_row}"
                _write_formula_cell(ws_asm, row, layout.ASM_COL_BULL_GROWTH, bull_growth_formula, "Bull增长率")

                # Bear增长率 = Base增长率 - 收入增长率振幅  — 黑色公式
                # Phase 12B-2 收口：不使用 MAX(...,-0.8) 旧边界截断，Bear = Base - 振幅
                bear_growth_formula = f"={base_growth_cl}{excel_row}-{growth_spread_cl}{excel_row}"
                _write_formula_cell(ws_asm, row, layout.ASM_COL_BEAR_GROWTH, bear_growth_formula, "Bear增长率")

                # 毛利率振幅 — 蓝色输入
                _write_input_cell(ws_asm, row, layout.ASM_COL_MARGIN_SPREAD, assumptions["gross_margin_scenario_spread"], "毛利率振幅")

                # Bull毛利率 = Base毛利率 + 毛利率振幅  — 黑色公式
                # Phase 12B-2 收口：不使用 MIN(...,1) 旧边界截断，Bull = Base + 振幅
                base_margin_cl = _col_letter(layout.ASM_COL_BASE_MARGIN)
                margin_spread_cl = _col_letter(layout.ASM_COL_MARGIN_SPREAD)
                bull_margin_formula = f"={base_margin_cl}{excel_row}+{margin_spread_cl}{excel_row}"
                _write_formula_cell(ws_asm, row, layout.ASM_COL_BULL_MARGIN, bull_margin_formula, "Bull毛利率")

                # Bear毛利率 = Base毛利率 - 毛利率振幅  — 黑色公式
                # Phase 12B-2 收口：不使用 MAX(...,0) 旧边界截断，Bear = Base - 振幅
                bear_margin_formula = f"={base_margin_cl}{excel_row}-{margin_spread_cl}{excel_row}"
                _write_formula_cell(ws_asm, row, layout.ASM_COL_BEAR_MARGIN, bear_margin_formula, "Bear毛利率")

                # 假设性质 — 普通黑色文本
                nature = _assumption_data_nature(segment, year)
                ws_asm.write(row, layout.ASM_COL_ASSUMPTION_NATURE, nature, text_fmt)

        # 费用假设
        ws_asm.write(layout.asm_section2_title_row, 0, "费用假设", section_fmt)
        fee_col_names = ["年度", "经营费用率", "其他损益率", "所得税率"]
        _write_header(ws_asm, layout.asm_fee_header_row, fee_col_names)
        for year_idx, year in enumerate(years):
            row = layout.asm_fee_row(year_idx)
            opex_ratio, other_ratio = profit_year_assumptions(
                assumptions, year, year_idx
            )
            ws_asm.write(row, layout.ASM_FEE_COL_YEAR, year, formula_int_fmt)
            _write_input_cell(ws_asm, row, layout.ASM_FEE_COL_OPEX, opex_ratio, "经营费用率")
            _write_input_cell(ws_asm, row, layout.ASM_FEE_COL_OTHER, other_ratio, "其他损益率")
            _write_input_cell(ws_asm, row, layout.ASM_FEE_COL_TAX, assumptions["tax_rate"], "所得税率")

        ws_asm.freeze_panes(layout.asm_data_start, 2)

        # ==================== Sheet 2b: 假设依据 ====================
        rationale_items = assumptions.get("rationale_items", [])
        if rationale_items:
            ws_rt = workbook.add_worksheet("假设依据")
            ws_rt.hide_gridlines(2)
            ws_rt.write(0, 0, f"{company_name} 假设依据", title_fmt)
            ws_rt.write(1, 0, f"股票代码: {ticker}  |  币种/单位: {currency}", subtitle_fmt)

            rt_headers = [
                "业务分部", "年度", "指标", "数值",
                "预测方法", "依据说明", "置信度", "是否用户修改",
                "驱动因子类型", "是否占位假设",
            ]
            _write_header(ws_rt, 3, rt_headers)
            ws_rt.set_column(0, 0, 16)
            ws_rt.set_column(1, 1, 8)
            ws_rt.set_column(2, 2, 12)
            ws_rt.set_column(3, 3, 10)
            ws_rt.set_column(4, 4, 18)
            ws_rt.set_column(5, 5, 60)
            ws_rt.set_column(6, 6, 8)
            ws_rt.set_column(7, 7, 12)
            ws_rt.set_column(8, 8, 18)
            ws_rt.set_column(9, 9, 12)

            from modeling.rationale import (
                CONFIDENCE_LABELS,
                MARGIN_DRIVER_LABELS,
                METRIC_LABELS,
                METHOD_LABELS,
                REVENUE_DRIVER_LABELS,
            )

            for idx, item in enumerate(rationale_items):
                row = 4 + idx
                ws_rt.write(row, 0, item["segment_name"], text_fmt)
                ws_rt.write(row, 1, item["year"], formula_int_fmt)
                ws_rt.write(
                    row, 2,
                    METRIC_LABELS.get(item["metric"], item["metric"]),
                    text_fmt,
                )
                ws_rt.write(row, 3, item["value"], formula_pct_fmt)
                ws_rt.write(
                    row, 4,
                    METHOD_LABELS.get(item["method"], item["method"]),
                    text_fmt,
                )
                ws_rt.write(row, 5, item["rationale"], text_fmt)
                ws_rt.write(
                    row, 6,
                    CONFIDENCE_LABELS.get(item["confidence"], item["confidence"]),
                    text_fmt,
                )
                ws_rt.write(
                    row, 7,
                    "是" if item["is_user_modified"] else "否",
                    text_fmt,
                )
                # 驱动因子类型
                driver_type = item.get("driver_type", "")
                driver_labels = (
                    REVENUE_DRIVER_LABELS
                    if item["metric"] == "revenue_growth"
                    else MARGIN_DRIVER_LABELS
                )
                ws_rt.write(
                    row, 8,
                    driver_labels.get(driver_type, driver_type) if driver_type else "",
                    text_fmt,
                )
                # 是否占位假设
                ws_rt.write(
                    row, 9,
                    "是" if item.get("is_placeholder", True) else "否",
                    text_fmt,
                )

            ws_rt.freeze_panes(4, 0)

        # ==================== Sheet 3: 预测汇总 ====================
        ws_su = workbook.add_worksheet("预测汇总")
        ws_su.hide_gridlines(2)
        ws_su.write(layout.su_title_row, 0, f"{company_name} 预测汇总", title_fmt)
        ws_su.write(layout.su_title_row + 1, 0, f"币种/单位: {currency}", subtitle_fmt)

        su_col_names = ["情景", "年度", "年度类型", "收入", "毛利", "毛利率", "净利润", "净利率"]
        _write_header(ws_su, layout.su_header_row, su_col_names)
        for col, name in enumerate(su_col_names):
            ws_su.set_column(col, col, _col_width_by_type(_get_format_type(name)))

        scenario_idx_map = {s: i for i, s in enumerate(SCENARIOS)}
        for scenario in SCENARIOS:
            scenario_idx = scenario_idx_map[scenario]
            sc_data_start = layout.su_data_start + scenario_idx * layout.n_years
            sc_ws = scenario  # 工作表名
            sc_total_rev_col_letter = _col_letter(layout.sc_total_revenue_col)
            sc_gp_col_letter = _col_letter(layout.sc_gross_profit_col)
            sc_gm_col_letter = _col_letter(layout.sc_gross_margin_col)
            sc_net_col_letter = _col_letter(layout.sc_net_profit_col)
            sc_nm_col_letter = _col_letter(layout.sc_net_margin_col)

            for year_idx, year in enumerate(years):
                row = sc_data_start + year_idx
                excel_row = row + 1
                sc_forecast_excel_row = layout.sc_forecast_row(year_idx) + 1

                ws_su.write(row, layout.SU_COL_SCENARIO, scenario, text_fmt)
                ws_su.write(row, layout.SU_COL_YEAR, year, formula_int_fmt)
                ws_su.write(row, layout.SU_COL_YEAR_TYPE, "预测", text_fmt)

                rev_formula = f"='{sc_ws}'!{sc_total_rev_col_letter}{sc_forecast_excel_row}"
                _write_formula_cell(ws_su, row, layout.SU_COL_REVENUE, rev_formula, "收入")

                gp_formula = f"='{sc_ws}'!{sc_gp_col_letter}{sc_forecast_excel_row}"
                _write_formula_cell(ws_su, row, layout.SU_COL_GP, gp_formula, "毛利")

                gm_formula = f"='{sc_ws}'!{sc_gm_col_letter}{sc_forecast_excel_row}"
                _write_formula_cell(ws_su, row, layout.SU_COL_GM, gm_formula, "毛利率")

                net_formula = f"='{sc_ws}'!{sc_net_col_letter}{sc_forecast_excel_row}"
                _write_formula_cell(ws_su, row, layout.SU_COL_NET, net_formula, "净利润")

                nm_formula = f"='{sc_ws}'!{sc_nm_col_letter}{sc_forecast_excel_row}"
                _write_formula_cell(ws_su, row, layout.SU_COL_NM, nm_formula, "净利率")

        ws_su.freeze_panes(layout.su_data_start, 2)

        # ==================== Sheets 4-6: Bull / Base / Bear ====================
        scenario_growth_col_map = {"Bull": layout.ASM_COL_BULL_GROWTH, "Base": layout.ASM_COL_BASE_GROWTH, "Bear": layout.ASM_COL_BEAR_GROWTH}
        scenario_margin_col_map = {"Bull": layout.ASM_COL_BULL_MARGIN, "Base": layout.ASM_COL_BASE_MARGIN, "Bear": layout.ASM_COL_BEAR_MARGIN}

        # 读取基期指标
        from modeling.engine import baseline_metrics as _bm
        baseline = _bm(assumptions)
        base_revenues = {s["name"]: s["base_revenue"] for s in segments}
        base_gross_margins = {}
        for s in segments:
            gm = s.get("reported_gross_margin")
            if gm is None:
                gm = s.get("base_gross_margin", 0)
            base_gross_margins[s["name"]] = gm

        for scenario in SCENARIOS:
            ws_sc = workbook.add_worksheet(scenario)
            ws_sc.hide_gridlines(2)
            ws_sc.write(layout.sc_title_row, 0, f"{company_name} {scenario} 情景明细", title_fmt)
            ws_sc.write(layout.sc_title_row + 1, 0, f"币种/单位: {currency}", subtitle_fmt)

            # 列标题
            sc_col_names = ["年度"]
            for s in segments:
                sc_col_names.append(f"{s['name']}收入")
            sc_col_names.extend([
                "总收入", "毛利", "毛利率", "经营费用", "其他损益",
                "税前利润", "所得税", "净利润", "净利率",
            ])
            _write_header(ws_sc, layout.sc_header_row, sc_col_names)

            # 设置列宽
            ws_sc.set_column(0, 0, 10)
            for col, name in enumerate(sc_col_names[1:], 1):
                ws_sc.set_column(col, col, _col_width_by_type(_get_format_type(name)))

            # 基期行（公式引用假设表的基期收入，确保修改假设表能驱动预测）
            bl_row = layout.sc_baseline_row
            bl_excel_row = bl_row + 1
            ws_sc.write(bl_row, layout.SC_COL_YEAR, f"基期 {fiscal_year}", text_fmt)
            total_rev = 0.0
            total_gp = 0.0
            for seg_idx, segment in enumerate(segments):
                seg_name = segment["name"]
                rev = base_revenues[seg_name]
                gm = base_gross_margins[seg_name]
                gp = rev * gm
                total_rev += rev
                total_gp += gp
                # 用公式引用假设表的基期收入单元格
                asm_base_rev_row = layout.asm_segment_row(seg_idx, 0) + 1
                asm_base_rev_col_letter = _col_letter(layout.ASM_COL_BASE_REV)
                base_rev_formula = f"='假设'!{asm_base_rev_col_letter}{asm_base_rev_row}"
                _write_formula_cell(ws_sc, bl_row, layout.sc_segment_col(seg_idx), base_rev_formula, "收入")
            _write_static_cell(ws_sc, bl_row, layout.sc_total_revenue_col, total_rev, "收入")
            _write_static_cell(ws_sc, bl_row, layout.sc_gross_profit_col, total_gp, "毛利")
            gm = total_gp / total_rev if total_rev else 0
            _write_static_cell(ws_sc, bl_row, layout.sc_gross_margin_col, gm, "毛利率")

            # 基期费用等
            base_opex = total_rev * assumptions["base_opex_ratio"]
            base_other = total_rev * assumptions["base_other_ratio"]
            base_pretax = total_gp - base_opex + base_other
            base_tax = max(base_pretax, 0) * assumptions["tax_rate"]
            base_net = base_pretax - base_tax
            base_nm = base_net / total_rev if total_rev else 0
            _write_static_cell(ws_sc, bl_row, layout.sc_opex_col, base_opex, "经营费用")
            _write_static_cell(ws_sc, bl_row, layout.sc_other_col, base_other, "其他损益")
            _write_static_cell(ws_sc, bl_row, layout.sc_pretax_col, base_pretax, "税前利润")
            _write_static_cell(ws_sc, bl_row, layout.sc_tax_col, base_tax, "所得税")
            _write_static_cell(ws_sc, bl_row, layout.sc_net_profit_col, base_net, "净利润")
            _write_static_cell(ws_sc, bl_row, layout.sc_net_margin_col, base_nm, "净利率")

            # 预测行（公式）
            growth_col = scenario_growth_col_map[scenario]
            margin_col = scenario_margin_col_map[scenario]
            for year_idx, year in enumerate(years):
                row = layout.sc_forecast_row(year_idx)
                excel_row = row + 1  # 1-based for formulas
                prev_excel_row = excel_row - 1

                ws_sc.write(row, layout.SC_COL_YEAR, year, formula_int_fmt)

                # 各分部收入公式
                for seg_idx, segment in enumerate(segments):
                    seg_col = layout.sc_segment_col(seg_idx)
                    seg_col_letter = _col_letter(seg_col)
                    asm_growth_row = layout.asm_segment_row(seg_idx, year_idx) + 1  # Excel 1-based
                    asm_growth_col_letter = _col_letter(growth_col)
                    formula = f"={seg_col_letter}{prev_excel_row}*(1+'假设'!{asm_growth_col_letter}{asm_growth_row})"
                    _write_formula_cell(ws_sc, row, seg_col, formula, "收入")

                # 总收入 = SUM(各分部收入)
                first_seg_col = layout.sc_segment_col(0)
                last_seg_col = layout.sc_segment_col(layout.n_segments - 1)
                first_letter = _col_letter(first_seg_col)
                last_letter = _col_letter(last_seg_col)
                total_rev_col = layout.sc_total_revenue_col
                total_rev_letter = _col_letter(total_rev_col)
                formula = f"=SUM({first_letter}{excel_row}:{last_letter}{excel_row})"
                _write_formula_cell(ws_sc, row, total_rev_col, formula, "收入")

                # 毛利 = SUM(各分部收入 * 各分部毛利率)
                gp_parts = []
                for seg_idx, segment in enumerate(segments):
                    seg_col = layout.sc_segment_col(seg_idx)
                    seg_letter = _col_letter(seg_col)
                    asm_margin_row = layout.asm_segment_row(seg_idx, year_idx) + 1
                    asm_margin_col_letter = _col_letter(margin_col)
                    gp_parts.append(f"{seg_letter}{excel_row}*'假设'!{asm_margin_col_letter}{asm_margin_row}")
                gp_formula = "=" + "+".join(gp_parts)
                _write_formula_cell(ws_sc, row, layout.sc_gross_profit_col, gp_formula, "毛利")

                # 毛利率 = 毛利 / 总收入
                gp_col_letter = _col_letter(layout.sc_gross_profit_col)
                gm_formula = f"=IF({total_rev_letter}{excel_row}=0,0,{gp_col_letter}{excel_row}/{total_rev_letter}{excel_row})"
                _write_formula_cell(ws_sc, row, layout.sc_gross_margin_col, gm_formula, "毛利率")

                # 经营费用 = 总收入 * 费用率
                asm_fee_row = layout.asm_fee_row(year_idx) + 1
                opex_formula = f"={total_rev_letter}{excel_row}*'假设'!B{asm_fee_row}"
                _write_formula_cell(ws_sc, row, layout.sc_opex_col, opex_formula, "经营费用")

                # 其他损益 = 总收入 * 其他损益率
                other_formula = f"={total_rev_letter}{excel_row}*'假设'!C{asm_fee_row}"
                _write_formula_cell(ws_sc, row, layout.sc_other_col, other_formula, "其他损益")

                # 税前利润 = 毛利 - 经营费用 + 其他损益
                gp_cl = _col_letter(layout.sc_gross_profit_col)
                opex_cl = _col_letter(layout.sc_opex_col)
                other_cl = _col_letter(layout.sc_other_col)
                pretax_formula = f"={gp_cl}{excel_row}-{opex_cl}{excel_row}+{other_cl}{excel_row}"
                _write_formula_cell(ws_sc, row, layout.sc_pretax_col, pretax_formula, "税前利润")

                # 所得税 = IF(税前利润>0, 税前利润*税率, 0)
                pretax_cl = _col_letter(layout.sc_pretax_col)
                tax_formula = f"=IF({pretax_cl}{excel_row}>0,{pretax_cl}{excel_row}*'假设'!D{asm_fee_row},0)"
                _write_formula_cell(ws_sc, row, layout.sc_tax_col, tax_formula, "所得税")

                # 净利润 = 税前利润 - 所得税
                tax_cl = _col_letter(layout.sc_tax_col)
                net_formula = f"={pretax_cl}{excel_row}-{tax_cl}{excel_row}"
                _write_formula_cell(ws_sc, row, layout.sc_net_profit_col, net_formula, "净利润")

                # 净利率 = IF(总收入=0, 0, 净利润/总收入)
                net_cl = _col_letter(layout.sc_net_profit_col)
                nm_formula = f"=IF({total_rev_letter}{excel_row}=0,0,{net_cl}{excel_row}/{total_rev_letter}{excel_row})"
                _write_formula_cell(ws_sc, row, layout.sc_net_margin_col, nm_formula, "净利率")

            ws_sc.freeze_panes(layout.sc_baseline_row + 1, 1)

        # ==================== 图表 ====================
        chart = workbook.add_chart({"type": "line"})
        scenario_colors = {"Bull": "#FF3B30", "Base": "#0868D7", "Bear": "#68B7FF"}
        sc_total_rev_letter = _col_letter(layout.sc_total_revenue_col)
        baseline_excel_row = layout.sc_baseline_row + 1
        last_forecast_excel_row = layout.sc_forecast_row(layout.n_years - 1) + 1

        for scenario in SCENARIOS:
            chart.add_series(
                {
                    "name": scenario,
                    "categories": [
                        scenario,
                        layout.sc_baseline_row,
                        layout.SC_COL_YEAR,
                        layout.sc_forecast_row(layout.n_years - 1),
                        layout.SC_COL_YEAR,
                    ],
                    "values": [
                        scenario,
                        layout.sc_baseline_row,
                        layout.sc_total_revenue_col,
                        layout.sc_forecast_row(layout.n_years - 1),
                        layout.sc_total_revenue_col,
                    ],
                    "line": {"color": scenario_colors[scenario], "width": 2.25},
                    "marker": {"type": "circle", "size": 6},
                }
            )
        chart.set_title({"name": "三情景收入趋势"})
        chart.set_y_axis(
            {
                "name": currency,
                "major_gridlines": {
                    "visible": True,
                    "format": {"color": "#E0E0E0"},
                },
            }
        )
        chart.set_x_axis({"name": "年度"})
        chart.set_legend({"position": "bottom"})
        chart.set_size({"width": 600, "height": 360})
        ws_su.insert_chart("J4", chart)

    output.seek(0)
    return output.getvalue()
