"""研究流程契约 — Phase 15-1。

定义固定流程：
    1. 公司与项目
    2. 历史业务与财务资料
    3. 假设与驱动因子
    4. 预测与情景
    5. 估值与市场对照（示范）
    6. 导出与交付

"资料与证据"不再是单独必经页面。资料来源、口径、可比性和自动提取说明
作为"历史业务与财务资料"页的备查内容，以及假设页的可展开依据内容。

关键行为契约：
    - 读取公司资料并生成业务拆分成功后，直接进入"历史业务与财务资料"
    - 后续页面仍可查看来源、披露资料、口径、质量与可比性
    - 最近披露年度显示财年性质，如 FY2025（公司财年，截至 2025-12-31）
    - 预测默认首年为"最近披露财年 + 1"
    - 若 FY2026 已被识别为真实披露期，才允许从 FY2027E 开始
    - 估值与市场对照为功能示范，仅 Apple 和腾讯提供静态演示数据
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ── 固定流程定义 ──────────────────────────────────────────────


FLOW_STEPS = (
    ("company", "公司与项目", "确认研究标的，获取最新披露年度与币种"),
    ("source", "历史业务与财务资料", "查看来源、披露资料、口径、质量与可比性"),
    ("assumption", "假设与驱动因子", "编辑分部收入增长率和毛利率假设"),
    ("forecast", "预测与情景", "查看 Bull/Base/Bear 三情景预测结果"),
    ("valuation", "估值与市场对照", "估值与市场对照（示范），仅 Apple 和腾讯提供静态演示数据"),
    ("export", "导出与交付", "导出 Excel 模型，包含假设、预测和依据"),
)

# 页面顺序（用于导航和前进/后退）
FLOW_ORDER = [step[0] for step in FLOW_STEPS]

# 旧流程中有"资料与证据"作为单独页面，新流程中合并到"历史业务与财务资料"
LEGACY_SOURCE_PAGE_KEY = "source"
LEGACY_SOURCE_PAGE_NAME = "资料与证据"
NEW_SOURCE_PAGE_NAME = "历史业务与财务资料"


@dataclass
class FlowStep:
    """流程步骤定义。"""

    key: str
    name: str
    description: str

    @property
    def step_number(self) -> int:
        """步骤编号（从 1 开始）。"""
        return FLOW_ORDER.index(self.key) + 1


def get_flow_step(key: str) -> FlowStep | None:
    """根据 key 获取流程步骤。"""
    for step_key, name, desc in FLOW_STEPS:
        if step_key == key:
            return FlowStep(key=step_key, name=name, description=desc)
    return None


def get_next_step(key: str) -> FlowStep | None:
    """获取下一步。"""
    if key not in FLOW_ORDER:
        return None
    idx = FLOW_ORDER.index(key)
    if idx + 1 >= len(FLOW_ORDER):
        return None
    return get_flow_step(FLOW_ORDER[idx + 1])


def get_prev_step(key: str) -> FlowStep | None:
    """获取上一步。"""
    if key not in FLOW_ORDER:
        return None
    idx = FLOW_ORDER.index(key)
    if idx == 0:
        return None
    return get_flow_step(FLOW_ORDER[idx - 1])


# ── 预测首年计算 ──────────────────────────────────────────────


def format_fiscal_year(fiscal_year: str | None, period_end_date: str | None = None) -> str:
    """格式化财年显示。

    返回格式：FY2025（公司财年，截至 2025-12-31）
    如果没有 period_end_date，只返回 FY2025（公司财年）
    """
    if not fiscal_year:
        return "未确定财年"

    year_str = str(fiscal_year).strip()
    if period_end_date:
        # 格式化日期为 YYYY-MM-DD
        date_str = str(period_end_date).strip()
        if " " in date_str:
            date_str = date_str.split(" ")[0]
        if len(date_str) >= 10:
            date_str = date_str[:10]
        return f"FY{year_str}（公司财年，截至 {date_str}）"
    return f"FY{year_str}（公司财年）"


def compute_forecast_start_year(
    fiscal_year: str | None,
    actual_disclosure_years: list[str] | None = None,
) -> tuple[int, str]:
    """计算预测首年。

    规则：
    1. 预测默认首年为"最近披露财年 + 1"
    2. 若该年已被系统识别为真实披露期，才允许跳到下一年

    返回: (预测首年, 年份性质)
        年份性质: "Estimate"（预测年）或 "Actual"（已是真实披露期）

    示例:
        fiscal_year="2025" → 返回 (2026, "Estimate")
        fiscal_year="2025", actual_disclosure_years=["2026"] → 返回 (2027, "Estimate")
    """
    if not fiscal_year:
        # 无最近披露财年，回退到当前年+1
        from datetime import date
        return date.today().year + 1, "Estimate"

    try:
        latest_year = int(str(fiscal_year).strip())
    except (ValueError, TypeError):
        from datetime import date
        return date.today().year + 1, "Estimate"

    # 默认首年 = 最近披露财年 + 1
    forecast_start = latest_year + 1

    # 如果该年已被识别为真实披露期，跳到下一年
    actual_years_set = {str(y).strip() for y in (actual_disclosure_years or [])}
    while str(forecast_start) in actual_years_set:
        forecast_start += 1

    return forecast_start, "Estimate"


def format_forecast_year(year: int, nature: str = "Estimate") -> str:
    """格式化预测年度显示。

    返回格式：
        - Estimate: FY2026E
        - Actual: FY2026（实际）
    """
    if nature == "Actual":
        return f"FY{year}（实际）"
    return f"FY{year}E"


def should_skip_source_page(
    assumptions: dict[str, Any] | None,
    research_completed: bool,
) -> bool:
    """判断是否应跳过"资料与证据"页面。

    行为契约：
    - 读取公司资料并生成业务拆分成功后，直接进入"历史业务与财务资料"
    - 不再要求第二次点击"下一步：资料与证据"

    但这不是删除页面，而是自动前进。
    """
    if not research_completed:
        return False
    if assumptions is None:
        return False
    # 如果已有 assumptions 且研究已完成，说明拆分已生成
    # 此时不需要要求用户再次点击进入资料页
    return True
