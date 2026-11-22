"""换订阅追溯场景数据：同一 API 的两位调用方，其中一位在账期内有两段订阅。

这些表由 subscription_usage 领域服务消费：
- ``subscription``：两段先后生效的订阅（同一调用方同一 API）
- ``usage_billing``：月底合并出账的一行原账单
- ``_usage_detail``：换订阅前后的原始用量记录，用于切分命中区间
- ``_subscription_change``：订阅变化记录，写明换出/换入的订阅
- ``_billing_correction`` / ``_billing_trace_log``：更正单与只读追溯日志（初始为空）

金额设计（2026-10 账期，订单明细查询API）：
- 陈嘉禾：10-01~10-15 基础套餐 1800 次 × 0.20 元，10-16~10-31 增强套餐 1200 次 × 0.25 元
  月底按旧单价合并出账 600.00 元（3000 次），按订阅单价重算应为 660.00 元，
  原账单少收 60.00 元 → 需要开差额补收。
- 林墨：整月单段订阅 1200 次 × 0.20 元，原账单 240.00 元与重算一致 → 无需调整。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

TRACE_API = "订单明细查询API"
TRACE_CYCLE = "2026-10"
TRACE_CALLER_A = "陈嘉禾"
TRACE_CALLER_B = "林墨"

_SUB_A = {
    "id": 4,
    "status": "已通过",
    "pending": False,
    "abnormal": False,
    "申请编号": "SUBS-0011",
    "订阅API": TRACE_API,
    "申请方": TRACE_CALLER_A,
    "调用频率": "3000次/日",
    "数据范围": "订单主表",
    "有效期": "2026-10-01~2026-10-15",
    "审批状态": "已通过",
    "驳回原因": "",
    "套餐": "基础套餐",
    "生效开始": "2026-10-01",
    "生效结束": "2026-10-15",
    "计费单价": "0.20",
    "变更类型": "初始订阅",
    "变更前订阅": "",
}

_SUB_A_NEW = {
    "id": 5,
    "status": "已通过",
    "pending": False,
    "abnormal": False,
    "申请编号": "SUBS-0012",
    "订阅API": TRACE_API,
    "申请方": TRACE_CALLER_A,
    "调用频率": "6000次/日",
    "数据范围": "订单主表+明细",
    "有效期": "2026-10-16~2026-10-31",
    "审批状态": "已通过",
    "驳回原因": "",
    "套餐": "增强套餐",
    "生效开始": "2026-10-16",
    "生效结束": "2026-10-31",
    "计费单价": "0.25",
    "变更类型": "换订阅",
    "变更前订阅": "SUBS-0011",
}

_SUB_B = {
    "id": 6,
    "status": "已通过",
    "pending": False,
    "abnormal": False,
    "申请编号": "SUBS-0021",
    "订阅API": TRACE_API,
    "申请方": TRACE_CALLER_B,
    "调用频率": "1500次/日",
    "数据范围": "订单主表",
    "有效期": "2026-10-01~2026-10-31",
    "审批状态": "已通过",
    "驳回原因": "",
    "套餐": "基础套餐",
    "生效开始": "2026-10-01",
    "生效结束": "2026-10-31",
    "计费单价": "0.20",
    "变更类型": "初始订阅",
    "变更前订阅": "",
}


def _daily_details(
    start_id: int,
    caller: str,
    start_date: str,
    end_date: str,
    application_no: str,
    subscription_id: int,
    unit_price: float,
    calls_per_day: list[int],
) -> list[dict[str, Any]]:
    """按天生成原始用量；调用次数逐日给出，金额按订阅单价计算。"""
    rows: list[dict[str, Any]] = []
    cursor = date.fromisoformat(start_date)
    last = date.fromisoformat(end_date)
    index = 0
    while cursor <= last and index < len(calls_per_day):
        calls = calls_per_day[index]
        rows.append({
            "id": start_id + index,
            "用量编号": f"UDTL-{start_id + index:04d}",
            "调用方": caller,
            "API名称": TRACE_API,
            "计费周期": TRACE_CYCLE,
            "用量日期": cursor.isoformat(),
            "申请编号": application_no,
            "订阅ID": subscription_id,
            "调用次数": calls,
            "数据行数": calls * 15,
            "计费单价": f"{unit_price:.2f}",
            "费用金额": round(calls * unit_price, 2),
        })
        cursor += timedelta(days=1)
        index += 1
    return rows


# 陈嘉禾 10-01~10-15：合计 1800 次；10-16~10-31：合计 1200 次。
_A_OLD_CALLS = [120] * 15  # 1800
_A_NEW_CALLS = [75] * 16   # 1200
# 林墨 10-01~10-31：合计 1200 次，每三天一条记录，避免种子文件过长。
_B_CALLS = [120, 120, 120, 120, 120, 120, 120, 120, 120, 120]  # 1200


def _build_details() -> list[dict[str, Any]]:
    rows = _daily_details(
        1, TRACE_CALLER_A, "2026-10-01", "2026-10-15",
        "SUBS-0011", 4, 0.20, _A_OLD_CALLS)
    rows += _daily_details(
        16, TRACE_CALLER_A, "2026-10-16", "2026-10-31",
        "SUBS-0012", 5, 0.25, _A_NEW_CALLS)
    # 林墨整月单段订阅，每 3 天一条记录。
    cursor = date(2026, 10, 1)
    for offset, calls in enumerate(_B_CALLS):
        rows.append({
            "id": 40 + offset,
            "用量编号": f"UDTL-{40 + offset:04d}",
            "调用方": TRACE_CALLER_B,
            "API名称": TRACE_API,
            "计费周期": TRACE_CYCLE,
            "用量日期": (cursor + timedelta(days=offset * 3)).isoformat(),
            "申请编号": "SUBS-0021",
            "订阅ID": 6,
            "调用次数": calls,
            "数据行数": calls * 15,
            "计费单价": "0.20",
            "费用金额": round(calls * 0.20, 2),
        })
    return rows


SCENARIO_ROWS: dict[str, list[dict[str, Any]]] = {
    "subscription": [_SUB_A, _SUB_A_NEW, _SUB_B],
    "usage_billing": [
        {
            "id": 4,
            "status": "已出账",
            "pending": False,
            "abnormal": False,
            "记录编号": "USAG-2026-1001",
            "调用方": TRACE_CALLER_A,
            "API名称": TRACE_API,
            "调用次数": "3000",
            "数据行数": "45000",
            "计费周期": TRACE_CYCLE,
            "费用金额": 600.0,
            "结算状态": "已出账",
        },
        {
            "id": 5,
            "status": "已出账",
            "pending": False,
            "abnormal": False,
            "记录编号": "USAG-2026-1002",
            "调用方": TRACE_CALLER_B,
            "API名称": TRACE_API,
            "调用次数": "1200",
            "数据行数": "18000",
            "计费周期": TRACE_CYCLE,
            "费用金额": 240.0,
            "结算状态": "已出账",
        },
    ],
    "_usage_detail": _build_details(),
    "_subscription_change": [
        {
            "id": 1,
            "变更编号": "CHG-2026-0001",
            "订阅API": TRACE_API,
            "调用方": TRACE_CALLER_A,
            "原订阅": "SUBS-0011",
            "新订阅": "SUBS-0012",
            "变更类型": "套餐升级",
            "变更生效日": "2026-10-16",
            "变更原因": "订单量增长，基础套餐额度不足",
            "status": "已生效",
            "pending": False,
            "abnormal": False,
        },
    ],
    "_billing_correction": [],
    "_billing_trace_log": [],
}
