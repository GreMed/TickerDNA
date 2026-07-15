"""假设与驱动因子页 — Step 4 Base 假设、振幅、假设依据。

从原 app.py 的 assumption_editor() 和 _show_rationale_panel() 迁移，
保持业务逻辑不变。
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from modeling.engine import (
    GROSS_MARGIN_MAX,
    GROSS_MARGIN_MIN,
    GROWTH_MAX,
    GROWTH_MIN,
    validate_assumptions,
)
from modeling.evidence import (
    FORMULA_DESCRIPTIONS,
    FORMULA_LABELS,
    MARGIN_FACTOR_DESCRIPTIONS,
    MARGIN_FACTOR_LABELS,
    SOURCE_LABELS as EVIDENCE_SOURCE_LABELS,
    build_evidence_layer,
    evidence_layer_summary,
    get_company_adaptation_hint,
)
from modeling.rationale import (
    CONFIDENCE_LABELS,
    MARGIN_DRIVER_LABELS,
    METRIC_LABELS,
    METHOD_LABELS,
    REVENUE_DRIVER_LABELS,
    aggregate_metric_rationale,
    mark_user_modified,
    sync_rationale_values,
)

from ui_pages.state import (
    get_assumptions,
    render_assumption_next_button,
    require_assumptions,
)


def render_assumption_page(years: list[int]) -> dict | None:
    """渲染假设与驱动因子页，返回更新后的 assumptions 或 None。"""
    if not require_assumptions():
        return None

    assumptions = get_assumptions()
    if not assumptions:
        return None

    result = _assumption_editor(assumptions, years)
    if result is not None:
        st.session_state["assumptions"] = result
    render_assumption_next_button()
    return result


def _show_rationale_panel(assumptions: dict, years: list[int]) -> None:
    """Phase 12B-2 收口：假设依据按业务分部和指标聚合展示。

    规则：
    1. 第一层按业务分部组织。
    2. 每个业务分部内部只分为收入增长率和毛利率。
    3. 同一指标的所有预测年度汇总展示，不每个年份重复生成一张完整卡片。
    4. 指标顶部性质：分析同一指标所有年度的 is_user_modified / is_placeholder 后统一判定：
       - 全部为用户定义 → "用户定义"
       - 全部为资料不足初始假设 → "资料不足的初始假设"
       - 同时包含两种性质 → "混合：用户定义 + 资料不足的初始假设"
    5. "共同依据" 只展示真正跨年度共用的内容，对完全相同的 evidence_items 去重。
    6. 年度差异依据不重复复制共同依据：
       - 未修改且无逐年资料的年度 → "无单独年度差异依据，沿用共同依据。"
       - 用户修改但未填写修改理由 → "用户修改；未提供单独修改理由。"
       - 存在明确年度专属依据 → 显示对应内容
    7. 一个年度被用户修改、其他年度仍沿用初始值时显示：
       "除用户修改年度外，其余年度沿用同一初始假设，尚无足够资料支持逐年差异。"
    8. 不得把一个年度的"用户定义"提升为整个指标全部用户定义。
    """
    items = assumptions.get("rationale_items", [])
    if not items:
        return

    has_low = any(it["confidence"] == "low" for it in items)
    has_placeholder = any(it.get("is_placeholder", True) for it in items)
    has_real_evidence = any(it.get("has_real_evidence", False) for it in items)
    header = "假设依据"
    if has_low:
        header += " ⚠ 含低置信度假设"

    with st.expander(header, expanded=False):
        if has_low:
            st.warning(
                "部分假设依据不足（标记为低置信度），"
                "建议用户根据行业情况手动调整。"
            )

        if has_real_evidence:
            st.caption(
                "✅ 示范案例已使用真实历史数据（多期 CAGR + 同比）生成预测依据。"
                "有真实证据只表示预测有真实历史资料支持，不代表预测结果是公司披露。"
            )

        if has_placeholder:
            st.caption(
                "🔶 部分假设为资料不足的初始假设，"
                "尚未完成多期历史 CAGR / 行业 / 公司目标 / 产能 / 新品驱动分析。"
                "用户修改个别值后，假设性质将变为「用户定义」，但不会自动生成虚假的修改理由。"
            )

        # 按业务分部组织
        seg_names = sorted({it["segment_name"] for it in items})
        for seg_name in seg_names:
            seg_items = [it for it in items if it["segment_name"] == seg_name]
            with st.expander(f"📊 {seg_name}", expanded=False):
                # 按指标分组：revenue_growth / gross_margin
                for metric in ("revenue_growth", "gross_margin"):
                    metric_items = [
                        it for it in seg_items if it["metric"] == metric
                    ]
                    if not metric_items:
                        continue

                    metric_label = METRIC_LABELS.get(metric, metric)
                    st.markdown(f"**{metric_label}**")

                    # Phase 12B-2 收口：调用纯函数进行统一聚合分析
                    agg = aggregate_metric_rationale(metric_items)

                    # 性质标签
                    from ui_pages.theme import (
                        info_ai_estimate,
                        info_user_confirmed,
                        info_risk,
                        info_mixed,
                    )
                    tag = agg["metric_nature_tag"]
                    nature_text = agg["metric_nature"]
                    if tag == "user_defined":
                        nature_tag = info_user_confirmed(nature_text)
                    elif tag == "placeholder":
                        nature_tag = info_risk(nature_text)
                    elif tag == "mixed":
                        nature_tag = info_mixed(nature_text)
                    else:
                        nature_tag = info_ai_estimate(nature_text)

                    # 方法：取出现频次最高的 method
                    from collections import Counter
                    method_counter = Counter(
                        it["method"] for it in metric_items
                    )
                    method_value = (
                        method_counter.most_common(1)[0][0]
                        if method_counter
                        else "default"
                    )
                    method_label = METHOD_LABELS.get(method_value, method_value)

                    import html as _html
                    safe_method_label = _html.escape(str(method_label))
                    st.markdown(
                        f"方法：{safe_method_label} ｜ 性质：{nature_tag}",
                        unsafe_allow_html=True,
                    )

                    # Phase 13：展示历史锚点（CAGR / 同比）和来源信息
                    first_item = metric_items[0] if metric_items else {}
                    has_real = first_item.get("has_real_evidence", False)
                    if has_real:
                        cagr_val = first_item.get("historical_cagr")
                        yoy_val = first_item.get("historical_yoy")
                        src_name = first_item.get("source_name", "")
                        src_url = first_item.get("source_url", "")
                        pub_date = first_item.get("publication_date", "")

                        anchor_parts = []
                        if cagr_val is not None:
                            anchor_parts.append(f"历史 CAGR = {cagr_val:.1%}")
                        if yoy_val is not None:
                            anchor_parts.append(f"最近一年同比 = {yoy_val:.1%}")
                        if anchor_parts:
                            st.markdown(f"历史锚点：{' ｜ '.join(anchor_parts)}")

                        if src_name:
                            src_parts = [f"来源：{src_name}"]
                            if pub_date and pub_date != "未记录":
                                src_parts.append(f"日期：{pub_date}")
                            st.caption(" ｜ ".join(src_parts))
                        if src_url:
                            st.caption(f"来源 URL：{src_url}")

                    # 共同依据：只有全部年度完全一致才展示
                    common_rationale = agg["common_rationale"]
                    if common_rationale:
                        st.markdown(f"共同依据：{common_rationale}")
                    else:
                        st.caption("无跨全部预测年度共用的依据。")

                    # 共同证据：只有全部年度都共有的才展示
                    common_evidence = agg["common_evidence"]
                    if common_evidence:
                        for ev in common_evidence:
                            st.markdown(f"- {ev}")

                    # 部分用户修改提示
                    partial_notice = agg["partial_user_notice"]
                    if partial_notice:
                        st.caption(partial_notice)

                    # 紧凑表格：预测年度 / Base 数值 / 年度差异依据 / 置信度 / 假设性质
                    table_rows = []
                    for row in agg["annual_rows"]:
                        conf_label = CONFIDENCE_LABELS.get(
                            row["confidence"], row["confidence"]
                        )
                        conf_icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(
                            row["confidence"], "⚪"
                        )
                        table_rows.append({
                            "预测年度": f"FY{row['year']}E",
                            "Base 数值": f"{row['base_value']:.1%}",
                            "年度差异依据": row["year_diff"],
                            "置信度": f"{conf_icon} {conf_label}",
                            "假设性质": row["nature"],
                        })
                    # Phase 12B-2 收口：在 dataframe 前用 caption 显示年度差异依据汇总，
                    # 确保 dataframe 内的文字在页面 innerText 中可见
                    year_diff_hints = sorted({
                        row["年度差异依据"] for row in table_rows
                        if row["年度差异依据"] in (
                            "无单独年度差异依据，沿用共同依据。",
                            "用户修改；未提供单独修改理由。",
                            "无单独年度差异依据。",
                        )
                    })
                    if year_diff_hints:
                        st.caption(
                            "年度差异依据：" + "；".join(year_diff_hints)
                        )
                    st.dataframe(
                        pd.DataFrame(table_rows),
                        hide_index=True,
                        use_container_width=True,
                    )
                    st.markdown("")


def _show_forecast_logic_cards(assumptions: dict, years: list[int]) -> None:
    """为每个分部展示预测逻辑卡。

    展示：
    - 当前预测公式
    - 基期数据与来源（收入和毛利率分别标注）
    - 已获得的未来证据
    - 缺失的关键驱动数据
    - 当前 Base 假设
    - 置信度
    - 是否仍为资料不足的初始假设

    本阶段不因为选择了公式就伪装成已完成真实驱动因子预测。
    """
    evidence_layers = build_evidence_layer(assumptions, years)
    if not evidence_layers:
        return

    summary = evidence_layer_summary(evidence_layers)
    symbol = assumptions.get("symbol", "") or assumptions.get("ticker", "")
    adaptation = get_company_adaptation_hint(symbol) if symbol else None

    header = "预测逻辑卡"
    if summary["all_placeholder"]:
        header += " 🔶 全部为资料不足的初始假设"
    elif summary["placeholder_count"] > 0:
        header += f" ⚠ {summary['placeholder_count']} 个分部仍为资料不足的初始假设"

    with st.expander(header, expanded=False):
        st.markdown(
            "**预测逻辑卡**为每个分部展示预测公式、基期数据与来源、前瞻证据和缺失驱动。"
            "选择公式不等于已完成真实驱动因子预测——只有当关键驱动数据已获得且非占位时，"
            "预测才真正基于证据推导。"
        )

        # 规则建议的预测路径（待经营数据验证）
        if adaptation and adaptation.get("company"):
            st.info(
                f"🏢 **规则建议的预测路径，待经营数据验证**："
                f"{adaptation['company']} 适合"
                f"「{adaptation['formula_label']}」预测逻辑。{adaptation['note']}"
            )

        # 证据层摘要
        col1, col2, col3 = st.columns(3)
        col1.metric("分部数", summary["total_segments"])
        col2.metric("缺失驱动因子", summary["total_missing_drivers"])
        if summary["all_placeholder"]:
            col3.metric("资料不足的初始假设", f"{summary['placeholder_count']}/{summary['total_segments']}")
        else:
            col3.metric("真实证据", f"{summary['real_evidence_count']}/{summary['total_segments']}")

        # 每个分部的逻辑卡
        for layer in evidence_layers:
            _render_single_logic_card(layer, years)


def _render_single_logic_card(layer: dict, years: list[int]) -> None:
    """渲染单个分部的预测逻辑卡。"""
    seg_name = layer["segment_name"]
    formula_label = layer["formula_label"]
    formula_desc = layer["formula_description"]
    confidence = layer["confidence"]
    is_placeholder = layer["is_placeholder"]
    has_real_evidence = layer["has_real_evidence"]

    # 卡片标题
    conf_icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(confidence, "⚪")
    conf_label = {"high": "高", "medium": "中", "low": "低"}.get(confidence, confidence)

    if is_placeholder:
        status_tag = "🔶 资料不足的初始假设"
    elif has_real_evidence:
        status_tag = "✅ 证据推导"
    else:
        status_tag = "📊 用户输入"

    with st.expander(f"📋 {seg_name} — {formula_label} ｜ {conf_icon} {conf_label} ｜ {status_tag}"):
        # 1. 当前预测公式
        st.markdown(f"**预测公式**：`{formula_label}`")
        if formula_desc:
            st.caption(formula_desc)

        # 所需驱动因子（直接路径）
        required_drivers = layer["required_drivers"]
        if required_drivers:
            st.markdown(f"**直接路径驱动因子**：{'、'.join(required_drivers)}")

        # 细化路径（量价公式专属）
        if layer.get("has_detailed_path"):
            detailed_drivers = layer["detailed_drivers"]
            if detailed_drivers:
                st.markdown(
                    f"**细化路径驱动因子**（替代路径）："
                    f"{'、'.join(detailed_drivers)}"
                )

        # 2. 基期数据与来源（原子化，收入和毛利率分别标注）
        st.markdown("**基期数据与来源**")
        metric_evidence = layer["metric_evidence"]
        if metric_evidence:
            me_rows = []
            for me in metric_evidence:
                if me["metric"] == "revenue":
                    value_str = (
                        f"{me['value']:,.0f}" if me["value"] is not None else "❌ 缺失"
                    )
                elif me["metric"] == "gross_margin":
                    value_str = (
                        f"{me['value']:.1%}" if me["value"] is not None else "❌ 缺失"
                    )
                elif me["metric"] == "growth_rate":
                    if me["value"] is not None:
                        value_str = f"{me['value']:.1%}"
                    else:
                        value_str = "缺少多期历史数据"
                else:
                    value_str = "—"
                me_rows.append({
                    "指标": me["metric_label"],
                    "数值": value_str,
                    "资料性质": me["nature_label"],
                    "来源": me["source_name"] or "—",
                    "资料所属期间": me["fiscal_period"],
                    "发布日期": me["publication_date"],
                })
            st.dataframe(
                pd.DataFrame(me_rows),
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.caption("无基期数据")

        # 2.5 多期历史趋势
        historical_trend = layer.get("historical_trend")
        if historical_trend and historical_trend.get("has_real_history"):
            st.markdown("**多期历史趋势**")
            st.caption(
                "历史趋势可作为后续预测依据；本阶段尚未自动写入 Base、Bull 或 Bear，"
                "历史表现不代表未来结果。"
            )

            # 获取方式映射
            channel_labels = {
                "snapshot": "内置官方快照",
                "realtime": "实时官方披露",
                "uploaded_pdf": "上传 PDF",
                "": "—",
            }

            # 历史年度表 — 收入和毛利率分别展示独立来源
            hist_rows = []
            for hp in historical_trend["periods"]:
                rev_ev = hp["revenue_evidence"]
                gm_ev = hp.get("gross_margin_evidence")

                rev_str = (
                    f"{hp['revenue']:,.0f}" if hp["revenue"] is not None else "❌ 缺失"
                )
                rev_channel = hp.get("revenue_acquisition_channel", "")
                rev_page = hp.get("revenue_page_or_table", "")

                if hp["gross_margin"] is not None:
                    gm_str = f"{hp['gross_margin']:.1%}"
                    gm_nature_label = gm_ev["nature_label"] if gm_ev else "—"
                    gm_channel = hp.get("gross_margin_acquisition_channel", "")
                    gm_page = hp.get("gross_margin_page_or_table", "")
                    gm_pub = gm_ev["publication_date"] if gm_ev else "未记录"
                    gm_src = gm_ev["source_name"] if gm_ev else ""
                else:
                    gm_str = "❌ 缺失"
                    gm_nature_label = "缺失"
                    gm_channel = ""
                    gm_page = ""
                    gm_pub = "未记录"
                    gm_src = ""

                hist_rows.append({
                    "财年": hp["fiscal_year"],
                    "收入": rev_str,
                    "收入来源": rev_ev["source_name"] or "—",
                    "收入性质": rev_ev["nature_label"],
                    "收入获取方式": channel_labels.get(rev_channel, rev_channel or "—"),
                    "收入页码/表名": rev_page or "—",
                    "收入发布日期": rev_ev["publication_date"],
                    "毛利率": gm_str,
                    "毛利率来源": gm_src or "—",
                    "毛利率性质": gm_nature_label,
                    "毛利率获取方式": channel_labels.get(gm_channel, gm_channel or "—"),
                    "毛利率页码/表名": gm_page or "—",
                    "毛利率发布日期": gm_pub,
                })
            st.dataframe(
                pd.DataFrame(hist_rows),
                hide_index=True,
                use_container_width=True,
            )

            # 收入来源链接
            st.markdown("**收入来源链接**")
            for hp in historical_trend["periods"]:
                rev_ev = hp["revenue_evidence"]
                if rev_ev.get("source_url"):
                    st.markdown(
                        f"- {hp['fiscal_year']} 收入："
                        f"[{rev_ev['source_name'] or '来源'}]({rev_ev['source_url']})"
                    )
                else:
                    st.markdown(f"- {hp['fiscal_year']} 收入：无链接")

            # 毛利率来源链接
            has_any_gm = any(
                hp.get("gross_margin") is not None
                for hp in historical_trend["periods"]
            )
            if has_any_gm:
                st.markdown("**毛利率来源链接**")
                for hp in historical_trend["periods"]:
                    gm_ev = hp.get("gross_margin_evidence")
                    if hp["gross_margin"] is not None and gm_ev and gm_ev.get("source_url"):
                        st.markdown(
                            f"- {hp['fiscal_year']} 毛利率："
                            f"[{gm_ev['source_name'] or '来源'}]({gm_ev['source_url']})"
                        )
                    elif hp["gross_margin"] is not None:
                        st.markdown(f"- {hp['fiscal_year']} 毛利率：无链接")
                    else:
                        st.markdown(f"- {hp['fiscal_year']} 毛利率：❌ 缺失（未披露）")

            # 收入同比增长率
            yoy_list = historical_trend.get("revenue_yoy", [])
            if yoy_list:
                yoy_rows = []
                for yoy in yoy_list:
                    if yoy.get("growth_rate") is not None:
                        input_ids = yoy.get("input_evidence_ids", [])
                        yoy_rows.append({
                            "期间": f"{yoy['from_year']}→{yoy['to_year']}",
                            "收入同比": f"{yoy['growth_rate']:.1%}",
                            "性质": "由历史收入计算",
                            "输入证据": ", ".join(input_ids) if input_ids else "—",
                            "说明": yoy.get("note", ""),
                        })
                    else:
                        yoy_rows.append({
                            "期间": f"{yoy['from_year']}→{yoy['to_year']}",
                            "收入同比": "—",
                            "性质": "缺失",
                            "输入证据": "—",
                            "说明": yoy.get("note", "无法计算"),
                        })
                st.markdown("**收入同比增长率**")
                st.dataframe(
                    pd.DataFrame(yoy_rows),
                    hide_index=True,
                    use_container_width=True,
                )

            # 收入 CAGR
            cagr = historical_trend.get("revenue_cagr")
            if cagr:
                cagr_ids = cagr.get("input_evidence_ids", [])
                st.markdown(
                    f"**收入 CAGR**（{cagr['from_year']}–{cagr['to_year']}，"
                    f"{cagr['n_years']} 年）："
                    f"`{cagr['cagr']:.1%}`（由历史收入计算）"
                )
                if cagr_ids:
                    st.caption(f"输入证据：{', '.join(cagr_ids)}")
            else:
                st.caption("收入 CAGR：不足 3 个连续、可比、完整财年，无法计算")

            # 毛利率变化
            mc_list = historical_trend.get("margin_changes", [])
            if mc_list:
                mc_rows = []
                for mc in mc_list:
                    if mc.get("change_pp") is not None:
                        input_ids = mc.get("input_evidence_ids", [])
                        mc_rows.append({
                            "期间": f"{mc['from_year']}→{mc['to_year']}",
                            "毛利率变化": f"{mc['change_pp']:+.1f} pp",
                            "性质": "由历史毛利率计算",
                            "输入证据": ", ".join(str(x) for x in input_ids if x) if any(input_ids) else "—",
                        })
                    else:
                        mc_rows.append({
                            "期间": f"{mc['from_year']}→{mc['to_year']}",
                            "毛利率变化": "—",
                            "性质": "缺失",
                            "输入证据": "—",
                            "说明": mc.get("note", ""),
                        })
                st.markdown("**毛利率变化**")
                st.dataframe(
                    pd.DataFrame(mc_rows),
                    hide_index=True,
                    use_container_width=True,
                )

            # 缺失年份警告
            missing_years = historical_trend.get("missing_years", [])
            if missing_years:
                st.warning(
                    f"⚠ **缺失年份**：{', '.join(missing_years)}。"
                    "这些年份无数据，不参与趋势计算。"
                )

            # 不可比口径警告
            if not historical_trend.get("comparable", False):
                comp_note = historical_trend.get("comparability_note", "")
                st.warning(
                    f"⚠ **口径不可比**：{comp_note}。"
                    "已停止跨口径同比/CAGR 计算。"
                )
        elif historical_trend and not historical_trend.get("has_real_history"):
            st.markdown("**多期历史趋势**")
            st.caption(
                "缺少多期历史数据，无法计算趋势。"
                "历史趋势可作为后续预测依据；本阶段尚未自动写入 Base、Bull 或 Bear。"
            )

        # 3. 经营指标
        st.markdown("**经营指标**（关键驱动因子数据）")
        op_metrics = layer["operating_metrics"]
        if op_metrics:
            op_rows = []
            for om in op_metrics:
                source_label = EVIDENCE_SOURCE_LABELS.get(om["source"], om["source"])
                value_str = f"{om['value']:,.2f}" if om["value"] is not None else "❌ 缺失"
                op_rows.append({
                    "指标": om["name"],
                    "数值": value_str,
                    "单位": om["unit"] or "—",
                    "来源": source_label,
                    "适用年度": om["applicable_year"],
                })
            st.dataframe(
                pd.DataFrame(op_rows),
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.caption("无经营指标")

        # 4. 前瞻证据
        st.markdown("**前瞻证据**")
        fwd_evidence = layer["forward_evidence"]
        if fwd_evidence:
            fwd_rows = []
            for fe in fwd_evidence:
                source_label = EVIDENCE_SOURCE_LABELS.get(fe["source"], fe["source"])
                desc = fe["description"] if not fe["is_missing"] else "❌ 缺失"
                fwd_rows.append({
                    "类型": fe["evidence_type_label"],
                    "内容": desc,
                    "适用年度": fe["applicable_year"],
                    "来源": source_label,
                })
            st.dataframe(
                pd.DataFrame(fwd_rows),
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.caption("无前瞻证据")

        # 5. 缺失的关键驱动数据
        missing_drivers = layer["missing_drivers"]
        if missing_drivers:
            st.warning(
                f"⚠ **缺失关键驱动数据**：{'、'.join(missing_drivers)}。"
                "当前 Base 假设仍为占位值，未基于真实经营数据推导。"
            )

        # 6. 候选毛利率影响因子（规则建议，待证据验证）
        margin_factors = layer["margin_factor_labels"]
        if margin_factors:
            st.markdown(
                f"**候选毛利率影响因子（规则建议，待证据验证）**："
                f"{'、'.join(margin_factors)}"
            )
            for mf in layer["margin_factors"]:
                desc = MARGIN_FACTOR_DESCRIPTIONS.get(mf, "")
                label = MARGIN_FACTOR_LABELS.get(mf, mf)
                if desc:
                    st.caption(f"- {label}：{desc}")
        else:
            st.markdown(
                "**候选毛利率影响因子**：待判断（未匹配到规则建议的因子）"
            )

        # 7. 当前 Base 假设
        st.markdown("**当前 Base 假设**")
        base_rows = []
        for ba in layer["base_assumptions"]:
            base_rows.append({
                "年度": ba["year"],
                "收入增长率": f"{ba['base_growth']:.1%}" if ba["base_growth"] is not None else "—",
                "毛利率": f"{ba['base_gross_margin']:.1%}" if ba["base_gross_margin"] is not None else "—",
            })
        st.dataframe(
            pd.DataFrame(base_rows),
            hide_index=True,
            use_container_width=True,
        )

        # 8. 是否仍为资料不足的初始假设
        if is_placeholder:
            st.caption(
                "🔶 当前 Base 假设仍为资料不足的初始假设，未基于真实驱动因子数据推导。"
                "公式和驱动因子类型为规则推断，不代表已完成真实驱动因子预测。"
                "用户修改个别值不会改变整个分部的初始假设状态。"
            )
        elif has_real_evidence:
            st.caption(
                "✅ 当前 Base 假设基于真实证据推导（非资料不足的初始假设）。"
            )
        else:
            st.caption(
                "📊 当前 Base 假设为用户输入值，非资料不足的初始假设。"
            )


def _assumption_editor(assumptions: dict, years: list[int]) -> dict:
    """假设编辑主函数（从原 app.py 迁移，保持业务逻辑不变）。"""
    version = st.session_state.get("assumption_version", 0)
    from ui_pages.theme import render_page_header, render_section_header
    render_page_header("Step 3", "假设与驱动因子", "基于历史事实和已确认资料，形成可解释的 Base 假设。")

    st.markdown(
        '<div class="td-note-line">'
        '<strong>编辑顺序</strong>　先确认基期锚点，再设置逐年 Base 假设，'
        '最后用统一振幅生成 Bull / Bear。比例填写 15 代表 15%。'
        '</div>',
        unsafe_allow_html=True,
    )
    with st.expander("查看假设性质与使用规则", expanded=False):
        st.markdown(
            "- **基期实际数据**：来自公司披露或模型估算，已在上一页确认，表格中标注为"
            "「公司披露」「按公司合计反推」「模型估算」\n"
            "- **模型初始假设**：系统根据现有资料生成的默认预测假设\n"
            "- **用户修改假设**：您编辑后的值会自动标注为「用户定义」，"
            "后续预测结果将据此更新，**不等同于公司披露**"
        )

    def number(value: object, default: float = 0.0) -> float:
        return default if pd.isna(value) else float(value)

    def _basis_label(segment: dict) -> str:
        basis = str(segment.get("gross_margin_basis", segment.get("basis", "estimated")))
        if basis == "reported":
            return "公司披露"
        if basis == "derived":
            return "按公司合计反推"
        if basis == "estimated":
            return "模型估算"
        if basis == "user_defined":
            return "用户定义"
        return "模型估算"

    def _original_label(segment: dict) -> str:
        orig = segment.get("original_gross_margin_basis", "")
        if orig == "reported":
            return "原始来源：公司披露"
        if orig == "derived":
            return "原始来源：按公司合计反推"
        if orig == "estimated":
            return "原始来源：模型估算"
        return ""

    original_segments = {
        str(segment.get("name", "")): dict(segment)
        for segment in assumptions["segments"]
    }
    segment_rows = []
    for segment in assumptions["segments"]:
        basis_text = _basis_label(segment)
        orig_text = _original_label(segment)
        if orig_text and basis_text == "用户定义":
            display_basis = f"{basis_text}（{orig_text}）"
        else:
            display_basis = basis_text
        segment_rows.append(
            {
                "业务分部": segment["name"],
                "基期收入": segment["base_revenue"],
                "基期毛利率%": float(
                    segment.get("base_gross_margin", 0.45)
                )
                * 100,
                "数据性质": display_basis,
                "披露利润指标": segment.get("profit_metric_name") or "—",
                "披露利润": (
                    f"{float(segment['reported_profit']):,.1f}"
                    if segment.get("reported_profit") is not None
                    else "未披露"
                ),
                "披露利润率%": (
                    f"{float(segment['reported_profit_margin']) * 100:.1f}"
                    if segment.get("reported_profit_margin") is not None
                    else "未披露"
                ),
            }
        )

    render_section_header(
        "基期分部收入与毛利率",
        "这是预测起点；可在进入逐年预测前校正分部收入和毛利率。",
    )
    edited = st.data_editor(
        pd.DataFrame(segment_rows),
        num_rows="dynamic",
        hide_index=True,
        use_container_width=True,
        column_config={
            "业务分部": st.column_config.TextColumn(required=True),
            "基期收入": st.column_config.NumberColumn(min_value=0.0, format="%.1f"),
            "基期毛利率%": st.column_config.NumberColumn(
                min_value=0.0,
                max_value=100.0,
                format="%.1f",
            ),
            "披露利润指标": st.column_config.TextColumn(width="medium"),
            "披露利润": st.column_config.TextColumn(width="medium"),
            "披露利润率%": st.column_config.TextColumn(width="medium"),
        },
        disabled=["数据性质", "披露利润指标", "披露利润", "披露利润率%"],
        key=f"segment_editor_{version}",
    )

    updated_segments = []
    for _, row in edited.dropna(subset=["业务分部"]).iterrows():
        name = str(row["业务分部"])
        original = original_segments.get(name)
        if original:
            updated_revenue = number(row["基期收入"])
            updated_margin = number(row["基期毛利率%"], 45.0) / 100
            segment = {
                **original,
                "name": name,
                "base_revenue": updated_revenue,
                "base_gross_margin": updated_margin,
            }
            revenue_edited = (
                abs(float(original.get("base_revenue", 0)) - updated_revenue)
                > 1e-9
            )
            margin_edited = (
                abs(
                    float(original.get("base_gross_margin", 0.45))
                    - updated_margin
                )
                > 1e-9
            )
            if revenue_edited or margin_edited:
                orig_basis = original.get("gross_margin_basis", original.get("basis", "estimated"))
                if orig_basis != "user_defined":
                    segment["original_gross_margin_basis"] = orig_basis
                elif "original_gross_margin_basis" in original:
                    segment["original_gross_margin_basis"] = original["original_gross_margin_basis"]
                orig_evidence = original.get("evidence", "")
                if orig_evidence and not orig_evidence.startswith("原始来源："):
                    segment["original_evidence"] = orig_evidence
                segment["basis"] = "user_defined"
                segment["evidence"] = "用户修改"
                segment["gross_margin_basis"] = "user_defined"
                if margin_edited:
                    segment["reported_gross_margin"] = None
        else:
            segment = {
                "name": name,
                "base_revenue": number(row["基期收入"]),
                "base_gross_margin": (
                    number(row["基期毛利率%"], 45.0) / 100
                ),
                "base_growth": 0.10,
                "bull_growth": 0.15,
                "bear_growth": 0.05,
                "bull_gross_margin": 0.48,
                "bear_gross_margin": 0.42,
                "yearly_assumptions": {},
                "basis": "user_defined",
                "gross_margin_basis": "user_defined",
                "description": "",
                "evidence": "用户新增",
            }
        updated_segments.append(segment)
    assumptions["segments"] = updated_segments

    annual_lookup = {
        str(segment["name"]): segment for segment in assumptions["segments"]
    }
    annual_values: dict[tuple[str, int], dict[str, float]] = {}
    annual_user_edited: set[tuple[str, int]] = set()

    # Phase 12B-2：编辑器 key 与 symbol、assumption version、预测年度集合关联，
    # 不依赖页面位置 index（seg_idx），确保切换公司/版本后 key 正确刷新。
    symbol = assumptions.get("symbol") or assumptions.get("ticker", "")
    editor_key_prefix = f"{symbol}_{version}"

    # 逐年度 Base 收入增长率
    render_section_header(
        "Base 收入增长率",
        "按业务分部逐年填写；系统不会静默截断您输入的有限数值。",
    )
    # Phase 12B-2 收口：允许任意有限输入，不设旧的 -80%～200% 硬边界，
    # 不做静默截断；非常规输入仅显示警告，系统仍按原值计算。
    import math as _math
    for segment in assumptions["segments"]:
        seg_name = segment["name"]
        yearly = segment.get("yearly_assumptions", {})
        seg_default_growth = float(segment.get("base_growth", 0.10))
        orig_yearly_seg = original_segments.get(seg_name, {}).get(
            "yearly_assumptions", {}
        )

        st.markdown(f"**{seg_name}**")
        growth_cols = st.columns(len(years))
        for year_idx, year in enumerate(years):
            annual = yearly.get(str(year), {})
            current_growth = number(
                annual.get("base_growth"), seg_default_growth
            ) * 100

            input_key = f"growth_{editor_key_prefix}_{seg_name}_{year}"
            new_val = growth_cols[year_idx].number_input(
                f"FY{year}E（%）",
                value=current_growth,
                step=0.5,
                format="%.1f",
                key=input_key,
                help=f"{seg_name} FY{year}E 收入增长率",
            )

            # Phase 12B-2 收口：仅拒绝非有限输入（NaN/Inf），有限值按原值保留
            # 非有限值回退到分部默认值，提示必须包含实际采用的默认值
            try:
                parsed_growth = float(new_val)
                if not _math.isfinite(parsed_growth):
                    raise ValueError("non-finite")
            except (TypeError, ValueError):
                parsed_growth = seg_default_growth * 100
                growth_cols[year_idx].warning(
                    f"该输入不是有效的有限数值，系统未采用；"
                    f"已回退到默认值 {seg_default_growth * 100:.1f}%。"
                )
            new_growth = parsed_growth / 100

            # 非常规有限输入仅警告，不修改
            if new_growth > GROWTH_MAX or new_growth < GROWTH_MIN:
                growth_cols[year_idx].caption(
                    "⚠ 该输入超出常见观察范围，系统仍按您的输入值计算。"
                )

            annual_values.setdefault((seg_name, year), {})["base_growth"] = new_growth

            orig_val = orig_yearly_seg.get(str(year), {}).get(
                "base_growth", seg_default_growth
            )
            if abs(new_growth - orig_val) > 1e-9:
                annual_user_edited.add((seg_name, year))

    # 逐年度 Base 毛利率
    render_section_header(
        "Base 毛利率",
        "按业务分部逐年填写，数值将直接进入 Base 情景计算。",
    )
    for segment in assumptions["segments"]:
        seg_name = segment["name"]
        yearly = segment.get("yearly_assumptions", {})
        seg_default_margin = float(segment.get("base_gross_margin", 0.45))
        orig_yearly_seg = original_segments.get(seg_name, {}).get(
            "yearly_assumptions", {}
        )

        st.markdown(f"**{seg_name}**")
        margin_cols = st.columns(len(years))
        for year_idx, year in enumerate(years):
            annual = yearly.get(str(year), {})
            current_margin = number(
                annual.get("base_gross_margin"), seg_default_margin
            ) * 100

            input_key = f"margin_{editor_key_prefix}_{seg_name}_{year}"
            new_val = margin_cols[year_idx].number_input(
                f"FY{year}E（%）",
                value=current_margin,
                step=0.5,
                format="%.1f",
                key=input_key,
                help=f"{seg_name} FY{year}E 毛利率",
            )

            # Phase 12B-2 收口：仅拒绝非有限输入（NaN/Inf），有限值按原值保留
            # 非有限值回退到分部默认值，提示必须包含实际采用的默认值
            try:
                parsed_margin = float(new_val)
                if not _math.isfinite(parsed_margin):
                    raise ValueError("non-finite")
            except (TypeError, ValueError):
                parsed_margin = seg_default_margin * 100
                margin_cols[year_idx].warning(
                    f"该输入不是有效的有限数值，系统未采用；"
                    f"已回退到默认值 {seg_default_margin * 100:.1f}%。"
                )
            new_margin = parsed_margin / 100

            # 非常规有限输入仅警告，不修改
            if new_margin > GROSS_MARGIN_MAX or new_margin < GROSS_MARGIN_MIN:
                margin_cols[year_idx].caption(
                    "⚠ 该输入超出常见观察范围，系统仍按您的输入值计算。"
                )

            annual_values.setdefault((seg_name, year), {})["base_gross_margin"] = new_margin

            orig_val = orig_yearly_seg.get(str(year), {}).get(
                "base_gross_margin", seg_default_margin
            )
            if abs(new_margin - orig_val) > 1e-9:
                annual_user_edited.add((seg_name, year))

    # 清空 yearly_assumptions 并从 annual_values 重新填充
    for segment in assumptions["segments"]:
        segment["yearly_assumptions"] = {}

    for segment in assumptions["segments"]:
        name = segment["name"]
        for year in years:
            values = annual_values.get((name, year), {})
            base_growth = values.get(
                "base_growth",
                float(segment.get("base_growth", 0.10)),
            )
            base_margin = values.get(
                "base_gross_margin",
                float(segment.get("base_gross_margin", 0.45)),
            )
            year_entry = {
                "base_growth": base_growth,
                "base_gross_margin": base_margin,
            }
            if (name, year) in annual_user_edited:
                year_entry["basis"] = "user_defined"
            annual_lookup[name]["yearly_assumptions"][str(year)] = year_entry

    for name, year in annual_user_edited:
        mark_user_modified(assumptions, name, year, "revenue_growth")
        mark_user_modified(assumptions, name, year, "gross_margin")
    for seg in assumptions["segments"]:
        if seg.get("basis") == "user_defined":
            for year in years:
                mark_user_modified(assumptions, seg["name"], year, "revenue_growth")
        if seg.get("gross_margin_basis") == "user_defined":
            for year in years:
                mark_user_modified(assumptions, seg["name"], year, "gross_margin")

    sync_rationale_values(assumptions)

    # ── Phase 12B-2：情景振幅移至 Base 假设之后 ──────────────
    render_section_header(
        "情景振幅",
        "Bull / Bear 分别在 Base 基础上加减统一的增长率与毛利率振幅。",
    )
    spread_col1, spread_col2 = st.columns(2)
    with spread_col1:
        growth_spread = st.number_input(
            "收入增长率情景振幅（百分点）",
            min_value=0.0,
            max_value=100.0,
            value=float(assumptions.get("growth_scenario_spread", 0.05) * 100),
            step=1.0,
            key=f"growth_spread_{version}",
            help="Bull = Base + 振幅；Bear = Base - 振幅。",
        )
    with spread_col2:
        margin_spread = st.number_input(
            "毛利率情景振幅（百分点）",
            min_value=0.0,
            max_value=50.0,
            value=float(
                assumptions.get("gross_margin_scenario_spread", 0.03) * 100
            ),
            step=0.5,
            key=f"margin_spread_{version}",
            help="Bull = Base + 振幅；Bear = Base - 振幅。",
        )
    assumptions["growth_scenario_spread"] = growth_spread / 100
    assumptions["gross_margin_scenario_spread"] = margin_spread / 100

    # ── Bull/Bear 自动推导结果放入次级折叠区 ──────────────────
    scenario_preview = []
    for segment in assumptions["segments"]:
        name = segment["name"]
        preview_rows = {
            label: {"业务分部": name, "假设指标": label}
            for label in (
                "Bull收入增长率%",
                "Base收入增长率%",
                "Bear收入增长率%",
                "Bull毛利率%",
                "Base毛利率%",
                "Bear毛利率%",
            )
        }
        for year in years:
            values = annual_values.get((name, year), {})
            base_growth = values.get(
                "base_growth",
                float(segment.get("base_growth", 0.10)),
            )
            base_margin = values.get(
                "base_gross_margin",
                float(segment.get("base_gross_margin", 0.45)),
            )
            # Phase 12B-2 收口：Bull/Bear 由 Base ± 振幅直接计算，不做旧边界截断
            preview_rows["Bull收入增长率%"][str(year)] = (
                base_growth + assumptions["growth_scenario_spread"]
            ) * 100
            preview_rows["Base收入增长率%"][str(year)] = base_growth * 100
            preview_rows["Bear收入增长率%"][str(year)] = (
                base_growth - assumptions["growth_scenario_spread"]
            ) * 100
            preview_rows["Bull毛利率%"][str(year)] = (
                base_margin + assumptions["gross_margin_scenario_spread"]
            ) * 100
            preview_rows["Base毛利率%"][str(year)] = base_margin * 100
            preview_rows["Bear毛利率%"][str(year)] = (
                base_margin - assumptions["gross_margin_scenario_spread"]
            ) * 100
        scenario_preview.extend(preview_rows.values())

    with st.expander("Bull / Bear 自动推导结果", expanded=False):
        st.dataframe(
            pd.DataFrame(scenario_preview),
            hide_index=True,
            use_container_width=True,
            column_config={
                str(year): st.column_config.NumberColumn(format="%.1f")
                for year in years
            },
        )

    render_section_header(
        "经营费用率与其他损益率",
        "这两项不参与 Bull / Base / Bear 情景映射，三种情景在同一年度共用"
        "相同假设；快捷设置按基期逐年线性变化，0 表示保持不变。",
    )
    with st.expander("📖 了解这些指标如何影响净利润"):
        st.markdown(
            "**净利润计算链**（以 Base 情景为例）：\n"
            "1. **收入** = 各分部收入之和\n"
            "2. **毛利** = 收入 × 毛利率\n"
            "3. **经营费用** = 收入 × 经营费用率（通常含销售、管理、研发费用）\n"
            "4. **其他损益** = 收入 × 其他损益率（通常含利息、投资收益、汇兑损益等）\n"
            "5. **税前利润** = 毛利 - 经营费用 + 其他损益\n"
            "6. **所得税** = 税前利润 × 所得税率（仅当税前利润 > 0 时）\n"
            "7. **净利润** = 税前利润 - 所得税\n"
            "\n"
            "**关键影响**：\n"
            "- 经营费用率 ↑ → 税前利润 ↓ → 净利润 ↓\n"
            "- 其他损益率 ↑（正值）→ 税前利润 ↑ → 净利润 ↑\n"
            "- 所得税率 ↑ → 净利润 ↓（税前利润为正时）\n"
            "\n"
            "注：上述为简化模型，未考虑递延税、少数股东权益等复杂项。"
        )
    stored_opex_change = float(
        assumptions.get("opex_ratio_annual_change", 0.0)
    )
    stored_other_change = float(
        assumptions.get("other_ratio_annual_change", 0.0)
    )
    profit_change_col1, profit_change_col2 = st.columns(2)
    with profit_change_col1:
        opex_annual_change = (
            st.number_input(
                "经营费用率每年变动（百分点）",
                min_value=-20.0,
                max_value=20.0,
                value=stored_opex_change * 100,
                step=0.5,
                key=f"opex_annual_change_{version}",
                help=(
                    "0：与基期一致；+1：每年较上一年增加 1 个百分点；"
                    "-1：每年较上一年降低 1 个百分点。"
                ),
            )
            / 100
        )
    with profit_change_col2:
        other_annual_change = (
            st.number_input(
                "其他损益率每年变动（百分点）",
                min_value=-20.0,
                max_value=20.0,
                value=stored_other_change * 100,
                step=0.5,
                key=f"other_annual_change_{version}",
                help=(
                    "0：与基期一致；正数表示逐年增加，负数表示逐年降低。"
                ),
            )
            / 100
        )

    opex_change_updated = abs(
        opex_annual_change - stored_opex_change
    ) > 1e-12
    other_change_updated = abs(
        other_annual_change - stored_other_change
    ) > 1e-12
    assumptions["opex_ratio_annual_change"] = opex_annual_change
    assumptions["other_ratio_annual_change"] = other_annual_change

    base_opex_ratio = float(assumptions.get("base_opex_ratio", 0.23))
    base_other_ratio = float(assumptions.get("base_other_ratio", 0.0))
    existing_profit_yearly = assumptions.get(
        "yearly_profit_assumptions",
        {},
    )
    if not isinstance(existing_profit_yearly, dict):
        existing_profit_yearly = {}

    fiscal_year = str(assumptions.get("fiscal_year", "")).strip()
    base_period = f"基期 {fiscal_year}" if fiscal_year else "基期"
    profit_rows = [
        {
            "假设指标": "经营费用率%",
            base_period: base_opex_ratio * 100,
        },
        {
            "假设指标": "其他损益率%",
            base_period: base_other_ratio * 100,
        },
    ]
    for year_index, year in enumerate(years, start=1):
        existing = existing_profit_yearly.get(str(year), {})
        if not isinstance(existing, dict):
            existing = {}
        opex_value = (
            base_opex_ratio + opex_annual_change * year_index
            if opex_change_updated or "opex_ratio" not in existing
            else float(existing["opex_ratio"])
        )
        other_value = (
            base_other_ratio + other_annual_change * year_index
            if other_change_updated or "other_ratio" not in existing
            else float(existing["other_ratio"])
        )
        profit_rows[0][str(year)] = min(max(opex_value, 0.0), 0.9) * 100
        profit_rows[1][str(year)] = (
            min(max(other_value, -0.3), 0.3) * 100
        )

    profit_editor_key = (
        f"profit_year_editor_{version}_{years[0]}_{len(years)}_"
        f"{opex_annual_change:.4f}_{other_annual_change:.4f}"
    )
    edited_profit_assumptions = st.data_editor(
        pd.DataFrame(profit_rows),
        hide_index=True,
        use_container_width=True,
        disabled=["假设指标", base_period],
        column_config={
            base_period: st.column_config.NumberColumn(format="%.1f"),
            **{
                str(year): st.column_config.NumberColumn(format="%.1f")
                for year in years
            },
        },
        key=profit_editor_key,
    )
    assumptions["yearly_profit_assumptions"] = {}
    profit_row_lookup = {
        str(row["假设指标"]): row
        for _, row in edited_profit_assumptions.iterrows()
    }
    for year in years:
        assumptions["yearly_profit_assumptions"][str(year)] = {
            "opex_ratio": number(
                profit_row_lookup["经营费用率%"][str(year)],
                base_opex_ratio * 100,
            )
            / 100,
            "other_ratio": number(
                profit_row_lookup["其他损益率%"][str(year)],
                base_other_ratio * 100,
            )
            / 100,
        }

    assumptions["tax_rate"] = (
        st.number_input(
            "所得税率（%）", min_value=0.0, max_value=60.0,
            value=float(assumptions["tax_rate"] * 100), step=1.0,
            key=f"tax_rate_{version}",
        )
        / 100
    )

    render_section_header(
        "依据与预测逻辑",
        "需要复核时再展开查看，不影响上方已经完成的假设编辑。",
    )
    _show_rationale_panel(assumptions, years)
    _show_forecast_logic_cards(assumptions, years)

    input_warnings = validate_assumptions(assumptions, years)
    if input_warnings:
        for msg in input_warnings:
            st.warning(msg)

    return assumptions
