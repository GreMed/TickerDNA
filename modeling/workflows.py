"""工作流函数集合 - 不依赖 Streamlit UI，可独立测试。

包含：
- perform_company_search: 公司搜索
- perform_research: 公司研究/业务拆分
- check_export_integrity: 导出前完整性检查
- perform_export: Excel 导出
- company_switch_cleanup: 切换公司时的状态清除清单
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from modeling.company_data import (
    CompanyCandidate,
    CompanySearchError,
    search_companies,
)
from modeling.engine import (
    build_forecast,
    normalize_assumptions,
    summary_table,
)
from modeling.exporter import export_excel
from modeling.generator import research_company_assumptions

logger = logging.getLogger(__name__)


COMPANY_SWITCH_CLEAR_KEYS = (
    "assumptions",
    "source",
    "assumption_version",
    "fallback_message",
    "research_error",
    "research_context",
    "pending_split_confirmation",
    "export_error",
    "selected_company",
    "_forecast_results",
    "_forecast_summary",
    "_assumptions_reviewed",
)


def company_switch_cleanup(session_state_like: dict) -> list[str]:
    """切换公司时清除上一家公司的状态，返回被清除的键名列表。

    参数:
        session_state_like: 类字典对象（如 st.session_state 或测试用 dict）

    返回:
        实际被清除的键名列表
    """
    cleared = []
    for key in COMPANY_SWITCH_CLEAR_KEYS:
        if key in session_state_like:
            del session_state_like[key]
            cleared.append(key)
    return cleared


def perform_company_search(query: str) -> tuple[list[CompanyCandidate], str | None]:
    """执行公司搜索，返回候选列表和错误信息（如有）。

    可测试函数，不依赖 Streamlit UI。
    异常只写入日志，返回通俗用户提示。
    """
    query = (query or "").strip()
    if not query:
        return [], "请输入公司名称或股票代码。"
    try:
        candidates = search_companies(query)
        return candidates, None
    except CompanySearchError:
        logger.warning("公司搜索失败（CompanySearchError）", exc_info=True)
        return [], "搜索失败，请稍后重试或改用股票代码搜索。"
    except Exception:
        logger.exception("搜索过程中出现异常")
        return [], "搜索过程中出现异常，请稍后重试或改用股票代码搜索。"


def perform_research(
    company: CompanyCandidate,
    company_context: str,
    split_basis: dict[str, str],
) -> tuple[dict | None, str | None, str | None, str | None, list | None]:
    """执行研究，返回 assumptions、source、fallback 提示和错误信息。

    可测试函数，不依赖 Streamlit UI。
    返回: (assumptions, source, fallback_message, error_message, available_dimensions)
    """
    try:
        assumptions, source = research_company_assumptions(
            company, company_context, split_basis=split_basis
        )

        if assumptions.get("disclosure_access_blocked"):
            return None, None, None, (
                "当前未能读取官方披露数据，因此无法判断该口径是否已披露。"
                "请稍后重试，或补充更完整的公司经营资料后重新研究。"
            ), None

        if assumptions.get("split_basis_unavailable"):
            return (
                assumptions,
                source,
                None,
                None,
                assumptions.get("available_split_dimensions", []),
            )

        source_category = assumptions.get("source_category", "")
        source_text = source or ""
        fallback_message = None
        has_snapshot = "内置官方快照" in source_category or "内置官方快照" in source_text
        has_estimation = "估算" in source_category or "模型估算" in source_text
        has_live = "实时" in source_category or "官方披露抓取" in source_text

        if has_snapshot and not has_live:
            fallback_message = (
                "ℹ️ 当前使用内置官方快照数据。"
                "实时官方披露数据暂不可用，"
                f"但数据来源已明确标注为「{source_category}」，"
                "不等于实时公司披露。"
            )
        elif has_estimation and not has_live and not has_snapshot:
            fallback_message = (
                f"⚠️ 当前数据为模型估算（{source_category}），"
                "并非公司披露的官方数据。"
            )
        elif "混合" in source_category and has_snapshot:
            fallback_message = (
                f"ℹ️ 当前数据为混合来源（{source_category}），"
                "部分使用内置官方快照。实时官方披露数据未完全获取，"
                "数据不等于实时公司披露。"
            )

        return assumptions, source, fallback_message, None, None
    except Exception:
        logger.exception("研究过程中出现异常")
        return None, None, None, "研究过程中出现问题，请稍后重试或检查输入信息。", None


def _is_valid_number(value: Any) -> bool:
    """判断值是否为有限数值（非 NaN、非 inf、非字符串）。"""
    if value is None:
        return False
    if not isinstance(value, (int, float)):
        return False
    if pd.isna(value):
        return False
    return math_isfinite(value)


def _has_valid_growth(seg: dict) -> bool:
    """检查分部是否有有效的收入增长率。

    分部存在有效 base_growth 时通过；
    没有 base_growth，但每个预测年度都有有效 yearly base_growth 时也通过。
    """
    if _is_valid_number(seg.get("base_growth")):
        return True

    yearly = seg.get("yearly_assumptions")
    if not yearly or not isinstance(yearly, dict):
        return False

    for year, annual in yearly.items():
        if not isinstance(annual, dict):
            return False
        if not _is_valid_number(annual.get("base_growth")):
            return False
    return True


def _has_valid_margin(seg: dict) -> bool:
    """检查分部是否有有效的毛利率。

    分部存在有效 base_gross_margin 时通过；
    没有 base_gross_margin，但每个预测年度都有有效 yearly base_gross_margin 时也通过。
    """
    if _is_valid_number(seg.get("base_gross_margin")):
        return True

    yearly = seg.get("yearly_assumptions")
    if not yearly or not isinstance(yearly, dict):
        return False

    for year, annual in yearly.items():
        if not isinstance(annual, dict):
            return False
        if not _is_valid_number(annual.get("base_gross_margin")):
            return False
    return True


def check_export_integrity(
    assumptions: dict,
    forecasts: dict[str, pd.DataFrame],
    summary: pd.DataFrame,
) -> tuple[bool, list[str]]:
    """导出前完整性检查。

    检查项：
    1. assumptions 和 segments 存在
    2. 分部名称有效
    3. 分部有有效且有限的基期收入
    4. 分部有 Base 增长率或逐年度增长率
    5. 分部有 Base 毛利率或逐年度毛利率
    6. Bull/Base/Bear 三情景完整
    7. 预测结果没有 NaN/inf
    8. 分部收入合计与公司总收入一致
    9. summary 与情景结果一致

    返回: (是否通过, 问题列表)
    """
    problems: list[str] = []

    if not assumptions:
        problems.append("缺少假设数据，无法生成 Excel。请先完成公司研究。")
        return False, problems

    segments = assumptions.get("segments", [])
    if not segments:
        problems.append("缺少业务分部数据。请至少确认一个收入分部。")
        return False, problems

    for idx, seg in enumerate(segments):
        name = seg.get("name", f"分部{idx + 1}")
        name_str = str(name).strip() if name is not None else ""

        if not name_str:
            problems.append(f"第 {idx + 1} 个分部缺少名称。")
            continue

        base_rev = seg.get("base_revenue")
        if base_rev is None or not isinstance(base_rev, (int, float)):
            problems.append(f"分部「{name_str}」缺少有效的基期收入数据。")
        elif pd.isna(base_rev) or not math_isfinite(base_rev):
            problems.append(f"分部「{name_str}」的基期收入无效（NaN 或无穷大）。")

        has_growth = _has_valid_growth(seg)
        if not has_growth:
            problems.append(f"分部「{name_str}」缺少有效的收入增长率假设。")

        has_margin = _has_valid_margin(seg)
        if not has_margin:
            problems.append(f"分部「{name_str}」缺少有效的毛利率假设。")

    for scenario in ["Bull", "Base", "Bear"]:
        if scenario not in forecasts:
            problems.append(f"缺少 {scenario} 情景预测结果。")
        elif forecasts[scenario] is None or len(forecasts[scenario]) == 0:
            problems.append(f"{scenario} 情景没有预测数据。")

    for scenario, df in forecasts.items():
        if df is None:
            continue
        numeric_cols = df.select_dtypes(include=["number"]).columns
        if len(numeric_cols) == 0:
            continue
        if df[numeric_cols].isna().any().any():
            problems.append(f"{scenario} 情景预测中存在缺失值（NaN），请检查输入数据。")
            break
        if (df[numeric_cols] == float("inf")).any().any():
            problems.append(f"{scenario} 情景预测中存在无穷大值（inf），请检查增长率设置。")
            break
        if (df[numeric_cols] == float("-inf")).any().any():
            problems.append(f"{scenario} 情景预测中存在负无穷大值（-inf），请检查增长率设置。")
            break

    if "Base" in forecasts and len(forecasts["Base"]) > 0:
        df = forecasts["Base"]
        seg_cols = [f"{s['name']}收入" for s in segments if s.get("name")]
        existing_seg_cols = [c for c in seg_cols if c in df.columns]
        if existing_seg_cols and "收入" in df.columns:
            seg_sum = df[existing_seg_cols].sum(axis=1)
            total = df["收入"]
            max_diff = (seg_sum - total).abs().max()
            if max_diff > 0.01:
                problems.append(
                    f"分部收入合计与公司总收入不一致（最大差额 {max_diff:.2f}）。请检查分部数据。"
                )

    if summary is not None and len(summary) > 0:
        for scenario in ["Bull", "Base", "Bear"]:
            if scenario not in forecasts:
                continue
            df = forecasts[scenario]
            if df is None or len(df) == 0:
                continue
            summary_scenario = summary[summary["情景"] == scenario]
            if len(summary_scenario) == 0:
                problems.append(f"汇总表中缺少 {scenario} 情景数据。")
                continue
            if len(summary_scenario) != len(df):
                problems.append(f"汇总表与 {scenario} 情景行数不一致。")
                continue
            for col in ["收入", "毛利", "毛利率", "净利润", "净利率"]:
                if col in summary_scenario.columns and col in df.columns:
                    diff = (
                        summary_scenario[col].reset_index(drop=True)
                        - df[col].reset_index(drop=True)
                    ).abs().max()
                    if pd.notna(diff) and diff > 0.001:
                        problems.append(
                            f"汇总表与 {scenario} 情景的「{col}」数据不一致（最大差额 {diff:.4f}）。"
                        )
                        break

    return len(problems) == 0, problems


def perform_export(
    assumptions: dict,
    forecasts: dict[str, pd.DataFrame],
    summary: pd.DataFrame,
) -> tuple[bytes | None, str | None, list[str]]:
    """执行 Excel 导出，包含完整性检查。

    可测试函数，不依赖 Streamlit UI。
    返回: (excel_bytes, error_message, problems)
    """
    ok, problems = check_export_integrity(assumptions, forecasts, summary)
    if not ok:
        return None, None, problems

    try:
        excel_bytes = export_excel(assumptions, forecasts, summary)
        if not excel_bytes or len(excel_bytes) < 100:
            return None, "Excel 导出失败：生成的文件内容无效。请检查数据完整性或稍后重试。", []
        return excel_bytes, None, []
    except Exception:
        logger.exception("Excel 导出失败")
        return None, "Excel 导出失败。请检查数据或稍后重试。", []


def math_isfinite(value: Any) -> bool:
    """判断值是否为有限数（不依赖 math 模块的辅助函数）。"""
    try:
        return float("-inf") < float(value) < float("inf")
    except (TypeError, ValueError):
        return False
