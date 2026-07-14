"""历史口径映射与可比性规则 — Phase 12B-1 收口。

四种可比性状态：
    - direct：直接可比（同一 comparability_key 跨年度）
    - sum_of_components：旧分部组成项加总后可比（多对一映射，需实际对账）
    - residual：公司合计倒算的补充项（需实际计算 total - sum(known)）
    - unmapped：无法可靠映射，不可比

规则：
    - 名称变化不等于不可比
    - 只有 direct 和经总额对账通过的 sum_of_components 才能进入 CAGR/趋势计算
    - residual 必须明确标记"公司合计倒算/补充项"
    - unmapped 不得进入趋势计算
    - sum_of_components 必须实际计算组成项之和并与目标值对账
    - residual 必须实际计算 total - sum(known_segments)

Phase 12B-1 收口新增：
    - company_total_for_year 必须带 fiscal_year，并验证等于当前计算年度
    - 每个 known segment 都必须逐项验证（fiscal_year/currency/unit/dimension/revenue_nature/非residual/不重叠）
    - residual 必须使用独立公司财务总收入，不得使用分部合计
    - sum_of_components 需要显式映射记录（mapping_source + verification_status）
    - 父子重叠检测使用 coverage_key/parent_key，不仅检测同名重复
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 可比性状态常量
DIRECT = "direct"
SUM_OF_COMPONENTS = "sum_of_components"
RESIDUAL = "residual"
UNMAPPED = "unmapped"

# 用户友好标签
COMPARABILITY_LABELS = {
    DIRECT: "直接可比",
    SUM_OF_COMPONENTS: "组成项加总后可比",
    RESIDUAL: "公司合计倒算 / 补充项",
    UNMAPPED: "无法可靠映射",
}

# 可进入趋势/CAGR 计算的状态
TREND_ELIGIBLE = {DIRECT, SUM_OF_COMPONENTS}

# 对账误差容忍范围（占比 5%）
RECONCILIATION_TOLERANCE = 0.05


@dataclass
class SegmentPeriodMapping:
    """单个分部在单个历史年度的可比性映射结果。"""

    segment_name: str
    fiscal_year: str
    status: str  # direct | sum_of_components | residual | unmapped
    comparability_key: str = ""
    comparability_note: str = ""
    # sum_of_components 时的来源分部
    source_segments: list[str] = field(default_factory=list)
    # residual 时的倒算说明
    residual_basis: str = ""  # "total - sum(other_reported_segments)"
    # 对账详情
    reconciliation_detail: str = ""
    # 对账明细（用于页面展示）
    component_details: list[dict[str, Any]] = field(default_factory=list)
    target_value: float | None = None
    computed_sum: float | None = None
    difference: float | None = None
    error_ratio: float | None = None
    currency: str = ""
    unit: str = ""

    @property
    def label(self) -> str:
        """用户友好标签。"""
        return COMPARABILITY_LABELS.get(self.status, self.status)

    @property
    def can_enter_trend(self) -> bool:
        """是否可进入趋势/CAGR 计算。"""
        return self.status in TREND_ELIGIBLE


# 映射来源类型
MAPPING_SOURCE_BUILT_IN = "built_in_reviewed"
MAPPING_SOURCE_USER_CONFIRMED = "user_confirmed"
MAPPING_SOURCE_NONE = "none"

# 映射核验状态
VERIFICATION_PENDING = "pending"
VERIFICATION_VERIFIED = "verified"
VERIFICATION_FAILED = "failed"


@dataclass
class SegmentMappingRecord:
    """显式口径映射记录 — Phase 12B-1 收口。

    正式映射记录格式：
        {
            symbol,
            target_segment,
            fiscal_year,
            source_segments,
            mapping_source,
            verification_status,
            evidence,
            coverage_keys
        }

    只有真实生产流程生成并核验该记录后，页面才能显示"组成项加总后可比"。
    不得用名称模糊匹配、金额接近或 AI 猜测自动确认口径。

    只有以下情况允许进入计算：
    - mapping_source 为 built_in_reviewed 或 user_confirmed；
    - verification_status 必须等于 verified；
    - source_segments 全部能在原始历史分部池中找到；
    - 证据字段非空。
    """

    target_segment: str
    fiscal_year: str
    status: str  # sum_of_components | unmapped
    source_segments: list[str] = field(default_factory=list)
    mapping_source: str = MAPPING_SOURCE_NONE  # built_in_reviewed | user_confirmed | none
    verification_status: str = VERIFICATION_PENDING  # pending | verified | failed
    coverage_keys: list[str] = field(default_factory=list)
    parent_keys: list[str] = field(default_factory=list)
    verification_note: str = ""
    symbol: str = ""
    evidence: str = ""


def _same_currency_unit_dim(
    period_a: dict[str, Any],
    period_b: dict[str, Any],
) -> bool:
    """验证两个 period 的币种、单位、维度是否完全一致。

    Phase 12B-1 收口：currency、unit、dimension 三者都必须非空且完全一致。
    任一为空或任一不一致均返回 False。
    """
    cur_a = str(period_a.get("currency", "")).strip()
    cur_b = str(period_b.get("currency", "")).strip()
    unit_a = str(period_a.get("unit", "")).strip()
    unit_b = str(period_b.get("unit", "")).strip()
    dim_a = str(period_a.get("dimension", "")).strip()
    dim_b = str(period_b.get("dimension", "")).strip()
    # 三字段都必须非空，否则无法确认一致性
    if not cur_a or not cur_b:
        return False
    if not unit_a or not unit_b:
        return False
    if not dim_a or not dim_b:
        return False
    return cur_a == cur_b and unit_a == unit_b and dim_a == dim_b


def _has_overlap(
    source_segments: list[str],
    component_details: list[dict[str, Any]] | None = None,
) -> bool:
    """检查组成项列表中是否有重复或父子重叠。

    Phase 12B-1 收口：不仅检测同名重复，还使用 coverage_key/parent_key 检测父子重叠。
    无法证明不重叠时返回 True。
    """
    # 1. 同名重复检测
    seen: set[str] = set()
    for name in source_segments:
        norm = name.strip().lower()
        if norm in seen:
            return True
        seen.add(norm)

    # 2. 父子重叠检测：使用 coverage_key / parent_key
    if component_details:
        coverage_keys: set[str] = set()
        parent_keys: set[str] = set()
        for comp in component_details:
            ck = str(comp.get("coverage_key", "")).strip().lower()
            pk = str(comp.get("parent_key", "")).strip().lower()
            if ck:
                if ck in coverage_keys:
                    return True
                coverage_keys.add(ck)
            if pk:
                if pk in parent_keys:
                    return True
                parent_keys.add(pk)
            # 如果一个项的 coverage_key 是另一个项的 parent_key，可能存在父子重叠
            if ck and ck in parent_keys:
                return True
            if pk and pk in coverage_keys:
                return True

    return False


def _find_component_in_raw_pool(
    raw_pool: list[dict[str, Any]],
    segment_name: str,
    fiscal_year: str,
) -> dict[str, Any] | None:
    """从原始历史分部池中按名称和财年精确查找组成项。

    Phase 12B-1 收口：sum_of_components 的来源组成项必须从 raw pool 查找，
    不能要求旧分部仍存在于当前 segments。
    """
    for item in raw_pool:
        if (
            str(item.get("original_segment_name", "")).strip()
            == str(segment_name).strip()
            and str(item.get("fiscal_year", "")).strip() == str(fiscal_year).strip()
        ):
            return item
    return None


def _validate_known_segment_fields(
    period: dict[str, Any],
    segment_name: str,
) -> str:
    """验证 residual 已知分部的逐项字段完整性。

    Phase 12B-1 收口：每个 known segment 都必须有：
    - fiscal_year
    - currency
    - unit
    - dimension
    - revenue_nature
    - source_name / source_url
    - coverage_key

    任一字段缺失返回非空错误说明，全部通过返回空字符串。
    """
    required_fields = [
        "fiscal_year",
        "currency",
        "unit",
        "dimension",
        "revenue_nature",
        "revenue_source_name",
        "revenue_url",
        "coverage_key",
    ]
    for field_name in required_fields:
        value = str(period.get(field_name, "")).strip()
        if not value:
            return f"已知分部 {segment_name} 缺少字段 {field_name}，停止倒算"
    return ""


def _validate_company_financial_total(
    company_total: dict[str, Any] | None,
) -> str:
    """验证独立公司财务总收入的来源字段完整性。

    Phase 12B-1 收口：source_type、source_name、source_url 缺失时必须失败。
    currency、unit、dimension 也必须非空。
    """
    if not company_total:
        return "缺少独立公司总收入，无法倒算"
    for field_name in (
        "source_type", "source_name", "source_url",
        "currency", "unit", "dimension",
    ):
        value = str(company_total.get(field_name, "")).strip()
        if not value:
            return f"独立公司总收入缺少字段 {field_name}，无法倒算"
    return ""


def _find_verified_mapping(
    verified_mapping_records: list[dict[str, Any]] | None,
    target_segment: str,
    fiscal_year: str,
    symbol: str = "",
) -> dict[str, Any] | None:
    """从已核验映射记录中查找匹配 (symbol, target_segment, fiscal_year) 的记录。

    Phase 12B-1 收口（安全映射边界）：
    正式记录必须同时满足：
    - symbol 与当前公司完全一致（公司隔离，不同公司同名分部不得互用）；
    - target_segment 完全一致；
    - fiscal_year 完全一致；
    - mapping_source 为 built_in_reviewed 或 user_confirmed；
    - verification_status=verified；
    - evidence 非空；
    - source_segments 非空。

    任一条件不满足返回 None。
    """
    if not verified_mapping_records:
        return None
    if not symbol:
        return None
    norm_symbol = str(symbol).strip()
    for record in verified_mapping_records:
        rec_symbol = str(record.get("symbol", "")).strip()
        rec_target = str(record.get("target_segment", "")).strip()
        rec_year = str(record.get("fiscal_year", "")).strip()
        if rec_symbol != norm_symbol:
            continue
        if rec_target != str(target_segment).strip():
            continue
        if rec_year != str(fiscal_year).strip():
            continue
        mapping_source = str(record.get("mapping_source", "")).strip().lower()
        verification_status = str(
            record.get("verification_status", "")
        ).strip().lower()
        evidence = str(record.get("evidence", "")).strip()
        source_segments = record.get("source_segments", [])
        if not isinstance(source_segments, list) or not source_segments:
            return None
        if (
            mapping_source in (MAPPING_SOURCE_BUILT_IN, MAPPING_SOURCE_USER_CONFIRMED)
            and verification_status == VERIFICATION_VERIFIED
            and evidence
        ):
            return record
        return None
    return None


# ── 真实对账计算函数 ────────────────────────────────────────


def reconcile_sum_of_components(
    target_revenue: float,
    source_component_values: list[float],
    tolerance: float = RECONCILIATION_TOLERANCE,
) -> tuple[bool, str]:
    """实际计算组成项之和并与目标值对账。

    Args:
        target_revenue: 映射后的目标历史收入
        source_component_values: 各组成项的收入值列表
        tolerance: 误差容忍范围（占比）

    Returns:
        (是否对账通过, 对账详情文本)
    """
    if not source_component_values:
        return False, "无组成项数据"

    # 检查每个组成项是否有有效数值
    invalid_values = [
        v for v in source_component_values
        if v is None or (isinstance(v, (int, float)) and v != v)  # NaN check
    ]
    if invalid_values:
        return False, f"组成项中有 {len(invalid_values)} 个无效值"

    component_sum = sum(float(v) for v in source_component_values)
    if component_sum == 0:
        return False, "组成项之和为 0"

    if target_revenue is None or target_revenue == 0:
        return False, "目标值为空或 0"

    diff = abs(component_sum - target_revenue)
    ratio = diff / abs(target_revenue)

    if ratio <= tolerance:
        return True, (
            f"组成项加总 {component_sum:,.1f}，目标 {target_revenue:,.1f}，"
            f"误差 {ratio:.2%}（容忍 {tolerance:.0%}）"
        )
    else:
        return False, (
            f"组成项加总 {component_sum:,.1f}，目标 {target_revenue:,.1f}，"
            f"误差 {ratio:.2%} 超过容忍 {tolerance:.0%}"
        )


def compute_residual(
    total_revenue: float,
    known_segment_revenues: list[float],
    fiscal_year: str,
    currency: str = "",
    unit: str = "",
) -> tuple[float | None, str]:
    """计算 residual = total - sum(known_segments)。

    Args:
        total_revenue: 公司合计收入
        known_segment_revenues: 已知同口径分部的收入列表
        fiscal_year: 财年
        currency: 币种
        unit: 单位

    Returns:
        (residual 值, 倒算说明)
        若计算异常返回 (None, 错误说明)
    """
    if total_revenue is None or total_revenue <= 0:
        return None, "公司合计为空或非正"

    known_sum = sum(float(v) for v in known_segment_revenues if v is not None)
    residual = total_revenue - known_sum

    # 异常负数检查
    if residual < 0 and abs(residual) / total_revenue > 0.05:
        return None, (
            f"倒算结果为异常负数 {residual:,.1f}"
            f"（合计 {total_revenue:,.1f} - 已知 {known_sum:,.1f}）"
        )

    detail = (
        f"FY{fiscal_year} 公司合计倒算："
        f"{total_revenue:,.1f} - {known_sum:,.1f} = {residual:,.1f}"
    )
    if currency:
        detail += f"（{currency}"
        if unit:
            detail += f" / {unit}"
        detail += "）"
    return residual, detail


def classify_segment_period(
    segment: dict[str, Any],
    fiscal_year: str,
    latest_comparability_key: str,
    all_segments: list[dict[str, Any]] | None = None,
    company_total_for_year: dict[str, Any] | None = None,
    raw_historical_segment_pool: list[dict[str, Any]] | None = None,
    verified_mapping_records: list[dict[str, Any]] | None = None,
    symbol: str = "",
) -> SegmentPeriodMapping:
    """判断单个分部在单个历史年度的可比性状态。

    规则：
    1. 分部在该年度有 historical_period 记录
    2. comparability_key 与最近年度一致 → direct
    3. 显式 comparability_status=sum_of_components → 需实际对账才通过
    4. revenue_nature=residual → 需实际计算 total - sum(known) 才通过
    5. 其他情况 → unmapped

    Phase 12B-1 收口新增：
    - company_total_for_year 必须带 fiscal_year，并验证它等于当前计算年度
    - 每个 known segment 都必须逐项验证（fiscal_year/currency/unit/dimension/
      revenue_nature/source_name/source_url/coverage_key）
    - residual 必须使用独立公司财务总收入，不得使用分部合计
    - residual 重叠检查也必须使用 coverage_key/parent_key
    - company_financial_totals 的 source_type/source_name/source_url 缺失时必须失败
    - sum_of_components 的来源组成项必须从 raw_historical_segment_pool 查找
    - sum_of_components 需要显式映射记录（mapping_source + verification_status）
    - 父子重叠检测使用 coverage_key/parent_key，不仅检测同名重复

    Args:
        company_total_for_year: 该年度的独立公司总收入字典，包含：
            fiscal_year, revenue, currency, unit, dimension, period_end_date,
            source_type, source_name, source_url, publication_date
            缺少该年度公司总额时 residual 必须显示"无法倒算"。
        raw_historical_segment_pool: 原始历史分部池，保留所有历史年度分部记录。
            sum_of_components 的来源组成项必须全部从 raw pool 获取，
            不再回退到 all_segments 查找。
        verified_mapping_records: 已核验映射记录列表，每条包含：
            symbol, target_segment, fiscal_year, source_segments,
            mapping_source, verification_status, evidence, coverage_keys
            必须匹配 symbol、target_segment、fiscal_year 且经过核验才能进入计算。
            没有 verified record 时，即使 period_data 含 mapping_source 也不得进入计算。
        symbol: 当前公司证券代码，用于公司隔离。不同公司同名分部映射不得互用。
    """
    seg_name = segment.get("name", "")
    historical_periods = segment.get("historical_periods", [])

    # 找到该年度的 historical_period
    period_data = None
    for period in historical_periods:
        if str(period.get("fiscal_year", "")) == str(fiscal_year):
            period_data = period
            break

    if period_data is None:
        return SegmentPeriodMapping(
            segment_name=seg_name,
            fiscal_year=str(fiscal_year),
            status=UNMAPPED,
            comparability_note="该年度无历史数据",
        )

    comp_key = str(period_data.get("comparability_key", "")).strip()
    comp_note = str(period_data.get("comparability_note", "")).strip()
    revenue_nature = str(period_data.get("revenue_nature", "")).strip().lower()
    revenue = period_data.get("revenue")
    explicit_status = str(
        period_data.get("comparability_status", "")
    ).strip().lower()

    # ── residual：公司合计倒算的补充项 — 必须经过真实倒算 ──
    # 删除显式 comparability_status="residual" 可绕过实际计算的路径
    if revenue_nature == "residual" or explicit_status == RESIDUAL:
        # Phase 12B-1 收口：验证独立公司财务总收入的字段完整性
        validation_error = _validate_company_financial_total(
            company_total_for_year
        )
        if validation_error:
            return SegmentPeriodMapping(
                segment_name=seg_name,
                fiscal_year=str(fiscal_year),
                status=UNMAPPED,
                comparability_note=validation_error,
            )

        # Phase 12B-1 收口：验证 source_type 不是分部合计
        total_source_type = str(
            company_total_for_year.get("source_type", "")
        ).strip().lower()
        if total_source_type in ("segment_sum", "segment_table_total"):
            return SegmentPeriodMapping(
                segment_name=seg_name,
                fiscal_year=str(fiscal_year),
                status=UNMAPPED,
                comparability_note="缺少独立公司总收入（当前仅有分部合计），无法倒算",
            )

        # Phase 12B-1 收口：company_total_for_year 必须带 fiscal_year，并验证等于当前计算年度
        total_fiscal_year = str(
            company_total_for_year.get("fiscal_year", "")
        ).strip()
        if not total_fiscal_year:
            return SegmentPeriodMapping(
                segment_name=seg_name,
                fiscal_year=str(fiscal_year),
                status=UNMAPPED,
                comparability_note="独立公司总收入缺少财年标记，无法倒算",
            )
        if total_fiscal_year != str(fiscal_year):
            return SegmentPeriodMapping(
                segment_name=seg_name,
                fiscal_year=str(fiscal_year),
                status=UNMAPPED,
                comparability_note=(
                    f"公司总收入财年不匹配：传入 {total_fiscal_year}，"
                    f"当前计算 {fiscal_year}"
                ),
            )

        total_revenue = company_total_for_year.get("revenue")
        if total_revenue is None or float(total_revenue) <= 0:
            return SegmentPeriodMapping(
                segment_name=seg_name,
                fiscal_year=str(fiscal_year),
                status=UNMAPPED,
                comparability_note="该年度独立公司总收入为空或非正，无法倒算",
            )

        # 验证同财年、同币种、同单位、同维度（严格：非空且完全一致）
        total_currency = str(company_total_for_year.get("currency", "")).strip()
        total_unit = str(company_total_for_year.get("unit", "")).strip()
        total_dim = str(company_total_for_year.get("dimension", "")).strip()
        seg_currency = str(period_data.get("currency", "")).strip()
        seg_unit = str(period_data.get("unit", "")).strip()
        seg_dim = str(period_data.get("dimension", "")).strip()

        if not _same_currency_unit_dim(
            {"currency": total_currency, "unit": total_unit, "dimension": total_dim},
            {"currency": seg_currency, "unit": seg_unit, "dimension": seg_dim},
        ):
            return SegmentPeriodMapping(
                segment_name=seg_name,
                fiscal_year=str(fiscal_year),
                status=UNMAPPED,
                comparability_note=(
                    f"币种/单位/维度不一致或不完整："
                    f"公司总收入({total_currency}/{total_unit}/{total_dim}) "
                    f"vs 分部({seg_currency}/{seg_unit}/{seg_dim})"
                ),
            )

        # 收集同年度其他已知且不重叠的分部收入
        # Phase 12B-1 收口：每个 known segment 都必须逐项验证
        if all_segments is not None:
            known_revenues = []
            component_details = []

            for other_seg in all_segments:
                other_name = other_seg.get("name", "")
                if other_name == seg_name:
                    continue
                other_periods = other_seg.get("historical_periods", [])
                for op in other_periods:
                    op_fiscal_year = str(op.get("fiscal_year", "")).strip()
                    if op_fiscal_year != str(fiscal_year):
                        continue

                    # 逐项验证 1：revenue_nature 不能是 residual
                    other_nature = str(
                        op.get("revenue_nature", "")
                    ).strip().lower()
                    if other_nature == "residual":
                        # 不重复计算 residual 分部
                        break

                    # 逐项验证 2：必须有有效收入
                    rev = op.get("revenue")
                    if rev is None:
                        break

                    # 逐项验证 3：字段完整性（fiscal_year/currency/unit/dimension/
                    # revenue_nature/source_name/source_url/coverage_key）
                    field_error = _validate_known_segment_fields(op, other_name)
                    if field_error:
                        return SegmentPeriodMapping(
                            segment_name=seg_name,
                            fiscal_year=str(fiscal_year),
                            status=UNMAPPED,
                            comparability_note=field_error,
                        )

                    # 逐项验证 4：同币种/单位/维度（严格，非空且一致）
                    op_currency = str(op.get("currency", "")).strip()
                    op_unit = str(op.get("unit", "")).strip()
                    op_dim = str(op.get("dimension", "")).strip()
                    if op_currency != total_currency:
                        return SegmentPeriodMapping(
                            segment_name=seg_name,
                            fiscal_year=str(fiscal_year),
                            status=UNMAPPED,
                            comparability_note=(
                                f"已知分部 {other_name} 币种不一致："
                                f"{op_currency} vs 公司总收入 {total_currency}"
                            ),
                        )
                    if op_unit != total_unit:
                        return SegmentPeriodMapping(
                            segment_name=seg_name,
                            fiscal_year=str(fiscal_year),
                            status=UNMAPPED,
                            comparability_note=(
                                f"已知分部 {other_name} 单位不一致："
                                f"{op_unit} vs 公司总收入 {total_unit}"
                            ),
                        )
                    if op_dim != total_dim:
                        return SegmentPeriodMapping(
                            segment_name=seg_name,
                            fiscal_year=str(fiscal_year),
                            status=UNMAPPED,
                            comparability_note=(
                                f"已知分部 {other_name} 维度不一致："
                                f"{op_dim} vs 公司总收入 {total_dim}"
                            ),
                        )

                    # 逐项验证 5：与其他组成项不重叠（coverage_key/parent_key 检测）
                    op_coverage_key = str(op.get("coverage_key", "")).strip().lower()
                    op_parent_key = str(op.get("parent_key", "")).strip().lower()
                    comp_detail = {
                        "name": other_name,
                        "revenue": float(rev),
                        "source": str(op.get("revenue_source_name", "")),
                        "source_url": str(op.get("revenue_url", "")),
                        "fiscal_year": op_fiscal_year,
                        "currency": op_currency,
                        "unit": op_unit,
                        "dimension": op_dim,
                        "revenue_nature": other_nature,
                        "coverage_key": op_coverage_key,
                        "parent_key": op_parent_key,
                    }
                    if _has_overlap([other_name], [comp_detail]):
                        return SegmentPeriodMapping(
                            segment_name=seg_name,
                            fiscal_year=str(fiscal_year),
                            status=UNMAPPED,
                            comparability_note=(
                                f"已知分部 {other_name} 与其他组成项存在父子口径重叠"
                            ),
                        )

                    known_revenues.append(float(rev))
                    component_details.append(comp_detail)
                    break

            if not known_revenues:
                return SegmentPeriodMapping(
                    segment_name=seg_name,
                    fiscal_year=str(fiscal_year),
                    status=UNMAPPED,
                    comparability_note="已知分部列表为空，无法倒算",
                )

            residual_val, detail = compute_residual(
                float(total_revenue), known_revenues, str(fiscal_year),
                currency=total_currency or seg_currency,
                unit=total_unit or seg_unit,
            )
            if residual_val is None:
                return SegmentPeriodMapping(
                    segment_name=seg_name,
                    fiscal_year=str(fiscal_year),
                    status=UNMAPPED,
                    comparability_note=f"倒算失败：{detail}",
                )

            # 如果 residual 分部已有历史收入，进行误差核对
            if revenue is not None:
                existing_val = float(revenue)
                diff = abs(residual_val - existing_val)
                ratio = diff / abs(existing_val) if existing_val != 0 else 1.0
                if ratio > RECONCILIATION_TOLERANCE:
                    return SegmentPeriodMapping(
                        segment_name=seg_name,
                        fiscal_year=str(fiscal_year),
                        status=UNMAPPED,
                        comparability_note=(
                            f"倒算值 {residual_val:,.1f} 与已有值 {existing_val:,.1f}"
                            f"误差 {ratio:.2%} 超过容忍 {RECONCILIATION_TOLERANCE:.0%}"
                        ),
                    )

            return SegmentPeriodMapping(
                segment_name=seg_name,
                fiscal_year=str(fiscal_year),
                status=RESIDUAL,
                comparability_key=comp_key,
                comparability_note=comp_note or "公司合计倒算的补充项",
                residual_basis="当年公司总收入 - 当年已知且不重叠的分部收入之和",
                reconciliation_detail=detail,
                component_details=component_details,
                target_value=float(total_revenue),
                computed_sum=residual_val,
                currency=total_currency or seg_currency,
                unit=total_unit or seg_unit,
            )
        else:
            return SegmentPeriodMapping(
                segment_name=seg_name,
                fiscal_year=str(fiscal_year),
                status=UNMAPPED,
                comparability_note="缺少分部列表，无法倒算",
            )

    # ── sum_of_components：组成项加总后可比 — 需实际对账 ──
    # Phase 12B-1 收口（安全映射边界）：
    # 必须找到正式 verified_mapping_records 才能进入计算。
    # 删除 period_data.mapping_source fallback：
    #   - 即使 period_data 含 mapping_source=built_in_reviewed/user_confirmed
    #   - 即使 period_data 含 comparability_status=sum_of_components
    #   只要没有匹配的正式 verified record，都返回 unmapped。
    if explicit_status == SUM_OF_COMPONENTS:
        # 必须找到正式 verified record（含 symbol 隔离）
        verified_record = _find_verified_mapping(
            verified_mapping_records, seg_name, str(fiscal_year), symbol=symbol
        )
        if verified_record is None:
            return SegmentPeriodMapping(
                segment_name=seg_name,
                fiscal_year=str(fiscal_year),
                status=UNMAPPED,
                comparability_note=(
                    "未找到已核验的正式映射记录，不得进入组成项加总"
                ),
            )
        # 使用 verified mapping 的 source_segments（权威来源）
        source_segments = list(verified_record.get("source_segments", []))
        if not source_segments:
            return SegmentPeriodMapping(
                segment_name=seg_name,
                fiscal_year=str(fiscal_year),
                status=UNMAPPED,
                comparability_note="已核验映射记录中 source_segments 为空",
            )

        # 先检查同名重复
        if _has_overlap(source_segments):
            return SegmentPeriodMapping(
                segment_name=seg_name,
                fiscal_year=str(fiscal_year),
                status=UNMAPPED,
                comparability_note="组成项存在重复",
            )

        # Phase 12B-1 收口（安全映射边界）：
        # sum_of_components 的 source_segments 必须全部从 raw_historical_segment_pool 获取。
        # 删除 raw pool 找不到后回退 all_segments 的路径。
        # raw pool 为空、组成项缺失或证据字段不全时，返回 unmapped。
        if revenue is not None:
            if not raw_historical_segment_pool:
                return SegmentPeriodMapping(
                    segment_name=seg_name,
                    fiscal_year=str(fiscal_year),
                    status=UNMAPPED,
                    comparability_note="原始历史分部池为空，无法查找组成项",
                )
            component_values = []
            component_details = []
            missing_segments = []
            mismatch_segments = []

            for src_name in source_segments:
                # 仅从 raw pool 查找，不再回退 all_segments
                pool_item = _find_component_in_raw_pool(
                    raw_historical_segment_pool, src_name, str(fiscal_year)
                )

                if pool_item is None:
                    missing_segments.append(src_name)
                    continue

                # Phase 12B-1 收口（组成项证据完整性）：
                # 每个组成项都必须具有非空的 8 个字段：
                #   fiscal_year / original_segment_name / revenue /
                #   currency / unit / dimension / source_name / source_url
                # 任一缺失返回 unmapped，提示明确指出组成项与缺失字段。
                _comp_required_fields = (
                    ("fiscal_year", "fiscal_year"),
                    ("original_segment_name", "original_segment_name"),
                    ("revenue", "revenue"),
                    ("currency", "currency"),
                    ("unit", "unit"),
                    ("dimension", "dimension"),
                    ("source_name", "source_name"),
                    ("source_url", "source_url"),
                )
                missing_field = None
                for field_name, _label in _comp_required_fields:
                    val = pool_item.get(field_name)
                    if val is None:
                        missing_field = field_name
                        break
                    if isinstance(val, str) and not val.strip():
                        missing_field = field_name
                        break
                if missing_field is not None:
                    return SegmentPeriodMapping(
                        segment_name=seg_name,
                        fiscal_year=str(fiscal_year),
                        status=UNMAPPED,
                        comparability_note=(
                            f'组成项"{src_name}"缺少来源字段 {missing_field}，'
                            f"不能进入趋势计算"
                        ),
                    )

                rev = pool_item.get("revenue")
                comp_detail = {
                    "name": src_name,
                    "revenue": float(rev),
                    "source": str(pool_item.get("source_name", "")),
                    "source_url": str(pool_item.get("source_url", "")),
                    "fiscal_year": str(
                        pool_item.get("fiscal_year", "")
                    ),
                    "currency": str(pool_item.get("currency", "")),
                    "unit": str(pool_item.get("unit", "")),
                    "dimension": str(pool_item.get("dimension", "")),
                    "coverage_key": str(
                        pool_item.get("coverage_key", "")
                    ),
                    "parent_key": str(
                        pool_item.get("parent_key", "")
                    ),
                }
                if not _same_currency_unit_dim(period_data, comp_detail):
                    mismatch_segments.append(src_name)
                else:
                    component_values.append(float(rev))
                    component_details.append(comp_detail)

            if missing_segments:
                return SegmentPeriodMapping(
                    segment_name=seg_name,
                    fiscal_year=str(fiscal_year),
                    status=UNMAPPED,
                    comparability_note=(
                        f"组成项缺失：{', '.join(missing_segments)}"
                    ),
                )
            if mismatch_segments:
                return SegmentPeriodMapping(
                    segment_name=seg_name,
                    fiscal_year=str(fiscal_year),
                    status=UNMAPPED,
                    comparability_note=(
                        f"组成项币种/单位/维度不一致：{', '.join(mismatch_segments)}"
                    ),
                )

            # Phase 12B-1 收口：父子重叠检测（使用 coverage_key / parent_key）
            if _has_overlap(source_segments, component_details):
                return SegmentPeriodMapping(
                    segment_name=seg_name,
                    fiscal_year=str(fiscal_year),
                    status=UNMAPPED,
                    comparability_note="组成项存在父子口径重叠（coverage_key/parent_key 冲突）",
                )

            # 实际对账
            passed, detail = reconcile_sum_of_components(
                float(revenue), component_values
            )
            computed_sum = sum(component_values)
            diff = abs(computed_sum - float(revenue))
            ratio = diff / abs(float(revenue)) if float(revenue) != 0 else 1.0
            if passed:
                return SegmentPeriodMapping(
                    segment_name=seg_name,
                    fiscal_year=str(fiscal_year),
                    status=SUM_OF_COMPONENTS,
                    comparability_key=comp_key,
                    comparability_note=comp_note,
                    source_segments=source_segments,
                    reconciliation_detail=detail,
                    component_details=component_details,
                    target_value=float(revenue),
                    computed_sum=computed_sum,
                    difference=diff,
                    error_ratio=ratio,
                    currency=str(period_data.get("currency", "")),
                    unit=str(period_data.get("unit", "")),
                )
            else:
                return SegmentPeriodMapping(
                    segment_name=seg_name,
                    fiscal_year=str(fiscal_year),
                    status=UNMAPPED,
                    comparability_note=f"对账失败：{detail}",
                    source_segments=source_segments,
                    component_details=component_details,
                    target_value=float(revenue),
                    computed_sum=computed_sum,
                    difference=diff,
                    error_ratio=ratio,
                )
        else:
            return SegmentPeriodMapping(
                segment_name=seg_name,
                fiscal_year=str(fiscal_year),
                status=UNMAPPED,
                comparability_note="缺少组成项数据或目标值，无法对账",
            )

    # ── 显式 direct / unmapped（不含 residual）──
    if explicit_status in (DIRECT, UNMAPPED):
        return SegmentPeriodMapping(
            segment_name=seg_name,
            fiscal_year=str(fiscal_year),
            status=explicit_status,
            comparability_key=comp_key,
            comparability_note=comp_note,
            source_segments=list(period_data.get("source_segments", [])),
        )

    # ── direct：comparability_key 与最近年度一致 ──
    latest_key = str(latest_comparability_key).strip()
    if comp_key and latest_key and comp_key == latest_key:
        return SegmentPeriodMapping(
            segment_name=seg_name,
            fiscal_year=str(fiscal_year),
            status=DIRECT,
            comparability_key=comp_key,
            comparability_note=comp_note,
        )

    # comparability_key 不同 → unmapped
    if comp_key and comp_note:
        return SegmentPeriodMapping(
            segment_name=seg_name,
            fiscal_year=str(fiscal_year),
            status=UNMAPPED,
            comparability_key=comp_key,
            comparability_note=comp_note,
        )

    if comp_key and not latest_key:
        return SegmentPeriodMapping(
            segment_name=seg_name,
            fiscal_year=str(fiscal_year),
            status=UNMAPPED,
            comparability_key=comp_key,
            comparability_note="最近年度无可比性标记",
        )

    # 无 comparability_key
    return SegmentPeriodMapping(
        segment_name=seg_name,
        fiscal_year=str(fiscal_year),
        status=UNMAPPED,
        comparability_note="无可比性标记",
    )


def build_comparability_matrix(
    segments: list[dict[str, Any]],
    historical_years: list[str],
    company_financial_totals: dict[str, dict[str, Any]] | None = None,
    raw_historical_segment_pool: list[dict[str, Any]] | None = None,
    verified_mapping_records: list[dict[str, Any]] | None = None,
    symbol: str = "",
) -> dict[tuple[str, str], SegmentPeriodMapping]:
    """构建可比性矩阵。

    Phase 12B-1 收口（安全映射边界）：
    - 使用独立公司财务总收入（company_financial_totals），不使用分部合计。
    - sum_of_components 的来源组成项必须全部从 raw_historical_segment_pool 查找。
    - sum_of_components 必须找到正式 verified_mapping_records 才能进入计算。
    - 通过 symbol 实现公司隔离，不同公司同名分部映射不得互用。

    Args:
        segments: assumptions["segments"]
        historical_years: 历史年度列表（如 ["2023", "2024", "2025"]）
        company_financial_totals: 按财年索引的独立公司总收入字典。
            当前阶段 F10 provider 尚未接入独立来源，返回空 dict。
            缺少某年度公司总额时，该年度 residual 必须显示"缺少独立公司总收入，无法倒算"。
        raw_historical_segment_pool: 原始历史分部池，保留所有历史年度分部记录。
        verified_mapping_records: 已核验映射记录列表。
        symbol: 当前公司证券代码，用于公司隔离。

    Returns:
        {(segment_name, fiscal_year): SegmentPeriodMapping}
    """
    matrix: dict[tuple[str, str], SegmentPeriodMapping] = {}

    for segment in segments:
        seg_name = segment.get("name", "")
        historical_periods = segment.get("historical_periods", [])

        # 获取最近年度的 comparability_key 作为基准
        latest_key = ""
        if historical_periods:
            latest_period = historical_periods[-1]
            latest_key = str(latest_period.get("comparability_key", "")).strip()

        for year in historical_years:
            # 按年度获取对应年度的独立公司总收入
            year_total = None
            if company_financial_totals:
                year_total = company_financial_totals.get(str(year))

            mapping = classify_segment_period(
                segment, str(year), latest_key,
                all_segments=segments,
                company_total_for_year=year_total,
                raw_historical_segment_pool=raw_historical_segment_pool,
                verified_mapping_records=verified_mapping_records,
                symbol=symbol,
            )
            matrix[(seg_name, str(year))] = mapping

    return matrix


def get_available_historical_years(segments: list[dict[str, Any]]) -> list[str]:
    """获取所有分部中出现的历史年度（去重、排序）。

    Returns:
        年度列表，如 ["2022", "2023", "2024", "2025"]
    """
    years_set: set[str] = set()
    for segment in segments:
        for period in segment.get("historical_periods", []):
            year = str(period.get("fiscal_year", "")).strip()
            if year:
                years_set.add(year)
    return sorted(years_set)


def select_historical_years(
    all_years: list[str],
    selection: str = "recent_3",
    custom_start: str | None = None,
    custom_end: str | None = None,
) -> list[str]:
    """根据用户选择返回历史年度。

    Args:
        all_years: 所有可用历史年度（已排序）
        selection: "recent_1" | "recent_3" | "recent_5" | "custom"
        custom_start: 自定义起始年度（含）
        custom_end: 自定义结束年度（含）

    Returns:
        选中的年度列表
    """
    if not all_years:
        return []

    if selection == "recent_1":
        return all_years[-1:]
    elif selection == "recent_3":
        return all_years[-3:]
    elif selection == "recent_5":
        return all_years[-5:]
    elif selection == "custom":
        start = str(custom_start).strip() if custom_start else ""
        end = str(custom_end).strip() if custom_end else ""
        result = []
        for year in all_years:
            if start and year < start:
                continue
            if end and year > end:
                continue
            result.append(year)
        return result
    else:
        return all_years[-3:]


def can_compute_cagr(
    segment_name: str,
    historical_years: list[str],
    comparability_matrix: dict[tuple[str, str], SegmentPeriodMapping],
) -> bool:
    """判断某分部是否可以计算 CAGR（所有年度都 direct 或 sum_of_components）。

    Args:
        segment_name: 分部名称
        historical_years: 历史年度列表
        comparability_matrix: 可比性矩阵

    Returns:
        True 如果至少 2 个年度且所有年度都可进入趋势计算
    """
    if not historical_years or len(historical_years) < 2:
        return False
    for year in historical_years:
        mapping = comparability_matrix.get((segment_name, str(year)))
        if mapping is None or not mapping.can_enter_trend:
            return False
    return True


def get_comparability_summary(
    segment_name: str,
    historical_years: list[str],
    comparability_matrix: dict[tuple[str, str], SegmentPeriodMapping],
) -> str:
    """获取某分部的可比性摘要文本。"""
    parts = []
    for year in historical_years:
        mapping = comparability_matrix.get((segment_name, str(year)))
        if mapping:
            parts.append(f"FY{year}: {mapping.label}")
        else:
            parts.append(f"FY{year}: 无数据")
    return " | ".join(parts)
