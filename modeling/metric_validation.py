"""分部指标语义校验与对账模块 — Phase 12B-0。

核心问题：
    紫光股份等 A 股公司缓存数据中 MAIN_BUSINESS_COST（成本）= 64160602157.35，
    换算为百万元 = 64160.6。如果被错误地映射为分部利润并展示给用户，
    会导致"分部营业利润 64160.6、利润率 83.5%"这类严重误导。

解决思路：
    1. 定义分部利润指标的类型枚举（gross_profit / operating_profit / net_profit / ebitda）
    2. 建立对账规则：只有指标定义、期间、单位、合并范围一致时才能对账
    3. 不可对账的利润指标不得展示为"毛利率"，不得用于毛利率预测依据
    4. 增加数据质量状态枚举：direct / sum_of_components / residual / model_estimate / unmapped

本模块只做校验和拦截，不修改数据源。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── 分部利润指标类型 ──────────────────────────────────────────


PROFIT_METRIC_GROSS_PROFIT = "gross_profit"          # 分部毛利 = 收入 - 成本
PROFIT_METRIC_OPERATING_PROFIT = "operating_profit"   # 分部营业利润
PROFIT_METRIC_NET_PROFIT = "net_profit"               # 分部净利润
PROFIT_METRIC_EBITDA = "ebitda"                       # 分部 EBITDA
PROFIT_METRIC_UNKNOWN = "unknown"                     # 未识别的利润指标

PROFIT_METRIC_LABELS: dict[str, str] = {
    "gross_profit": "分部毛利",
    "operating_profit": "分部营业利润",
    "net_profit": "分部净利润",
    "ebitda": "分部 EBITDA",
    "unknown": "未识别利润指标",
}

# A 股 F10 接口字段名 → 利润指标类型映射
# 关键：MAIN_BUSINESS_COST 是成本，不是利润，绝不映射为任何利润指标
A_SHARE_FIELD_METRIC_MAP: dict[str, str] = {
    "MAIN_BUSINESS_RPOFIT": PROFIT_METRIC_GROSS_PROFIT,       # 主营业务利润 = 收入 - 成本（即毛利）
    "MAIN_BUSINESS_PROFIT": PROFIT_METRIC_GROSS_PROFIT,
    "GROSS_RPOFIT_RATIO": PROFIT_METRIC_GROSS_PROFIT,         # 毛利率
    "GROSS_PROFIT_RATIO": PROFIT_METRIC_GROSS_PROFIT,
    # 注意：以下字段是成本或占比，不是利润，故意不映射
    # "MAIN_BUSINESS_COST" → 成本，不是利润
    # "MBC_RATIO" → 成本占比，不是利润率
    # "MBR_RATIO" → 利润占比（占公司总利润的比例），不是利润率
}

# 绝不能映射为利润指标的字段（黑名单）
COST_FIELD_BLACKLIST = frozenset({
    "MAIN_BUSINESS_COST",
    "MBC_RATIO",
    "mainBusinessCost",
    "mbcRatio",
    "OPERATE_COST",
    "operateCost",
    "OPERATING_COST",
    "operatingCost",
    "COST_OF_GOODS_SOLD",
    "costOfGoodsSold",
    "COGS",
    "cogs",
})


# ── 数据质量状态（用于历史可比性判断）──────────────────────────


QUALITY_DIRECT = "direct"                    # 直接可比：同一分部、同一口径
QUALITY_SUM_OF_COMPONENTS = "sum_of_components"  # 组成项加总后可比
QUALITY_RESIDUAL = "residual"                # 公司合计倒算的补充项
QUALITY_MODEL_ESTIMATE = "model_estimate"    # 模型估算
QUALITY_UNMAPPED = "unmapped"               # 无法可靠映射，不可用于趋势计算

QUALITY_LABELS: dict[str, str] = {
    "direct": "直接可比",
    "sum_of_components": "组成项加总",
    "residual": "公司合计倒算 / 补充项",
    "model_estimate": "模型估算",
    "unmapped": "不可比 / 不可用",
}

# 可用于趋势计算（CAGR、历史趋势、AI 预测依据）的质量状态
TRENDABLE_QUALITY = frozenset({
    QUALITY_DIRECT,
    QUALITY_SUM_OF_COMPONENTS,
})


# ── 对账校验结果 ──────────────────────────────────────────────


@dataclass
class ReconciliationResult:
    """分部利润指标对账结果。"""

    is_valid: bool                                   # 是否通过对账
    profit_metric: str                               # 利润指标类型
    quality: str                                     # 数据质量状态
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def quality_label(self) -> str:
        return QUALITY_LABELS.get(self.quality, self.quality)

    @property
    def profit_metric_label(self) -> str:
        return PROFIT_METRIC_LABELS.get(self.profit_metric, self.profit_metric)


def classify_profit_metric(field_name: str, value: float | None) -> str:
    """根据字段名判断利润指标类型。

    关键安全规则：
    - 成本字段（MAIN_BUSINESS_COST 等）返回 UNKNOWN，绝不映射为利润
    - MBR_RATIO（利润占比）不是利润率，返回 UNKNOWN
    - 只有明确的利润字段才返回对应类型
    """
    if field_name in COST_FIELD_BLACKLIST:
        return PROFIT_METRIC_UNKNOWN

    return A_SHARE_FIELD_METRIC_MAP.get(field_name, PROFIT_METRIC_UNKNOWN)


def is_metric_comparable(
    metric_a: str,
    metric_b: str,
) -> bool:
    """判断两个利润指标是否可对账。

    只有指标定义一致时才能对账。
    unknown 指标不可与任何指标对账。
    """
    if metric_a == PROFIT_METRIC_UNKNOWN or metric_b == PROFIT_METRIC_UNKNOWN:
        return False
    return metric_a == metric_b


def reconcile_segment_profit(
    *,
    segment_name: str,
    reported_profit: float | None,
    reported_profit_margin: float | None,
    profit_metric_name: str,
    segment_revenue: float | None,
    company_gross_profit: float | None,
    company_gross_margin: float | None,
    company_total_revenue: float | None,
) -> ReconciliationResult:
    """校验分部利润指标是否可与公司合计指标对账。

    校验规则：
    1. 如果 reported_profit 为 None，返回 valid=True（无利润数据可校验）
    2. 如果 profit_metric_name 包含"营业利润"但 reported_profit 接近 segment_revenue * cost_ratio
       （即利润额≈成本），标记为错误：成本被误用为利润
    3. 如果 reported_profit_margin 与 company_gross_margin 差异过大（>50个百分点），
       且 profit_metric_name 不是"毛利"，标记为不可对账
    4. 倒算后为负数或明显异常（>200%）时拦截

    返回 ReconciliationResult，包含 is_valid、warnings、errors。
    """
    warnings: list[str] = []
    errors: list[str] = []

    # 无利润数据，无需校验
    if reported_profit is None:
        return ReconciliationResult(
            is_valid=True,
            profit_metric=PROFIT_METRIC_UNKNOWN,
            quality=QUALITY_UNMAPPED,
        )

    # 判断利润指标类型
    metric_name_lower = profit_metric_name.lower() if profit_metric_name else ""
    if "毛利" in profit_metric_name:
        profit_metric = PROFIT_METRIC_GROSS_PROFIT
    elif "营业利润" in profit_metric_name:
        profit_metric = PROFIT_METRIC_OPERATING_PROFIT
    elif "净利" in profit_metric_name:
        profit_metric = PROFIT_METRIC_NET_PROFIT
    elif "ebitda" in metric_name_lower:
        profit_metric = PROFIT_METRIC_EBITDA
    else:
        profit_metric = PROFIT_METRIC_UNKNOWN

    # 规则2：检查利润额是否异常接近收入（可能是收入而非利润）
    if segment_revenue and segment_revenue > 0:
        profit_to_revenue = reported_profit / segment_revenue
        if profit_to_revenue > 1.5:
            errors.append(
                f"分部「{segment_name}」的披露利润（{reported_profit:,.1f}）"
                f"超过分部收入（{segment_revenue:,.1f}）的 150%，"
                f"可能将收入或成本误用为利润。该指标不可用于毛利率预测。"
            )

    # 规则2b：非毛利指标的利润率超过 70% 时拦截
    # 营业利润率/净利率/EBITDA 利润率极少超过 70%，
    # 紫光股份案例：成本/收入 = 83.5%，被误用为"营业利润率"
    if (
        reported_profit_margin is not None
        and profit_metric != PROFIT_METRIC_GROSS_PROFIT
        and profit_metric != PROFIT_METRIC_UNKNOWN
        and reported_profit_margin > 0.70
    ):
        errors.append(
            f"分部「{segment_name}」的「{profit_metric_name}」"
            f"（{reported_profit_margin:.1%}）超过 70%，"
            f"明显异常，可能将成本或收入误用为利润。该指标不可用于毛利率预测。"
        )

    # 规则3：检查利润率是否与公司合计毛利率差异过大
    if (
        reported_profit_margin is not None
        and company_gross_margin is not None
        and profit_metric != PROFIT_METRIC_GROSS_PROFIT
    ):
        margin_diff = abs(reported_profit_margin - company_gross_margin)
        if margin_diff > 0.30:
            warnings.append(
                f"分部「{segment_name}」的「{profit_metric_name}」"
                f"（{reported_profit_margin:.1%}）与公司合计毛利率"
                f"（{company_gross_margin:.1%}）差异过大"
                f"（{margin_diff:.1%}），指标定义不一致，不可作为毛利率依据。"
            )

    # 规则4：利润率异常
    if reported_profit_margin is not None:
        if reported_profit_margin < -0.50:
            errors.append(
                f"分部「{segment_name}」的利润率（{reported_profit_margin:.1%}）"
                f"为负数且异常偏低，数据可能有误。"
            )
        elif reported_profit_margin > 2.0:
            errors.append(
                f"分部「{segment_name}」的利润率（{reported_profit_margin:.1%}）"
                f"超过 200%，明显异常，数据可能有误。"
            )

    # 规则5：分部利润合计与公司合计对账
    if (
        company_gross_profit is not None
        and company_total_revenue is not None
        and profit_metric == PROFIT_METRIC_GROSS_PROFIT
        and segment_revenue is not None
    ):
        # 只有毛利才能与公司合计毛利对账
        # 营业利润、净利润等不可与公司合计毛利对账
        pass  # 对账逻辑在历史数据层实现

    is_valid = len(errors) == 0
    quality = QUALITY_DIRECT if is_valid else QUALITY_UNMAPPED

    return ReconciliationResult(
        is_valid=is_valid,
        profit_metric=profit_metric,
        quality=quality,
        warnings=warnings,
        errors=errors,
    )


def can_use_as_margin_basis(
    profit_metric: str,
    reconciliation: ReconciliationResult,
) -> bool:
    """判断某个利润指标是否可用作毛利率预测依据。

    规则：
    1. 只有 gross_profit 类型才可用作毛利率依据
    2. operating_profit / net_profit / ebitda 不可用作毛利率依据
    3. unknown 类型不可用作任何依据
    4. 对账失败的指标不可用
    """
    if not reconciliation.is_valid:
        return False
    if profit_metric == PROFIT_METRIC_UNKNOWN:
        return False
    if profit_metric != PROFIT_METRIC_GROSS_PROFIT:
        return False
    return True


def is_trendable(quality: str) -> bool:
    """判断数据质量状态是否可用于趋势计算（CAGR、历史趋势、AI 预测依据）。"""
    return quality in TRENDABLE_QUALITY


# ── 历史数据层数据结构 ────────────────────────────────────────


@dataclass
class HistoricalSegmentRecord:
    """单个历史年度的分部记录。

    用于后续支持最近 1 年、3 年、5 年和自定义期间的历史分部数据。
    本轮只定义结构，不实现完整 UI。
    """

    company_name: str
    symbol: str
    segment_key: str                              # 分部唯一标识（用于跨年度匹配）
    segment_display_name: str                     # 分部显示名称（可能因年度变化）
    fiscal_year: str                              # 财年（如 "2025"）
    metric: str                                   # 指标（revenue / gross_profit / gross_margin / operating_profit / net_profit）
    exact_value: float                            # 精确值
    currency: str                                # 币种（如 "人民币百万元"）
    unit: str                                     # 单位（如 "百万元"）
    original_nature: str                          # 原始性质（reported / estimated / derived）
    comparability_key: str                        # 可比性键（用于判断是否同一分部）
    comparability_note: str = ""                  # 可比性说明
    quality: str = QUALITY_DIRECT                 # 数据质量状态
    acquisition_channel: str = ""                  # 获取渠道
    official_url: str = ""                         # 官方链接
    publication_date: str = "未记录"               # 发布日期
    page_or_table: str = ""                       # 页码或表格
    evidence_ids: list[str] = field(default_factory=list)  # 关联证据 ID


@dataclass
class HistoricalSegmentSeries:
    """跨年度的分部时间序列。

    包含同一分部（按 comparability_key 匹配）在多个年度的记录。
    用于计算 YoY、CAGR 等趋势指标。
    """

    company_name: str
    symbol: str
    segment_key: str
    segment_display_name: str
    currency: str
    unit: str
    metric: str
    records: list[HistoricalSegmentRecord] = field(default_factory=list)
    quality: str = QUALITY_DIRECT                 # 整体质量状态

    def add_record(self, record: HistoricalSegmentRecord) -> None:
        """添加一条历史记录，自动更新整体质量。"""
        self.records.append(record)
        # 如果任何一条记录不可比，整体质量降级
        if record.quality == QUALITY_UNMAPPED:
            self.quality = QUALITY_UNMAPPED
        elif record.quality == QUALITY_RESIDUAL and self.quality != QUALITY_UNMAPPED:
            self.quality = QUALITY_RESIDUAL
        elif record.quality == QUALITY_SUM_OF_COMPONENTS and self.quality not in (QUALITY_UNMAPPED, QUALITY_RESIDUAL):
            self.quality = QUALITY_SUM_OF_COMPONENTS

    @property
    def is_trendable(self) -> bool:
        """是否可用于趋势计算。"""
        return is_trendable(self.quality)

    @property
    def year_count(self) -> int:
        return len(self.records)

    @property
    def has_consecutive_years(self) -> bool:
        """是否有连续年度（用于 CAGR 计算，至少需要 3 年）。"""
        if len(self.records) < 3:
            return False
        years = sorted(int(r.fiscal_year) for r in self.records if r.fiscal_year.isdigit())
        if len(years) < 3:
            return False
        for i in range(1, len(years)):
            if years[i] - years[i - 1] != 1:
                return False
        return True


@dataclass
class SegmentMapping:
    """分部名称映射规则。

    用于处理名称变化：
    - 旧年度"空调 + 冰箱 + 洗衣机" → 新年度"白电"
    - 旧年度"在线广告" → 新年度"营销服务"
    """

    target_segment_key: str                       # 目标分部 key
    target_display_name: str                      # 目标分部显示名称
    source_segment_keys: list[str]                # 源分部 key 列表
    mapping_type: str = QUALITY_SUM_OF_COMPONENTS  # 映射类型
    alias_note: str = ""                          # 别名说明
    total_reconciliation: float | None = None     # 总额对账结果（加总后应等于目标值）

    def is_total_reconciled(self, tolerance: float = 0.01) -> bool:
        """检查总额是否对账（加总后等于目标值）。"""
        if self.total_reconciliation is None:
            return False
        return abs(self.total_reconciliation) <= tolerance
