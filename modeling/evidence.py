"""预测证据与公式框架模块 — 为每个 Base 假设提供可解释的证据层。

数据结构：
- MetricEvidence: 原子化指标证据（每条记录只代表一个指标）
- OperatingMetric: 可选经营指标（销量、价格、产能、利用率等）
- ForwardEvidence: 前瞻证据（公司目标、产能变化、新品发布等）
- ForecastFormula: 预测公式框架（5 种收入公式 + 毛利率解释）

核心原则：
- 基期收入与基期毛利率分别标注来源，不混用；
- 历史增长率只有在存在至少两期真实可比收入时才能计算；
- 当前只有一个基期时，历史增长率为 None；
- Base 预测增长率只出现在"当前 Base 假设"表，不出现在历史事实；
- fiscal_period 与 publication_date 分离；
- 缺失数据明确标记为缺失或占位，不能补造数字；
- 本阶段所有分部 has_real_evidence 必须为 False（尚未计算 driver）。
"""

from __future__ import annotations

import math
import re
from typing import Any


# ── 证据来源类型（source_type）─────────────────────────────────


SOURCE_DISCLOSURE = "disclosure"           # 公司官方披露
SOURCE_SNAPSHOT = "snapshot"                # 内置官方快照
SOURCE_DERIVED = "derived"                  # 按公司合计反推
SOURCE_ESTIMATED = "estimated"              # 模型估算
SOURCE_USER_DEFINED = "user_defined"        # 用户定义
SOURCE_MISSING = "missing"                   # 缺失
SOURCE_PLACEHOLDER = "placeholder"          # 占位

SOURCE_LABELS: dict[str, str] = {
    "disclosure": "公司披露",
    "snapshot": "内置官方快照",
    "derived": "按公司合计反推",
    "estimated": "模型估算",
    "user_defined": "用户定义",
    "missing": "缺失",
    "placeholder": "占位",
}


# ── 资料性质（nature）─────────────────────────────────────────
# nature 与 source_type 对应，但语义不同：
# source_type 描述数据从哪里来；nature 描述数据是否为实际/快照/反推/估算/用户/缺失。


NATURE_REPORTED = "reported"                # 实际披露数据
NATURE_SNAPSHOT = "snapshot"                # 内置官方快照
NATURE_DERIVED = "derived"                  # 按公司合计反推
NATURE_ESTIMATED = "estimated"              # 模型估算
NATURE_USER_DEFINED = "user_defined"        # 用户定义
NATURE_MISSING = "missing"                   # 缺失

NATURE_LABELS: dict[str, str] = {
    "reported": "公司披露",
    "snapshot": "内置官方快照",
    "derived": "按公司合计反推",
    "estimated": "模型估算",
    "user_defined": "用户定义",
    "missing": "缺失",
}


def _nature_from_basis(basis: str, source_category: str = "") -> str:
    """根据 segment.basis / gross_margin_basis 推断 nature。

    basis 取值：reported / derived / estimated / user_defined
    source_category 含"内置官方快照"时，reported 降级为 snapshot。
    """
    if basis == "reported":
        if "内置官方快照" in source_category or source_category == "内置官方快照":
            return NATURE_SNAPSHOT
        return NATURE_REPORTED
    if basis == "derived":
        return NATURE_DERIVED
    if basis == "estimated":
        return NATURE_ESTIMATED
    if basis == "user_defined":
        return NATURE_USER_DEFINED
    return NATURE_ESTIMATED


def _source_type_from_nature(nature: str) -> str:
    """nature → source_type 映射。"""
    mapping = {
        "reported": SOURCE_DISCLOSURE,
        "snapshot": SOURCE_SNAPSHOT,
        "derived": SOURCE_DERIVED,
        "estimated": SOURCE_ESTIMATED,
        "user_defined": SOURCE_USER_DEFINED,
        "missing": SOURCE_MISSING,
    }
    return mapping.get(nature, SOURCE_ESTIMATED)


def _confidence_from_nature(nature: str) -> str:
    """根据 nature 推断置信度。"""
    if nature in (NATURE_REPORTED, NATURE_SNAPSHOT):
        return "high"
    if nature in (NATURE_DERIVED, NATURE_USER_DEFINED):
        return "medium"
    return "low"


# ── 资料性质（material_type）──────────────────────────────────


MATERIAL_ACTUAL = "actual"                  # 实际数据
MATERIAL_TARGET = "target"                  # 公司目标/指引
MATERIAL_INDUSTRY = "industry"             # 行业数据
MATERIAL_ESTIMATE = "estimate"              # 估算值

MATERIAL_LABELS: dict[str, str] = {
    "actual": "实际数据",
    "target": "公司目标/指引",
    "industry": "行业数据",
    "estimate": "估算值",
}


# ── 预测公式类型 ─────────────────────────────────────────────


FORMULA_VOLUME_PRICE = "volume_price"
FORMULA_MARKET_SIZE_SHARE = "market_size_share"
FORMULA_STORE_COUNT_REVENUE = "store_count_revenue"
FORMULA_CUSTOMER_ARPU = "customer_arpu"
FORMULA_SUBSCRIPTION_ARPU = "subscription_arpu"
FORMULA_DIRECT_GROWTH = "direct_growth"

FORMULA_LABELS: dict[str, str] = {
    "volume_price": "收入 = 销量 × 单价",
    "market_size_share": "收入 = 行业空间 × 渗透率 × 市场份额",
    "store_count_revenue": "收入 = 门店数量 × 单店收入",
    "customer_arpu": "收入 = 客户数 × ARPU",
    "subscription_arpu": "收入 = 订阅客户数 × ARPU",
    "direct_growth": "收入 = 基期收入 × (1 + 增长率)",
}

# 公式所需的关键驱动因子（直接路径）
FORMULA_REQUIRED_DRIVERS: dict[str, list[str]] = {
    "volume_price": ["销量", "单价"],
    "market_size_share": ["行业空间", "渗透率", "市场份额"],
    "store_count_revenue": ["门店数量", "单店收入"],
    "customer_arpu": ["客户数", "ARPU"],
    "subscription_arpu": ["订阅客户数", "ARPU"],
    "direct_growth": ["增长率"],
}

# 量价公式的细化路径（替代路径）
FORMULA_VOLUME_PRICE_DETAILED_DRIVERS: list[str] = [
    "产能", "利用率", "产销率", "单价",
]

# 公式描述（用于逻辑卡展示）
FORMULA_DESCRIPTIONS: dict[str, str] = {
    "volume_price": (
        "收入由产品销量和平均售价决定。"
        "支持两种路径：直接路径（销量 × 单价）和细化路径"
        "（产能 × 利用率 × 产销率 × 单价）。"
        "适用于制造业、白酒、能源等实物产出型业务。"
    ),
    "market_size_share": (
        "收入由行业总市场空间、产品渗透率和公司市场份额决定。"
        "适用于成长期行业、新渗透产品。"
    ),
    "store_count_revenue": (
        "收入由门店数量和单店收入决定。"
        "适用于零售、餐饮、连锁线下业务。"
    ),
    "customer_arpu": (
        "收入由活跃用户数和 ARPU（每用户平均收入）决定。"
        "适用于互联网平台、游戏、社交业务。"
    ),
    "subscription_arpu": (
        "收入由订阅客户数和 ARPU 决定。"
        "适用于 SaaS、会员订阅、软件服务。"
    ),
    "direct_growth": (
        "收入 = 基期收入 × (1 + 增长率)。"
        "未选择特定 driver 公式时的兜底逻辑。"
    ),
}


# ── 毛利率解释因子 ───────────────────────────────────────────


MARGIN_FACTOR_PRODUCT_MIX = "product_mix"
MARGIN_FACTOR_SCALE_EFFECT = "scale_effect"
MARGIN_FACTOR_COST_PASS_THROUGH = "cost_pass_through"
MARGIN_FACTOR_SUPPLY_DEMAND = "supply_demand"
MARGIN_FACTOR_LIFECYCLE = "lifecycle"

MARGIN_FACTOR_LABELS: dict[str, str] = {
    "product_mix": "产品结构",
    "scale_effect": "规模效应",
    "cost_pass_through": "成本传导",
    "supply_demand": "行业供需",
    "lifecycle": "生命周期",
}

MARGIN_FACTOR_DESCRIPTIONS: dict[str, str] = {
    "product_mix": "高毛利产品占比变化影响综合毛利率",
    "scale_effect": "规模扩大摊薄固定成本，提升毛利率",
    "cost_pass_through": "原材料/大宗商品价格变动传导至售价",
    "supply_demand": "行业供需格局影响定价权和毛利率",
    "lifecycle": "产品生命周期阶段（导入/成长/成熟/衰退）影响毛利率",
}


# ── 原子化指标证据（MetricEvidence）────────────────────────────
# 每条记录只代表一个指标，确保收入、毛利率、增长率不共用来源。


METRIC_REVENUE = "revenue"
METRIC_GROSS_MARGIN = "gross_margin"
METRIC_GROWTH_RATE = "growth_rate"

METRIC_LABELS: dict[str, str] = {
    "revenue": "基期收入",
    "gross_margin": "基期毛利率",
    "growth_rate": "历史增长率",
}


def make_metric_evidence(
    metric: str,
    value: float | None,
    unit: str,
    fiscal_period: str,
    publication_date: str,
    source_type: str,
    source_name: str,
    source_url: str,
    nature: str,
    confidence: str,
    is_placeholder: bool = False,
    is_historical: bool = True,
) -> dict[str, Any]:
    """创建一条原子化指标证据记录。

    每条记录只代表一个指标（收入/毛利率/增长率）。
    缺失数据用 nature=missing 标记，value=None，不补造数字。

    Args:
        metric: 指标类型（revenue / gross_margin / growth_rate）
        value: 指标数值（缺失时为 None）
        unit: 单位（如"百万元"/"百分比"）
        fiscal_period: 资料所属期间（如"2025财年"/"FY2025"）
        publication_date: 公告实际发布日期（如"2026-03-18"），未知为"未记录"
        source_type: 来源类型（disclosure/snapshot/derived/estimated/user_defined/missing）
        source_name: 来源名称（如"香港交易所披露易"）
        source_url: 来源 URL
        nature: 资料性质（reported/snapshot/derived/estimated/user_defined/missing）
        confidence: 置信度（high/medium/low）
        is_placeholder: 是否为占位假设
        is_historical: 是否为历史事实（True=基期事实，False=预测假设）
    """
    is_missing = nature == NATURE_MISSING or value is None
    return {
        "metric": metric,
        "metric_label": METRIC_LABELS.get(metric, metric),
        "value": value,
        "unit": unit,
        "fiscal_period": fiscal_period,
        "publication_date": publication_date,
        "source_type": source_type,
        "source_name": source_name,
        "source_url": source_url,
        "nature": nature,
        "nature_label": NATURE_LABELS.get(nature, nature),
        "source_label": SOURCE_LABELS.get(source_type, source_type),
        "confidence": confidence,
        "is_missing": is_missing,
        "is_placeholder": is_placeholder,
        "is_historical": is_historical,
    }


# ── 经营指标 ─────────────────────────────────────────────────


# 经营指标名称枚举
OPERATING_METRIC_NAMES = [
    "销量", "单价", "产能", "利用率", "产销率",
    "市场空间", "渗透率", "市场份额",
    "门店数", "单店收入",
    "客户数", "ARPU", "订阅客户数",
]


def make_operating_metric(
    name: str,
    segment_name: str,
    year: int,
    value: float | None = None,
    unit: str = "",
    source: str = SOURCE_MISSING,
    source_label: str = "",
    material_date: str = "",
    material_type: str = MATERIAL_ESTIMATE,
    confidence: str = "low",
    applicable_year: int | None = None,
) -> dict[str, Any]:
    """创建一条经营指标记录。

    缺失数据用 source=missing 标记，value=None，不补造数字。
    """
    return {
        "name": name,
        "segment_name": segment_name,
        "year": year,
        "value": value,
        "unit": unit,
        "source": source,
        "source_label": source_label or SOURCE_LABELS.get(source, source),
        "material_date": material_date,
        "material_type": material_type,
        "confidence": confidence,
        "applicable_year": applicable_year or year,
        "is_missing": source == SOURCE_MISSING or value is None,
    }


# ── 前瞻证据 ─────────────────────────────────────────────────


FORWARD_EVIDENCE_TYPES = [
    "公司目标", "产能变化", "新品发布", "开店计划",
    "行业供需", "管理层指引", "其他",
]

FORWARD_EVIDENCE_LABELS: dict[str, str] = {
    "company_target": "公司目标",
    "capacity_change": "产能变化",
    "new_product": "新品发布",
    "store_plan": "开店计划",
    "industry_supply_demand": "行业供需",
    "management_guidance": "管理层指引",
    "other": "其他",
}


def make_forward_evidence(
    evidence_type: str,
    segment_name: str,
    description: str,
    applicable_year: int,
    source: str = SOURCE_MISSING,
    source_label: str = "",
    material_date: str = "",
    material_type: str = MATERIAL_TARGET,
    confidence: str = "low",
) -> dict[str, Any]:
    """创建一条前瞻证据记录。

    缺失数据用 source=missing 标记，不补造数字。
    """
    return {
        "evidence_type": evidence_type,
        "evidence_type_label": FORWARD_EVIDENCE_LABELS.get(evidence_type, evidence_type),
        "segment_name": segment_name,
        "description": description,
        "applicable_year": applicable_year,
        "source": source,
        "source_label": source_label or SOURCE_LABELS.get(source, source),
        "material_date": material_date,
        "material_type": material_type,
        "confidence": confidence,
        "is_missing": source == SOURCE_MISSING,
    }


# ── 发布日期提取 ─────────────────────────────────────────────


def _extract_publication_date(sources: list[dict[str, Any]]) -> str:
    """从 sources 列表中提取发布日期。

    查找 URL 中包含日期模式（YYYY-MM-DD 或 YYYY/MM/DD）的记录。
    返回第一个匹配的日期，未找到返回"未记录"。
    """
    date_patterns = [
        r"(\d{4})[-/](\d{2})[-/](\d{2})",
    ]
    for src in sources:
        url = src.get("url", "") or ""
        title = src.get("title", "") or ""
        for pattern in date_patterns:
            for text in (url, title):
                match = re.search(pattern, text)
                if match:
                    y, m, d = match.groups()
                    return f"{y}-{m}-{d}"
    return "未记录"


def _format_fiscal_period(fiscal_year: Any) -> str:
    """格式化财年期间。"""
    fy = str(fiscal_year).strip()
    if fy:
        return f"{fy}财年"
    return "未记录"


def _select_source_for_metric(
    sources: list[dict[str, Any]],
    nature: str,
) -> tuple[str, str]:
    """为指标选择最合适的来源名称和 URL。

    Args:
        sources: assumptions['sources'] 列表
        nature: 资料性质

    Returns:
        (source_name, source_url)
    """
    if not sources:
        return "未记录", ""

    # 优先选择包含具体日期的来源（非首页）
    for src in sources:
        url = src.get("url", "") or ""
        title = src.get("title", "") or ""
        if re.search(r"\d{4}[-/]\d{2}[-/]\d{2}", url + title):
            return title or "公司披露", url

    # 其次选择第一个非首页来源
    for src in sources:
        title = src.get("title", "") or ""
        url = src.get("url", "") or ""
        if title and "披露易" not in title and "资讯网" not in title and "Investor" not in title:
            return title, url

    # 最后返回第一个来源
    return sources[0].get("title", "公司披露"), sources[0].get("url", "")


# ── 预测公式选择 ─────────────────────────────────────────────


def select_formula_for_segment(segment: dict[str, Any]) -> str:
    """根据分部名称和描述选择主要预测公式。

    每个分部只能选择一种主要预测逻辑。
    当前为规则推断，未接入真实经营数据。
    """
    text = " ".join(
        str(part)
        for part in [segment.get("name", ""), segment.get("description", "")]
        if part
    ).lower()

    # 订阅/SaaS/会员 → 订阅客户数×ARPU
    if any(k in text for k in ("订阅", "会员", "saas", "软件", "cloud")):
        return FORMULA_SUBSCRIPTION_ARPU

    # 门店/零售/连锁 → 门店数×单店收入
    if any(k in text for k in ("门店", "零售", "连锁", "线下", "餐饮", "百货")):
        return FORMULA_STORE_COUNT_REVENUE

    # 用户/活跃/互联网/平台 → 客户数×ARPU
    if any(k in text for k in ("用户", "活跃", "mau", "dau", "互联网", "平台", "增值", "游戏", "社交", "广告")):
        return FORMULA_CUSTOMER_ARPU

    # 市场/份额/赛道 → 行业空间×渗透率×份额
    if any(k in text for k in ("市场", "份额", "赛道")):
        return FORMULA_MARKET_SIZE_SHARE

    # 制造/产能/产量/销量/白酒/汽车 → 销量×单价
    if any(
        k in text
        for k in (
            "制造", "产能", "产量", "销量", "酒业", "白酒",
            "汽车", "能源", "钢铁", "化工", "产品", "硬件", "设备",
            "iphone", "mac", "ipad", "酒",
        )
    ):
        return FORMULA_VOLUME_PRICE

    return FORMULA_DIRECT_GROWTH


def select_margin_factors(segment: dict[str, Any]) -> list[str]:
    """根据分部名称和描述选择候选毛利率解释因子。

    可返回多个因子（辅助因素）。
    未匹配到证据时返回空列表（UI 显示"待判断"）。
    """
    text = " ".join(
        str(part)
        for part in [segment.get("name", ""), segment.get("description", "")]
        if part
    ).lower()

    factors: list[str] = []

    if any(k in text for k in ("产品", "结构", "组合", "mix", "硬件", "软件")):
        factors.append(MARGIN_FACTOR_PRODUCT_MIX)
    if any(k in text for k in ("规模", "成本效应", "规模效应", "scale")):
        factors.append(MARGIN_FACTOR_SCALE_EFFECT)
    if any(k in text for k in ("原料", "成本传导", "大宗", "原材料", "成本")):
        factors.append(MARGIN_FACTOR_COST_PASS_THROUGH)
    if any(k in text for k in ("供需", "周期", "景气", "行业")):
        factors.append(MARGIN_FACTOR_SUPPLY_DEMAND)
    if any(k in text for k in ("生命周期", "成熟", "衰退", "新品")):
        factors.append(MARGIN_FACTOR_LIFECYCLE)

    return factors


# ── 占位状态判断 ─────────────────────────────────────────────


def compute_segment_placeholder_status(
    missing_drivers: list[str],
    rationale_items: list[dict[str, Any]],
) -> bool:
    """纯函数：判断分部是否仍为占位假设。

    规则：
    - `missing_drivers` 非空 → True；
    - 任一 rationale item 的 is_placeholder=True → True（使用 any 带明确括号）；
    - 只有 missing_drivers 为空，且所有年度、所有指标均非占位时，才能为 False。

    用户只修改一个年度/指标，其余仍占位时，分部必须保持占位。
    """
    # 1. 任一必要 driver 缺失即为占位
    if missing_drivers:
        return True
    # 2. 任一 rationale item 仍为占位即为占位
    #    使用带明确括号的 any()，而不是 all()
    if rationale_items:
        any_placeholder = any(
            bool(it.get("is_placeholder", True))
            for it in rationale_items
        )
        if any_placeholder:
            return True
    # 3. 没有 missing_drivers，也没有任何占位 item，才为非占位
    #    但若没有任何 rationale item 可判断，说明尚未生成假设，也视为占位
    if not rationale_items:
        return True
    return False


# ── 多期历史趋势计算 ─────────────────────────────────────────


def compute_historical_trends(
    historical_periods: list[dict[str, Any]],
    segment_name: str,
    sources: list[dict[str, Any]],
    disclosure_provider: str = "",
    source_category: str = "",
) -> dict[str, Any]:
    """从真实多期历史数据计算同比、CAGR 和毛利率变化。

    边界规则：
    - 分母为零时不计算同比/CAGR；
    - 年份缺失时不跨空档计算同比；
    - 不得读取 Base 预测增速补齐历史数据；
    - 口径变化（comparability_key 不同）时停止跨口径计算；
    - 计算结果标记为 derived，保存输入证据引用 (evidence_id)。

    可比性规则（删除 comparable=True 硬编码）：
    - 只有连续年度、相同 comparability_key、相同 dimension、相同单位且来源完整的数据才能计算同比和 CAGR；
    - 最新相邻年度不可比或缺失时，历史增长率显示缺失，不回退旧同比；
    - CAGR 至少要求 3 个连续、完整、可比财年；
    - 口径变化时停止跨口径计算并显示具体原因。

    Args:
        historical_periods: 多期历史数据列表，每期含完整 provenance 字段
        segment_name: 分部名称
        sources: 数据来源列表
        disclosure_provider: 披露提供方
        source_category: 来源分类

    Returns:
        dict 包含：
        - periods: 每期收入/毛利率的 MetricEvidence 列表（含独立 provenance）
        - revenue_yoy: 收入同比增长率列表（derived，含 input_evidence_ids）
        - revenue_cagr: 收入 CAGR（derived，至少 3 个连续可比财年才计算）
        - margin_changes: 毛利率变化列表（derived，pp 变化）
        - missing_years: 缺失年份列表
        - comparable: 是否可比（基于 comparability_key + dimension + currency + unit 真实判断）
        - comparability_note: 不可比时的说明
        - has_real_history: 是否有真实多期历史数据（至少 2 期收入）
    """
    if not historical_periods:
        return {
            "periods": [],
            "revenue_yoy": [],
            "revenue_cagr": None,
            "margin_changes": [],
            "missing_years": [],
            "comparable": False,
            "comparability_note": "无历史数据",
            "has_real_history": False,
        }

    # 按财年排序（过滤非法财年）
    def _parse_fy(p: dict[str, Any]) -> int:
        fy_str = str(p.get("fiscal_year", "0")).replace("FY", "").strip()
        try:
            n = int(fy_str)
            return n if n > 1900 else 0
        except (ValueError, TypeError):
            return 0

    sorted_periods = sorted(historical_periods, key=_parse_fy)

    # 去重（同一财年取第一条）
    seen_years: set[int] = set()
    deduped: list[dict[str, Any]] = []
    for hp in sorted_periods:
        fy_int = _parse_fy(hp)
        if fy_int in seen_years:
            continue
        seen_years.add(fy_int)
        deduped.append(hp)
    sorted_periods = deduped

    # 构建每期 MetricEvidence（含完整 provenance）
    periods: list[dict[str, Any]] = []
    for idx, hp in enumerate(sorted_periods):
        fy = str(hp.get("fiscal_year", ""))
        fy_int = _parse_fy(hp)
        fiscal_period = f"{fy}财年" if fy else ""
        rev = hp.get("revenue")
        gm = hp.get("gross_margin")

        # 收入 provenance
        rev_nature_str = hp.get("revenue_nature", "reported")
        rev_channel = hp.get("revenue_channel", "snapshot")
        rev_pub_date = hp.get("revenue_publication_date", hp.get("publication_date", "未记录")) or "未记录"
        rev_src_name = hp.get("revenue_source_name", hp.get("source_name", "")) or disclosure_provider or ""
        rev_src_url = hp.get("revenue_url", "")
        rev_page = hp.get("revenue_page_or_table", "")
        rev_currency = hp.get("currency", "")
        rev_unit = hp.get("unit", "million")

        # 收入 nature：内置快照取得时必须标为 snapshot，不能伪装成本次实时官方抓取
        if rev_nature_str == "reported" and rev_channel == "snapshot":
            rev_nature = NATURE_SNAPSHOT
        elif rev_nature_str == "reported" and rev_channel == "realtime":
            rev_nature = NATURE_REPORTED
        elif rev_nature_str == "reported" and rev_channel == "uploaded_pdf":
            rev_nature = NATURE_REPORTED
        elif rev_nature_str == "missing":
            rev_nature = NATURE_MISSING
        else:
            rev_nature = _nature_from_basis(rev_nature_str, "")

        rev_evidence_id = f"{segment_name}_rev_{fy}_{idx}"

        periods.append({
            "fiscal_year": fy,
            "fiscal_year_int": fy_int,
            "revenue": rev,
            "gross_margin": gm,
            "currency": rev_currency,
            "unit": rev_unit,
            "comparability_key": hp.get("comparability_key", ""),
            "comparability_note": hp.get("comparability_note", ""),
            "revenue_evidence": make_metric_evidence(
                metric=METRIC_REVENUE,
                value=rev,
                unit=f"{rev_currency} {rev_unit}" if rev is not None else "",
                fiscal_period=fiscal_period,
                publication_date=rev_pub_date,
                source_type=_source_type_from_nature(rev_nature),
                source_name=rev_src_name,
                source_url=rev_src_url,
                nature=rev_nature,
                confidence=_confidence_from_nature(rev_nature),
                is_placeholder=False,
                is_historical=True,
            ),
            "revenue_evidence_id": rev_evidence_id,
            "revenue_page_or_table": rev_page,
            "revenue_acquisition_channel": rev_channel,
        })

        # 毛利率 provenance（独立来源）
        gm_nature_str = hp.get("gross_margin_nature", "missing")
        gm_channel = hp.get("gross_margin_channel", "")
        gm_pub_date = hp.get("gross_margin_publication_date", "未记录") or "未记录"
        gm_src_name = hp.get("gross_margin_source_name", "")
        gm_src_url = hp.get("gross_margin_url", "")
        gm_page = hp.get("gross_margin_page_or_table", "")

        if gm is not None:
            if gm_nature_str == "reported" and gm_channel == "snapshot":
                gm_nature = NATURE_SNAPSHOT
            elif gm_nature_str == "reported" and gm_channel in ("realtime", "uploaded_pdf"):
                gm_nature = NATURE_REPORTED
            elif gm_nature_str == "estimated":
                gm_nature = NATURE_ESTIMATED
            else:
                gm_nature = _nature_from_basis(gm_nature_str, "")
        else:
            gm_nature = NATURE_MISSING

        gm_evidence_id = f"{segment_name}_gm_{fy}_{idx}"

        periods[-1]["gross_margin_evidence"] = (
            make_metric_evidence(
                metric=METRIC_GROSS_MARGIN,
                value=gm,
                unit="百分比" if gm is not None else "",
                fiscal_period=fiscal_period,
                publication_date=gm_pub_date,
                source_type=_source_type_from_nature(gm_nature),
                source_name=gm_src_name,
                source_url=gm_src_url,
                nature=gm_nature,
                confidence=_confidence_from_nature(gm_nature),
                is_placeholder=False,
                is_historical=True,
            ) if gm is not None else None
        )
        periods[-1]["gross_margin_evidence_id"] = gm_evidence_id if gm is not None else None
        periods[-1]["gross_margin_page_or_table"] = gm_page
        periods[-1]["gross_margin_acquisition_channel"] = gm_channel

    # 可比性判断：所有期 comparability_key 相同且非空才算可比
    all_ck = [p.get("comparability_key", "") for p in periods]
    all_ck_non_empty = [ck for ck in all_ck if ck]
    comparable = False
    comparability_note = ""

    if not all_ck_non_empty:
        # 没有 comparability_key，无法确认可比性
        comparable = False
        comparability_note = "缺少 comparability_key，无法确认口径可比性"
    elif len(set(all_ck_non_empty)) > 1:
        # comparability_key 不一致
        comparable = False
        notes = []
        for p in periods:
            ck = p.get("comparability_key", "")
            cn = p.get("comparability_note", "")
            if cn:
                notes.append(f"{p['fiscal_year']}({ck}): {cn}")
        comparability_note = "口径不一致：" + "；".join(notes) if notes else "comparability_key 不一致"
    elif len(all_ck_non_empty) == len(all_ck):
        # 所有期都有 comparability_key 且一致
        comparable = True
        # 检查是否有 comparability_note 标注
        notes = [p.get("comparability_note", "") for p in periods if p.get("comparability_note")]
        if notes:
            comparability_note = "口径已通过显式别名合并，可比：" + "；".join(set(notes))
    else:
        comparable = False
        comparability_note = "部分年度缺少 comparability_key"

    # 检查 currency 和 unit 一致性
    all_currencies = set(p.get("currency", "") for p in periods if p.get("currency"))
    all_units = set(p.get("unit", "") for p in periods if p.get("unit"))
    if len(all_currencies) > 1:
        comparable = False
        comparability_note = f"币种不一致：{', '.join(all_currencies)}"
    if len(all_units) > 1:
        comparable = False
        comparability_note = f"单位不一致：{', '.join(all_units)}"

    # 收入同比增长率（derived）— 严格可比性检查
    revenue_yoy: list[dict[str, Any]] = []
    for i in range(1, len(periods)):
        prev = periods[i - 1]
        curr = periods[i]
        prev_rev = prev["revenue"]
        curr_rev = curr["revenue"]
        prev_year = prev["fiscal_year_int"]
        curr_year = curr["fiscal_year_int"]

        # 1. 年份连续性
        if prev_year > 0 and curr_year > 0 and curr_year - prev_year != 1:
            revenue_yoy.append({
                "from_year": prev["fiscal_year"],
                "to_year": curr["fiscal_year"],
                "growth_rate": None,
                "note": f"年份不连续（{prev_year}→{curr_year}），不跨空档计算同比",
                "input_evidence_ids": [],
            })
            continue

        # 2. 可比性检查：相邻两期 comparability_key 必须一致
        prev_ck = prev.get("comparability_key", "")
        curr_ck = curr.get("comparability_key", "")
        if prev_ck and curr_ck and prev_ck != curr_ck:
            revenue_yoy.append({
                "from_year": prev["fiscal_year"],
                "to_year": curr["fiscal_year"],
                "growth_rate": None,
                "note": f"口径不可比（{prev_ck}→{curr_ck}），停止计算同比",
                "input_evidence_ids": [],
            })
            continue
        if not prev_ck or not curr_ck:
            revenue_yoy.append({
                "from_year": prev["fiscal_year"],
                "to_year": curr["fiscal_year"],
                "growth_rate": None,
                "note": "缺少 comparability_key，无法确认可比性",
                "input_evidence_ids": [],
            })
            continue

        # 3. 收入缺失
        if prev_rev is None or curr_rev is None:
            revenue_yoy.append({
                "from_year": prev["fiscal_year"],
                "to_year": curr["fiscal_year"],
                "growth_rate": None,
                "note": "收入缺失，无法计算",
                "input_evidence_ids": [],
            })
            continue

        # 4. 非有限值
        if not (math.isfinite(prev_rev) and math.isfinite(curr_rev)):
            revenue_yoy.append({
                "from_year": prev["fiscal_year"],
                "to_year": curr["fiscal_year"],
                "growth_rate": None,
                "note": "收入含非有限值",
                "input_evidence_ids": [],
            })
            continue

        # 5. 分母为零/负
        if prev_rev <= 0:
            revenue_yoy.append({
                "from_year": prev["fiscal_year"],
                "to_year": curr["fiscal_year"],
                "growth_rate": None,
                "note": f"分母为{'零' if prev_rev == 0 else '负'}（{prev_rev}），不计算同比",
                "input_evidence_ids": [],
            })
            continue

        growth = (curr_rev - prev_rev) / prev_rev
        revenue_yoy.append({
            "from_year": prev["fiscal_year"],
            "to_year": curr["fiscal_year"],
            "growth_rate": growth,
            "note": "",
            "nature": NATURE_DERIVED,
            "source_name": "由历史收入计算",
            "input_evidence_ids": [prev["revenue_evidence_id"], curr["revenue_evidence_id"]],
        })

    # 收入 CAGR（derived）— 至少 3 个连续、完整、可比财年
    revenue_cagr = None
    # 收集所有连续、可比、有收入的年份序列
    comparable_revenue_periods: list[dict[str, Any]] = []
    for p in periods:
        if p["revenue"] is None:
            continue
        if not p.get("comparability_key"):
            continue
        comparable_revenue_periods.append(p)

    if len(comparable_revenue_periods) >= 3:
        # 检查是否连续：年份连续 + comparability_key 一致
        is_continuous = True
        for i in range(1, len(comparable_revenue_periods)):
            prev = comparable_revenue_periods[i - 1]
            curr = comparable_revenue_periods[i]
            if curr["fiscal_year_int"] - prev["fiscal_year_int"] != 1:
                is_continuous = False
                break
            if prev["comparability_key"] != curr["comparability_key"]:
                is_continuous = False
                break

        if is_continuous:
            first = comparable_revenue_periods[0]
            last = comparable_revenue_periods[-1]
            n_years = last["fiscal_year_int"] - first["fiscal_year_int"]
            if (
                n_years >= 2
                and first["revenue"] is not None
                and first["revenue"] > 0
                and last["revenue"] is not None
                and math.isfinite(first["revenue"])
                and math.isfinite(last["revenue"])
            ):
                cagr = (last["revenue"] / first["revenue"]) ** (1.0 / n_years) - 1.0
                all_ids = [p["revenue_evidence_id"] for p in comparable_revenue_periods]
                revenue_cagr = {
                    "from_year": first["fiscal_year"],
                    "to_year": last["fiscal_year"],
                    "n_years": n_years,
                    "cagr": cagr,
                    "nature": NATURE_DERIVED,
                    "source_name": "由历史收入计算",
                    "input_evidence_ids": all_ids,
                }

    # 毛利率变化（derived，pp 变化）— 严格可比性检查
    margin_changes: list[dict[str, Any]] = []
    for i in range(1, len(periods)):
        prev = periods[i - 1]
        curr = periods[i]
        prev_gm = prev["gross_margin"]
        curr_gm = curr["gross_margin"]
        prev_year = prev["fiscal_year_int"]
        curr_year = curr["fiscal_year_int"]

        if prev_year > 0 and curr_year > 0 and curr_year - prev_year != 1:
            continue
        if prev_gm is None or curr_gm is None:
            continue
        # 可比性检查
        prev_ck = prev.get("comparability_key", "")
        curr_ck = curr.get("comparability_key", "")
        if prev_ck and curr_ck and prev_ck != curr_ck:
            margin_changes.append({
                "from_year": prev["fiscal_year"],
                "to_year": curr["fiscal_year"],
                "change_pp": None,
                "note": f"口径不可比（{prev_ck}→{curr_ck}），不计算毛利率变化",
                "nature": NATURE_MISSING,
                "input_evidence_ids": [],
            })
            continue
        if not prev_ck or not curr_ck:
            continue
        if not (math.isfinite(prev_gm) and math.isfinite(curr_gm)):
            continue
        change_pp = (curr_gm - prev_gm) * 100  # 转为百分点
        margin_changes.append({
            "from_year": prev["fiscal_year"],
            "to_year": curr["fiscal_year"],
            "change_pp": change_pp,
            "note": "",
            "nature": NATURE_DERIVED,
            "source_name": "由历史毛利率计算",
            "input_evidence_ids": [
                prev.get("gross_margin_evidence_id"),
                curr.get("gross_margin_evidence_id"),
            ],
        })

    # 缺失年份检测
    all_years = [p["fiscal_year_int"] for p in periods if p["fiscal_year_int"] > 0]
    missing_years: list[str] = []
    if len(all_years) >= 2:
        min_y = min(all_years)
        max_y = max(all_years)
        for y in range(min_y, max_y + 1):
            if y not in all_years:
                missing_years.append(str(y))

    has_real_history = len([p for p in periods if p["revenue"] is not None]) >= 2

    return {
        "periods": periods,
        "revenue_yoy": revenue_yoy,
        "revenue_cagr": revenue_cagr,
        "margin_changes": margin_changes,
        "missing_years": missing_years,
        "comparable": comparable,
        "comparability_note": comparability_note,
        "has_real_history": has_real_history,
    }


# ── 证据层构建 ───────────────────────────────────────────────


def build_evidence_layer_for_segment(
    segment: dict[str, Any],
    assumptions: dict[str, Any],
    years: list[int],
) -> dict[str, Any]:
    """为单个分部构建完整的证据层。

    包含：
    - 预测公式（当前选择 + 描述 + 所需驱动因子 + 细化路径）
    - 基期指标证据（原子化，每条只代表一个指标，收入与毛利率来源独立）
    - 经营指标（当前全部为 missing，不补造数字）
    - 前瞻证据（当前全部为 missing，不补造数字）
    - 缺失的关键驱动数据列表
    - 当前 Base 假设值（仅出现在此表，不出现在基期事实）
    - 置信度
    - 是否仍为占位假设（任一 driver 缺失即保持占位）

    注意：
    - 基期收入按 segment.basis 标注来源；
    - 基期毛利率按 segment.gross_margin_basis 独立标注；
    - 历史增长率只有在存在至少两期真实可比收入时才能计算；
    - 当前只有一个基期时，历史增长率为 None；
    - Base 预测增长率只出现在"当前 Base 假设"表；
    - 本阶段不接入真实经营数据，所有分部 has_real_evidence 必须为 False。
    """
    seg_name = segment["name"]
    formula = select_formula_for_segment(segment)
    margin_factors = select_margin_factors(segment)
    required_drivers = FORMULA_REQUIRED_DRIVERS.get(formula, [])
    detailed_drivers = (
        FORMULA_VOLUME_PRICE_DETAILED_DRIVERS
        if formula == FORMULA_VOLUME_PRICE
        else []
    )

    # 来源信息
    sources = assumptions.get("sources", [])
    source_category = assumptions.get("source_category", "")
    disclosure_provider = assumptions.get("disclosure_provider", "")
    fiscal_year = assumptions.get("fiscal_year", "")
    fiscal_period = _format_fiscal_period(fiscal_year)
    publication_date = _extract_publication_date(sources)

    # 基期指标
    base_revenue = segment.get("base_revenue")
    base_gm = segment.get("base_gross_margin")
    basis = segment.get("basis", "estimated")
    gm_basis = segment.get("gross_margin_basis", "estimated")

    # 基期收入来源
    revenue_nature = _nature_from_basis(basis, source_category)
    revenue_confidence = _confidence_from_nature(revenue_nature)
    rev_src_name, rev_src_url = _select_source_for_metric(sources, revenue_nature)
    if revenue_nature == NATURE_REPORTED and disclosure_provider:
        rev_src_name = disclosure_provider

    # 基期毛利率来源（独立标注）
    gm_nature = _nature_from_basis(gm_basis, source_category)
    gm_confidence = _confidence_from_nature(gm_nature)
    gm_src_name, gm_src_url = _select_source_for_metric(sources, gm_nature)
    if gm_nature == NATURE_REPORTED and disclosure_provider:
        gm_src_name = disclosure_provider

    # 历史增长率：从多期历史数据计算
    # 只有多期真实可比收入时才能计算；单期基期为 None
    historical_periods = segment.get("historical_periods", [])
    historical_trend = compute_historical_trends(
        historical_periods=historical_periods,
        segment_name=seg_name,
        sources=sources,
        disclosure_provider=disclosure_provider,
        source_category=source_category,
    )

    # 如果有多期历史数据，使用真实计算的最近一期同比增长率
    growth_rate_value = None
    growth_nature = NATURE_MISSING
    growth_confidence = "low"
    if historical_trend["has_real_history"] and historical_trend["revenue_yoy"]:
        # 取最后一期有效同比
        valid_yoy = [
            y for y in historical_trend["revenue_yoy"]
            if y.get("growth_rate") is not None
        ]
        if valid_yoy:
            last_yoy = valid_yoy[-1]
            growth_rate_value = last_yoy["growth_rate"]
            growth_nature = NATURE_DERIVED
            growth_confidence = "medium"

    # 构建原子化指标证据列表
    metric_evidence: list[dict[str, Any]] = []

    # 基期收入
    metric_evidence.append(
        make_metric_evidence(
            metric=METRIC_REVENUE,
            value=base_revenue,
            unit="百万元" if base_revenue is not None else "",
            fiscal_period=fiscal_period,
            publication_date=publication_date,
            source_type=_source_type_from_nature(revenue_nature),
            source_name=rev_src_name,
            source_url=rev_src_url,
            nature=revenue_nature,
            confidence=revenue_confidence,
            is_placeholder=False,
            is_historical=True,
        )
    )

    # 基期毛利率
    metric_evidence.append(
        make_metric_evidence(
            metric=METRIC_GROSS_MARGIN,
            value=base_gm,
            unit="百分比" if base_gm is not None else "",
            fiscal_period=fiscal_period,
            publication_date=publication_date,
            source_type=_source_type_from_nature(gm_nature),
            source_name=gm_src_name,
            source_url=gm_src_url,
            nature=gm_nature,
            confidence=gm_confidence,
            is_placeholder=False,
            is_historical=True,
        )
    )

    # 历史增长率（单期基期为 None）
    # 缺失指标不能继承收入/毛利率公告的发布日期，必须为"未记录"
    metric_evidence.append(
        make_metric_evidence(
            metric=METRIC_GROWTH_RATE,
            value=growth_rate_value,
            unit="百分比",
            fiscal_period=fiscal_period,
            publication_date="未记录",
            source_type=_source_type_from_nature(growth_nature),
            source_name="",
            source_url="",
            nature=growth_nature,
            confidence=growth_confidence,
            is_placeholder=False,
            is_historical=True,
        )
    )

    # 经营指标（全部标记为 missing — 本阶段不接入真实经营数据）
    operating_metrics: list[dict[str, Any]] = []
    # 直接路径驱动因子
    for driver_name in required_drivers:
        operating_metrics.append(
            make_operating_metric(
                name=driver_name,
                segment_name=seg_name,
                year=int(fiscal_year) if str(fiscal_year).isdigit() else 0,
                value=None,
                unit="",
                source=SOURCE_MISSING,
                confidence="low",
            )
        )
    # 细化路径驱动因子（量价公式专属，且不与直接路径重复）
    if detailed_drivers:
        for driver_name in detailed_drivers:
            if driver_name not in required_drivers:
                operating_metrics.append(
                    make_operating_metric(
                        name=driver_name,
                        segment_name=seg_name,
                        year=int(fiscal_year) if str(fiscal_year).isdigit() else 0,
                        value=None,
                        unit="",
                        source=SOURCE_MISSING,
                        confidence="low",
                    )
                )

    # 前瞻证据（全部标记为 missing — 本阶段不接入真实前瞻数据）
    forward_evidence: list[dict[str, Any]] = []
    forward_types = [
        "company_target", "capacity_change", "new_product",
        "industry_supply_demand", "management_guidance",
    ]
    for fwd_type in forward_types:
        forward_evidence.append(
            make_forward_evidence(
                evidence_type=fwd_type,
                segment_name=seg_name,
                description="尚无此前瞻证据数据",
                applicable_year=years[0] if years else 0,
                source=SOURCE_MISSING,
                confidence="low",
            )
        )

    # 缺失的关键驱动数据
    missing_drivers = [
        om["name"] for om in operating_metrics if om["is_missing"]
    ]

    # 当前 Base 假设值（仅出现在此表，不出现在基期事实）
    base_growth_default = segment.get("base_growth")  # Base 预测增长率，非历史
    yearly = segment.get("yearly_assumptions", {})
    base_assumptions_by_year: list[dict[str, Any]] = []
    for year in years:
        year_data = yearly.get(str(year), {})
        base_assumptions_by_year.append({
            "year": year,
            "base_growth": year_data.get("base_growth", base_growth_default),
            "base_gross_margin": year_data.get("base_gross_margin", base_gm),
        })

    # 置信度（沿用 rationale_items 逻辑）
    rationale_items = assumptions.get("rationale_items", [])
    seg_items = [
        it for it in rationale_items
        if it.get("segment_name") == seg_name
    ]
    if seg_items:
        confidences = [it.get("confidence", "low") for it in seg_items]
        if "low" in confidences:
            overall_confidence = "low"
        elif "medium" in confidences:
            overall_confidence = "medium"
        else:
            overall_confidence = "high"
    else:
        overall_confidence = "low"

    # 是否仍为占位假设 — 使用纯函数 compute_segment_placeholder_status
    # 规则：
    # - missing_drivers 非空 → True
    # - 任一 rationale item is_placeholder=True → True（any，非 all）
    # - 只有 missing_drivers 为空且所有 item 非占位时才为 False
    is_placeholder = compute_segment_placeholder_status(
        missing_drivers=missing_drivers,
        rationale_items=seg_items,
    )

    # has_real_evidence 必须同时满足：
    # - 所有必要 driver 均有数值；
    # - 来源不是 missing、placeholder 或纯模型估算；
    # - 预测公式已实际用于计算 Base 结果。
    # 本阶段尚未计算 driver，因此所有分部的 has_real_evidence 必须为 False。
    has_real_evidence = False

    return {
        "segment_name": seg_name,
        "formula": formula,
        "formula_label": FORMULA_LABELS.get(formula, formula),
        "formula_description": FORMULA_DESCRIPTIONS.get(formula, ""),
        "required_drivers": required_drivers,
        "detailed_drivers": detailed_drivers,
        "has_detailed_path": bool(detailed_drivers),
        "margin_factors": margin_factors,
        "margin_factor_labels": [
            MARGIN_FACTOR_LABELS.get(f, f) for f in margin_factors
        ],
        "metric_evidence": metric_evidence,
        "historical_metrics": metric_evidence,  # 向后兼容
        "operating_metrics": operating_metrics,
        "forward_evidence": forward_evidence,
        "missing_drivers": missing_drivers,
        "base_assumptions": base_assumptions_by_year,
        "confidence": overall_confidence,
        "is_placeholder": is_placeholder,
        "has_real_evidence": has_real_evidence,
        "fiscal_period": fiscal_period,
        "publication_date": publication_date,
        "disclosure_provider": disclosure_provider,
        "source_category": source_category,
        "historical_trend": historical_trend,
    }


def build_evidence_layer(
    assumptions: dict[str, Any],
    years: list[int],
) -> list[dict[str, Any]]:
    """为所有分部构建证据层。

    返回列表，每个元素是一个分部的完整证据层。
    """
    segments = assumptions.get("segments", [])
    return [
        build_evidence_layer_for_segment(seg, assumptions, years)
        for seg in segments
    ]


# ── 证据层摘要 ───────────────────────────────────────────────


def evidence_layer_summary(evidence_layers: list[dict[str, Any]]) -> dict[str, Any]:
    """生成证据层摘要统计。"""
    total_segments = len(evidence_layers)
    total_missing_drivers = sum(len(el["missing_drivers"]) for el in evidence_layers)
    placeholder_count = sum(1 for el in evidence_layers if el["is_placeholder"])
    real_evidence_count = sum(1 for el in evidence_layers if el["has_real_evidence"])

    formula_distribution: dict[str, int] = {}
    for el in evidence_layers:
        f = el["formula"]
        formula_distribution[f] = formula_distribution.get(f, 0) + 1

    return {
        "total_segments": total_segments,
        "total_missing_drivers": total_missing_drivers,
        "placeholder_count": placeholder_count,
        "real_evidence_count": real_evidence_count,
        "formula_distribution": formula_distribution,
        "all_placeholder": placeholder_count == total_segments,
    }


# ── 公司适配案例 ─────────────────────────────────────────────


def get_company_adaptation_hint(symbol: str) -> dict[str, str]:
    """返回规则建议的预测路径（待经营数据验证）。

    用于首批验证案例：
    - 腾讯（0700.HK）：用户/ARPU
    - Apple（AAPL）：销量×单价
    - 贵州茅台（600519.SS）：销量/产能×单价
    """
    symbol_upper = symbol.upper()
    if "0700" in symbol_upper:
        return {
            "company": "腾讯控股",
            "formula": FORMULA_CUSTOMER_ARPU,
            "formula_label": FORMULA_LABELS[FORMULA_CUSTOMER_ARPU],
            "note": "腾讯核心业务适合用户数×ARPU逻辑，支付/企业服务可附加辅助因素。",
        }
    if "AAPL" in symbol_upper:
        return {
            "company": "Apple Inc.",
            "formula": FORMULA_VOLUME_PRICE,
            "formula_label": FORMULA_LABELS[FORMULA_VOLUME_PRICE],
            "note": "Apple 适合销量×单价逻辑，iPhone/Mac/iPad 各分部可用产品结构解释毛利率。",
        }
    if "600519" in symbol_upper:
        return {
            "company": "贵州茅台",
            "formula": FORMULA_VOLUME_PRICE,
            "formula_label": FORMULA_LABELS[FORMULA_VOLUME_PRICE],
            "note": "茅台适合销量/产能×单价逻辑，产能和利用率是关键驱动因子。",
        }
    return {
        "company": "",
        "formula": FORMULA_DIRECT_GROWTH,
        "formula_label": FORMULA_LABELS[FORMULA_DIRECT_GROWTH],
        "note": "未匹配到特定公司适配，使用默认增长率公式。",
    }
