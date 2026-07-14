"""Phase 12B-3：财务报表式预测表格纯函数。

提供三个纯函数，不依赖 Streamlit，可由页面和测试直接调用：

- build_scenario_comparison_statement() — 情景对比表（指标为行、年度为列）
- build_scenario_detail_statement()     — 单情景明细表（财务报表式）
- format_forecast_statement()           — 格式化函数（不改变底层数值）

页面表格、摘要、图表、导出底层数值来自同一套预测结果，
不在页面中重新计算另一套结果。

基期列数据性质规则（Phase 12B-3 收口）：
- 基期列只展示真实披露或能由真实披露指标直接计算的数据
- baseline_metric_period_type(...) == "实际" 的指标才进入基期列
- 模型估算值不得写进基期列（显示为 None → 格式化为 —）
- 分部收入：segment.basis == "reported" 才进入基期列
- 分部毛利：segment.basis == "reported" 且 gross_margin_basis == "reported"
           才进入基期列；derived/estimated 的分部毛利显示为 —
- 基期列标签按数据性质生成：全部为实际 → FY{year}A；
  全部为估算 → FY{year} 基期估算；混合 → FY{year} 基期
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from modeling.engine import (
    SCENARIOS,
    baseline_metric_period_type,
    baseline_metrics,
)

# 指标标签 → 引擎 DataFrame 列名映射
_RATIO_METRICS = {"毛利率", "净利率", "经营费用率", "其他损益率"}
_AMOUNT_METRICS = {
    "收入", "毛利", "经营费用", "其他损益",
    "税前利润", "所得税", "净利润",
}

# 基期指标标签 → baseline_metrics() 返回的中文键
# Phase 12B-3 收口：统一使用真实产品字段，不建立另一套含义不一致的字段
_BASELINE_METRIC_KEYS: dict[str, str] = {
    "收入": "收入",
    "毛利率": "毛利率",
    "净利润": "净利润",
    "净利率": "净利率",
    "毛利": "毛利",
}


def _fiscal_year_str(assumptions: dict[str, Any]) -> str:
    """获取 fiscal_year 字符串。"""
    return str(assumptions.get("fiscal_year", "")).strip()


def _base_period_nature(assumptions: dict[str, Any], metric: str) -> str:
    """获取基期某指标的数据性质标签。

    返回值：实际 / 基期估算
    """
    return baseline_metric_period_type(assumptions, metric)


def _forecast_column_label(year: int) -> str:
    """生成预测年度列标签，如 FY2026E。"""
    return f"FY{year}E"


def _base_period_label(assumptions: dict[str, Any]) -> str:
    """生成基期列标签，按数据性质选择后缀。

    发布安全规则：
    - 表格中进入基期列的数值全部为真实披露时：FY{year}A
    - 没有可用真实披露、只存在模型基期时：FY{year} 基期估算
    - 存在混合性质且仍需展示时：使用中性标签 FY{year} 基期
      （脚注明确逐指标性质）

    不在同一个 FY{year}A 列中放入模型估算数据。
    """
    fiscal_year = _fiscal_year_str(assumptions) or "基期"

    # 检查基期各指标的数据性质
    natures = get_base_period_natures(assumptions)
    nature_values = list(natures.values())

    all_actual = all(n == "实际" for n in nature_values)
    all_estimated = all(n != "实际" for n in nature_values)

    if all_actual:
        return f"FY{fiscal_year}A"
    elif all_estimated:
        return f"FY{fiscal_year} 基期估算"
    else:
        return f"FY{fiscal_year} 基期"


def _base_metric_value_if_actual(
    assumptions: dict[str, Any],
    metric: str,
) -> float | None:
    """获取基期指标值，仅当性质为「实际」时返回，否则返回 None。

    Phase 12B-3 收口：模型估算不得写进基期列。
    """
    nature = _base_period_nature(assumptions, metric)
    if nature != "实际":
        return None

    base_metrics = baseline_metrics(assumptions)
    base_key = _BASELINE_METRIC_KEYS.get(metric, metric)
    value = base_metrics.get(base_key)
    if value is not None and pd.notna(value):
        return float(value)
    return None


def build_scenario_comparison_statement(
    assumptions: dict[str, Any],
    forecasts: dict[str, pd.DataFrame],
    years: list[int],
) -> pd.DataFrame:
    """构建情景对比表（指标为行、年度为列）。

    列顺序：基期（如 FY2025A 或 FY2025 基期估算）→ FY2026E → FY2027E → ...
    第一列固定为「指标」。

    指标行包括：
    - Bull 收入 / Base 收入 / Bear 收入
    - Bull 毛利率 / Base 毛利率 / Bear 毛利率
    - Bull 净利润 / Base 净利润 / Bear 净利润
    - Bull 净利率 / Base 净利率 / Bear 净利率

    基期列数据性质规则：
    - 仅当 baseline_metric_period_type(...) == "实际" 时才显示基期值
    - 模型估算值不进入基期列（显示为 None → 格式化为 —）
    - Bull/Base/Bear 三情景共用同一基期值

    Returns:
        DataFrame，第一列为「指标」，后续列为各年度。
    """
    base_label = _base_period_label(assumptions)

    # 列顺序：指标 → 基期 → 各预测年度
    year_labels = [_forecast_column_label(y) for y in years]
    columns = ["指标", base_label] + year_labels

    rows: list[dict[str, Any]] = []

    # 12 个指标行：Bull/Base/Bear × 收入/毛利率/净利润/净利率
    metric_keys = [
        ("收入", "收入", False),
        ("毛利率", "毛利率", True),
        ("净利润", "净利润", False),
        ("净利率", "净利率", True),
    ]

    for scenario in SCENARIOS:
        for metric_label, base_key, _is_ratio in metric_keys:
            row: dict[str, Any] = {"指标": f"{scenario} {metric_label}"}

            # 基期值（Bull/Base/Bear 共用）
            # Phase 12B-3 收口：仅当性质为「实际」时才显示
            base_value = _base_metric_value_if_actual(assumptions, metric_label)
            row[base_label] = base_value

            # 预测年度值
            frame = forecasts.get(scenario)
            if frame is not None and len(frame) > 0:
                engine_col = _metric_label_to_engine_col(metric_label)
                for year in years:
                    col_label = _forecast_column_label(year)
                    year_rows = frame[frame["年度"] == year]
                    if len(year_rows) > 0 and engine_col in year_rows.columns:
                        val = year_rows.iloc[0][engine_col]
                        row[col_label] = float(val) if pd.notna(val) else None
                    else:
                        row[col_label] = None
            else:
                for year in years:
                    col_label = _forecast_column_label(year)
                    row[col_label] = None

            rows.append(row)

    return pd.DataFrame(rows, columns=columns)


def build_scenario_detail_statement(
    assumptions: dict[str, Any],
    forecasts: dict[str, pd.DataFrame],
    scenario: str,
    years: list[int],
) -> pd.DataFrame:
    """构建单情景财务报表式明细表（指标为行、年度为列）。

    指标顺序：
    - 各业务分部收入
    - 公司总收入
    - 总收入增长率
    - 各业务分部毛利（仅当引擎中存在计算结果时）
    - 公司毛利
    - 公司毛利率
    - 经营费用
    - 经营费用率
    - 其他损益
    - 其他损益率
    - 税前利润
    - 所得税
    - 净利润
    - 净利率

    基期列数据性质规则：
    - 公司收入/毛利/毛利率/净利润/净利率：仅当性质为「实际」时才显示
    - 分部收入：segment.basis == "reported" 才显示
    - 分部毛利：segment.basis == "reported" 且 gross_margin_basis == "reported"
               才显示；derived/estimated 的分部毛利显示为 None
    - 经营费用、其他损益、税前利润、所得税等没有真实历史字段时显示 None

    只能展示预测引擎中真实存在的字段。缺少历史值时显示 None（格式化为 —）。

    Returns:
        DataFrame，第一列为「指标」，后续列为基期 + 各预测年度。
    """
    base_label = _base_period_label(assumptions)

    year_labels = [_forecast_column_label(y) for y in years]
    columns = ["指标", base_label] + year_labels

    frame = forecasts.get(scenario)
    segments = assumptions.get("segments", [])

    rows: list[dict[str, Any]] = []

    def _add_row(label: str, base_val: Any, forecast_vals: dict[str, Any]) -> None:
        row: dict[str, Any] = {"指标": label}
        row[base_label] = base_val
        for yl in year_labels:
            row[yl] = forecast_vals.get(yl)
        rows.append(row)

    def _get_year_value(year: int, col: str) -> Any:
        if frame is None or len(frame) == 0:
            return None
        year_rows = frame[frame["年度"] == year]
        if len(year_rows) == 0:
            return None
        if col not in year_rows.columns:
            return None
        val = year_rows.iloc[0][col]
        return float(val) if pd.notna(val) else None

    # ── 收入部分 ──────────────────────────────
    # 各业务分部收入
    # Phase 12B-3 收口：segment.basis == "reported" 才进入基期列
    for seg in segments:
        seg_name = seg["name"]
        col = f"{seg_name}收入"
        seg_basis = seg.get("basis", "estimated")
        # 基期值：仅当 basis == "reported" 时显示
        if seg_basis == "reported":
            base_rev = seg.get("base_revenue")
            base_val = float(base_rev) if base_rev is not None else None
        else:
            base_val = None
        forecast_vals = {
            _forecast_column_label(y): _get_year_value(y, col) for y in years
        }
        _add_row(f"{seg_name}收入", base_val, forecast_vals)

    # 公司总收入
    # Phase 12B-3 收口：仅当性质为「实际」时才显示基期值
    base_val = _base_metric_value_if_actual(assumptions, "收入")
    forecast_vals = {
        _forecast_column_label(y): _get_year_value(y, "收入") for y in years
    }
    _add_row("公司总收入", base_val, forecast_vals)

    # 总收入增长率
    forecast_growth_vals: dict[str, Any] = {}
    for i, year in enumerate(years):
        col_label = _forecast_column_label(year)
        if i == 0:
            # 第一个预测年度增长率 = (预测收入 / 基期收入) - 1
            # 基期收入使用 baseline_metrics 的值（不论性质），
            # 因为增长率是预测年度相对于基期的变化
            base_metrics_all = baseline_metrics(assumptions)
            base_rev_for_growth = base_metrics_all.get("收入")
            rev = _get_year_value(year, "收入")
            if (rev is not None and base_rev_for_growth is not None
                    and base_rev_for_growth != 0):
                forecast_growth_vals[col_label] = rev / base_rev_for_growth - 1.0
            else:
                forecast_growth_vals[col_label] = None
        else:
            prev_year = years[i - 1]
            prev_rev = _get_year_value(prev_year, "收入")
            curr_rev = _get_year_value(year, "收入")
            if prev_rev is not None and curr_rev is not None and prev_rev != 0:
                forecast_growth_vals[col_label] = curr_rev / prev_rev - 1.0
            else:
                forecast_growth_vals[col_label] = None
    _add_row("总收入增长率", None, forecast_growth_vals)

    # ── 毛利部分 ──────────────────────────────
    # 各业务分部毛利（仅当引擎中存在计算结果时）
    # Phase 12B-3 收口：分部毛利在基期列的展示规则
    # - segment.basis == "reported" 且 gross_margin_basis == "reported" 才显示
    # - derived/estimated 的分部毛利基期显示为 None（格式化为 —）
    for seg in segments:
        seg_name = seg["name"]
        col = f"{seg_name}毛利"
        if frame is not None and col in frame.columns:
            seg_basis = seg.get("basis", "estimated")
            gm_basis = seg.get("gross_margin_basis", "estimated")
            # 基期分部毛利：仅当 basis == "reported" 且 gross_margin_basis == "reported"
            if seg_basis == "reported" and gm_basis == "reported":
                base_rev = seg.get("base_revenue")
                base_margin = seg.get("base_gross_margin")
                if base_rev is not None and base_margin is not None:
                    base_val = float(base_rev) * float(base_margin)
                else:
                    base_val = None
            else:
                # derived 或 estimated 的分部毛利率 → 基期毛利为空
                base_val = None
            forecast_vals = {
                _forecast_column_label(y): _get_year_value(y, col) for y in years
            }
            _add_row(f"{seg_name}毛利", base_val, forecast_vals)

    # 公司毛利
    # Phase 12B-3 收口：仅当性质为「实际」时才显示基期值
    base_val = _base_metric_value_if_actual(assumptions, "毛利")
    forecast_vals = {
        _forecast_column_label(y): _get_year_value(y, "毛利") for y in years
    }
    _add_row("公司毛利", base_val, forecast_vals)

    # 公司毛利率
    # Phase 12B-3 收口：仅当性质为「实际」时才显示基期值
    base_val = _base_metric_value_if_actual(assumptions, "毛利率")
    forecast_vals = {
        _forecast_column_label(y): _get_year_value(y, "毛利率") for y in years
    }
    _add_row("公司毛利率", base_val, forecast_vals)

    # ── 利润部分 ──────────────────────────────
    profit_metrics = [
        ("经营费用", "经营费用"),
        ("经营费用率", "经营费用率"),
        ("其他损益", "其他损益"),
        ("其他损益率", "其他损益率"),
        ("税前利润", "税前利润"),
        ("所得税", "所得税"),
        ("净利润", "净利润"),
        ("净利率", "净利率"),
    ]

    for label, engine_col in profit_metrics:
        # 基期值：经营费用、其他损益、税前利润、所得税没有真实历史字段
        # 净利润/净利率仅当性质为「实际」时显示
        if label == "净利润":
            base_val = _base_metric_value_if_actual(assumptions, "净利润")
        elif label == "净利率":
            base_val = _base_metric_value_if_actual(assumptions, "净利率")
        else:
            # 经营费用、其他损益、税前利润、所得税等没有真实历史字段
            base_val = None

        forecast_vals = {
            _forecast_column_label(y): _get_year_value(y, engine_col) for y in years
        }
        _add_row(label, base_val, forecast_vals)

    return pd.DataFrame(rows, columns=columns)


def format_forecast_statement(
    statement: pd.DataFrame,
    ratio_rows: set[str] | None = None,
) -> pd.DataFrame:
    """格式化财务报表表格，不改变底层数值。

    格式规则：
    - 金额：千位分隔，最多保留一位小数（如 1,234,567.8）
    - 百分比：保留一位小数（如 12.3%）
    - 空值：显示 —
    - 负数：保留负号
    - 第一列「指标」不格式化

    Args:
        statement: build_scenario_*_statement() 的输出
        ratio_rows: 百分比指标行名称集合（如 {"公司毛利率", "净利率", ...}）
                    如果为 None，自动推断包含「率」字的行

    Returns:
        格式化后的 DataFrame（字符串值），底层数值不变
    """
    if statement is None or len(statement) == 0:
        return statement

    result = statement.copy()
    cols = result.columns.tolist()

    if not cols:
        return result

    # 第一列是指标名称列，不格式化
    metric_col = cols[0]
    value_cols = cols[1:]

    # 自动推断百分比行
    if ratio_rows is None:
        ratio_rows = set()
        for val in result[metric_col]:
            if val and "率" in str(val):
                ratio_rows.add(str(val))
        # 总收入增长率也是百分比
        for val in result[metric_col]:
            if val and "增长率" in str(val):
                ratio_rows.add(str(val))

    for col in value_cols:
        formatted_vals = []
        for idx in result.index:
            metric_name = str(result.at[idx, metric_col])
            raw_val = result.at[idx, col]

            if raw_val is None or (isinstance(raw_val, float) and pd.isna(raw_val)):
                formatted_vals.append("—")
            elif metric_name in ratio_rows:
                formatted_vals.append(f"{float(raw_val):.1%}")
            else:
                formatted_vals.append(f"{float(raw_val):,.1f}")

        result[col] = formatted_vals

    return result


def _metric_label_to_engine_col(metric_label: str) -> str:
    """将指标标签映射到引擎 DataFrame 的列名。"""
    mapping = {
        "收入": "收入",
        "毛利率": "毛利率",
        "净利润": "净利润",
        "净利率": "净利率",
        "毛利": "毛利",
    }
    return mapping.get(metric_label, metric_label)


def get_base_period_natures(
    assumptions: dict[str, Any],
) -> dict[str, str]:
    """获取基期各指标的数据性质，用于表头说明或脚注。

    返回 dict：{"收入": "实际", "毛利": "基期估算", ...}
    """
    metrics = ["收入", "毛利", "毛利率", "净利润", "净利率"]
    return {m: _base_period_nature(assumptions, m) for m in metrics}


def build_base_period_footnote(
    assumptions: dict[str, Any],
) -> str:
    """生成基期数据性质脚注，诚实表达不同指标的数据来源差异。

    例如：「基期 FY2025：收入为公司披露，毛利率为基期估算，净利润为基期估算。」
    """
    natures = get_base_period_natures(assumptions)
    fiscal_year = _fiscal_year_str(assumptions) or "基期"

    parts = []
    for metric in ["收入", "毛利率", "净利润", "净利率"]:
        nature = natures.get(metric, "基期估算")
        if nature == "实际":
            parts.append(f"{metric}为公司披露")
        elif nature == "基期估算":
            parts.append(f"{metric}为基期估算")
        else:
            parts.append(f"{metric}为{nature}")

    return f"基期 FY{fiscal_year}：" + "，".join(parts) + "。"
