"""订阅账期用量明细：按一次换订阅回查原账单与命中用量。

背景：
- 已出账用量平时只能按账期找到，缺少结合订阅变化记录的追查能力。
- 同一账期内同一 API 可能先后生效两份订阅；结算时同一调用方的请求被合
  成一行账单，账单上看不出两段订阅各自的生效区间、呼叫范围和金额。
- 财务需要找出与换订阅有关的用量，核对是否需要开差额调整。

本服务只做只读追溯：
1. 按账期 / 调用方 / API 找到已出账的原账单，原金额原样保留；
2. 读取订阅生效区间和订阅变化记录，还原账期内先后生效的订阅段；
3. 读取原始用量明细，按订阅段切出命中区间、命中调用量与命中金额；
4. 按原始调用量分摊原账单金额，与按订阅单价重算的金额对比，得出差额；
5. 用原用量记录判断能否发起更正（不自动入账）。

“打开原账单再查询换订阅明细不能视为二次收费”：
- 追溯查询全程只读，不新增账单、不改原账单金额、不重复计费用量；
- 每段分摊金额之和恒等于原账单金额（allocation_gap 始终为 0）；
- 重算金额只作为差额核对依据，返回结构中与 original_amount 分开呈现；
- 每次查询写一条零金额追溯日志，便于证明检索没有产生收费。
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from typing import Any

from app.store import store

CYCLE_RE = re.compile(r"^(\d{4})-(\d{2})$")
DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
INTERVAL_RE = re.compile(
    r"^\s*(\d{4}-\d{2}-\d{2})\s*(?:~|～|至|到|—|–|/|//)\s*(\d{4}-\d{2}-\d{2})\s*$"
)

# 只有已经出账的账单才进入财务核对范围；“计量中”的账单尚未出账。
BILLED_STATUSES = ("已出账", "已确认", "已结算")
CORRECTABLE_STATUSES = ("已出账", "已确认", "已结算")
# 订阅到达这些状态后才可能产生可计费用量。
SUBSCRIBED_STATUSES = ("已通过", "已生效", "已失效", "已完成")

MONEY_TOLERANCE = 0.01

DETAIL_MODULE = "_usage_detail"
CHANGE_MODULE = "_subscription_change"
CORRECTION_MODULE = "_billing_correction"
TRACE_LOG_MODULE = "_billing_trace_log"


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _number(value: Any, default: float | None = None) -> float | None:
    if value is None:
        return default
    text = str(value).strip().replace(",", "")
    if not text:
        return default
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    number = _number(value)
    return int(number) if number is not None else default


def _date(value: Any) -> str:
    text = _text(value)[:10]
    return text if DATE_RE.match(text) else ""


def _money(value: float) -> float:
    return round(float(value) + 0.0, 2)


def _same(left: Any, right: Any) -> bool:
    return _text(left).casefold() == _text(right).casefold()


def cycle_bounds(cycle: str) -> tuple[str, str] | None:
    matched = CYCLE_RE.match(_text(cycle))
    if not matched:
        return None
    year, month = int(matched.group(1)), int(matched.group(2))
    if not 1 <= month <= 12:
        return None
    last_day = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{last_day:02d}"


def _days(start: str, end: str) -> int:
    try:
        return max((date.fromisoformat(end) - date.fromisoformat(start)).days + 1, 0)
    except ValueError:
        return 0


def _clip(start: str, end: str, cycle_start: str, cycle_end: str) -> tuple[str, str] | None:
    left = max(start, cycle_start)
    right = min(end, cycle_end)
    return (left, right) if left <= right else None


def _parse_interval(value: Any) -> tuple[str, str] | None:
    matched = INTERVAL_RE.match(_text(value))
    if not matched:
        return None
    return matched.group(1), matched.group(2)


def _contiguous_ranges(days: list[str]) -> list[dict[str, Any]]:
    """把命中的用量日期压缩成连续区间，便于财务定位实际变动。"""
    if not days:
        return []
    ordered = sorted(set(days))
    ranges: list[dict[str, Any]] = []
    start = prev = ordered[0]
    for day in ordered[1:]:
        gap = (date.fromisoformat(day) - date.fromisoformat(prev)).days
        if gap == 1:
            prev = day
            continue
        ranges.append({"start": start, "end": prev, "days": _days(start, prev)})
        start = prev = day
    ranges.append({"start": start, "end": prev, "days": _days(start, prev)})
    return ranges


class SubscriptionUsageService:
    """订阅账期用量明细服务；不参与通用资源路由，只提供只读追溯接口。"""

    MODULE = "subscription_usage"
    ENTITY = "订阅账期用量明细"

    # ---------- 读取原始数据 ----------

    def _bills(
        self,
        billing_cycle: str | None = None,
        caller: str | None = None,
        api_name: str | None = None,
        bill_id: int | None = None,
        billed_only: bool = False,
    ) -> list[dict[str, Any]]:
        rows = store.rows("usage_billing")
        selected: list[dict[str, Any]] = []
        for row in rows:
            if bill_id is not None and int(row.get("id", 0)) != bill_id:
                continue
            if billing_cycle and _text(row.get("计费周期")) != billing_cycle:
                continue
            if caller and not _same(row.get("调用方"), caller):
                continue
            if api_name and not _same(row.get("API名称"), api_name):
                continue
            if billed_only and _text(row.get("status")) not in BILLED_STATUSES:
                continue
            selected.append(row)
        return selected

    def _details(
        self,
        billing_cycle: str | None = None,
        caller: str | None = None,
        api_name: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = store.rows(DETAIL_MODULE)
        selected: list[dict[str, Any]] = []
        for row in rows:
            if billing_cycle and _text(row.get("计费周期")) != billing_cycle:
                continue
            if caller and not _same(row.get("调用方"), caller):
                continue
            if api_name and not _same(row.get("API名称"), api_name):
                continue
            selected.append(row)
        return selected

    def _changes(
        self,
        billing_cycle: str | None = None,
        caller: str | None = None,
        api_name: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = store.rows(CHANGE_MODULE)
        selected: list[dict[str, Any]] = []
        for row in rows:
            if caller and not _same(row.get("调用方"), caller):
                continue
            if api_name and not _same(row.get("订阅API"), api_name):
                continue
            change_date = _date(row.get("变更生效日"))
            if billing_cycle and change_date:
                bounds = cycle_bounds(billing_cycle)
                if bounds and not (bounds[0] <= change_date <= bounds[1]):
                    continue
            selected.append(row)
        return selected

    # ---------- 订阅段还原 ----------

    def _subscription_rows(self, api_name: str, caller: str) -> list[dict[str, Any]]:
        rows = []
        for row in store.rows("subscription"):
            if not _same(row.get("订阅API"), api_name):
                continue
            if not _same(row.get("申请方"), caller):
                continue
            if _text(row.get("status")) not in SUBSCRIBED_STATUSES:
                continue
            rows.append(row)
        return rows

    def _segments(
        self,
        api_name: str,
        caller: str,
        cycle_start: str,
        cycle_end: str,
    ) -> list[dict[str, Any]]:
        """把同一调用方同一 API 在账期内的订阅展开成先后生效的订阅段。"""
        segments: list[dict[str, Any]] = []
        for row in self._subscription_rows(api_name, caller):
            start = _date(row.get("生效开始"))
            end = _date(row.get("生效结束"))
            if not (start and end):
                parsed = _parse_interval(row.get("有效期"))
                if parsed:
                    start, end = parsed
            resolved = bool(start and end)
            clipped = _clip(start, end, cycle_start, cycle_end) if resolved else None
            if resolved and clipped is None:
                continue
            segments.append({
                "subscription_id": row.get("id"),
                "application_no": _text(row.get("申请编号")),
                "package": _text(row.get("套餐")) or _text(row.get("数据范围")),
                "applicant": _text(row.get("申请方")),
                "api_name": _text(row.get("订阅API")),
                "effective_start": start,
                "effective_end": end,
                "cycle_start": clipped[0] if clipped else cycle_start,
                "cycle_end": clipped[1] if clipped else cycle_end,
                "covered_days": _days(*clipped) if clipped else 0,
                "unit_price": _number(row.get("计费单价")),
                "subscription_status": _text(row.get("status")),
                "change_marker": _text(row.get("变更类型")),
                "previous_application_no": _text(row.get("变更前订阅")),
                "resolved": resolved,
            })
        segments.sort(key=lambda item: (
            item["effective_start"] or cycle_start, int(item["subscription_id"] or 0)))
        return segments

    def _attach_hits(
        self,
        segments: list[dict[str, Any]],
        details: list[dict[str, Any]],
    ) -> None:
        """把原始用量明细挂到订阅段上，产出命中区间与命中金额。"""
        claimed: set[int] = set()
        for segment in segments:
            matched: list[dict[str, Any]] = []
            for detail in details:
                detail_date = _date(detail.get("用量日期"))
                if not detail_date:
                    continue
                if not (segment["cycle_start"] <= detail_date <= segment["cycle_end"]):
                    continue
                linked = _text(detail.get("申请编号")) or _text(detail.get("订阅编号"))
                if linked and segment["application_no"]:
                    if not _same(linked, segment["application_no"]):
                        continue
                elif not segment["resolved"]:
                    continue
                matched.append(detail)
                claimed.add(int(detail.get("id", 0)))

            calls = sum(_integer(item.get("调用次数")) for item in matched)
            rows = sum(_integer(item.get("数据行数")) for item in matched)
            amount = sum(_number(item.get("费用金额"), 0.0) or 0.0 for item in matched)
            days = sorted({_date(item.get("用量日期")) for item in matched if _date(item.get("用量日期"))})
            segment["detail_records"] = len(matched)
            segment["detail_calls"] = calls
            segment["detail_rows"] = rows
            segment["detail_amount"] = _money(amount)
            segment["hit_start"] = days[0] if days else ""
            segment["hit_end"] = days[-1] if days else ""
            segment["hit_ranges"] = _contiguous_ranges(days)
            segment["hit_dates"] = len(days)

        # 未挂上任何订阅段的用量单独列出，避免财务漏看。
        leftovers = [
            detail for detail in details
            if int(detail.get("id", 0)) not in claimed
        ]
        if leftovers:
            dates = sorted({_date(item.get("用量日期")) for item in leftovers if _date(item.get("用量日期"))})
            segments.append({
                "subscription_id": None,
                "application_no": "",
                "package": "未匹配订阅",
                "applicant": _text(leftovers[0].get("调用方")),
                "api_name": _text(leftovers[0].get("API名称")),
                "effective_start": dates[0] if dates else "",
                "effective_end": dates[-1] if dates else "",
                "cycle_start": dates[0] if dates else "",
                "cycle_end": dates[-1] if dates else "",
                "covered_days": _days(dates[0], dates[-1]) if dates else 0,
                "unit_price": None,
                "subscription_status": "",
                "change_marker": "",
                "previous_application_no": "",
                "resolved": False,
                "detail_records": len(leftovers),
                "detail_calls": sum(_integer(item.get("调用次数")) for item in leftovers),
                "detail_rows": sum(_integer(item.get("数据行数")) for item in leftovers),
                "detail_amount": _money(sum(
                    _number(item.get("费用金额"), 0.0) or 0.0 for item in leftovers)),
                "hit_start": dates[0] if dates else "",
                "hit_end": dates[-1] if dates else "",
                "hit_ranges": _contiguous_ranges(dates),
                "hit_dates": len(dates),
            })

    # ---------- 账单分摊与差额 ----------

    @staticmethod
    def _allocate(original_amount: float, segments: list[dict[str, Any]]) -> list[float]:
        """按原始调用量分摊原账单金额，余数放在最后一段，保证合计等于原金额。"""
        weights = [max(segment.get("detail_calls", 0), 0) for segment in segments]
        total = sum(weights)
        if total <= 0:
            weights = [max(segment.get("covered_days", 0), 0) for segment in segments]
            total = sum(weights)
        if total <= 0:
            return [_money(original_amount)] + [_money(0) for _ in segments[1:]]

        allocated: list[float] = []
        remaining = _money(original_amount)
        for index, weight in enumerate(weights):
            if index == len(weights) - 1:
                allocated.append(_money(remaining))
            else:
                share = _money(original_amount * weight / total)
                allocated.append(share)
                remaining = _money(remaining - share)
        return allocated

    def _recalculate(self, segment: dict[str, Any]) -> float:
        price = segment.get("unit_price")
        if price is not None and segment.get("detail_calls"):
            return _money(price * segment["detail_calls"])
        return _money(segment.get("detail_amount", 0.0))

    def _mark_changes(
        self,
        segments: list[dict[str, Any]],
        changes: list[dict[str, Any]],
        cycle_start: str,
    ) -> list[dict[str, Any]]:
        """结合订阅变化记录标注换订阅；没有记录时按生效区间相邻关系推断。"""
        events: list[dict[str, Any]] = []
        change_by_new: dict[str, dict[str, Any]] = {}
        for change in changes:
            new_no = _text(change.get("新订阅"))
            if new_no:
                change_by_new[new_no] = change

        previous: dict[str, Any] | None = None
        for segment in segments:
            matched_change = change_by_new.get(segment["application_no"])
            if matched_change:
                segment["change_type"] = _text(matched_change.get("变更类型")) or "换订阅"
                segment["changed"] = True
                events.append({
                    "change_id": matched_change.get("id"),
                    "change_no": _text(matched_change.get("变更编号")),
                    "change_type": segment["change_type"],
                    "change_date": _date(matched_change.get("变更生效日")),
                    "from_subscription": _text(matched_change.get("原订阅")),
                    "to_subscription": segment["application_no"],
                    "reason": _text(matched_change.get("变更原因")),
                    "source": "订阅变化记录",
                    "applied": segment["resolved"] and bool(segment.get("detail_records")),
                })
            elif previous and segment["effective_start"] and segment["effective_start"] > cycle_start:
                segment["change_type"] = "换订阅"
                segment["changed"] = True
                events.append({
                    "change_id": None,
                    "change_no": "",
                    "change_type": "换订阅",
                    "change_date": segment["effective_start"],
                    "from_subscription": previous["application_no"],
                    "to_subscription": segment["application_no"],
                    "reason": "",
                    "source": "按订阅生效区间推断",
                    "applied": bool(segment.get("detail_records")),
                })
            else:
                segment["change_type"] = segment.get("change_marker") or "单段订阅"
                segment["changed"] = False
            previous = segment
        return events

    def _bill_trace(
        self,
        bill: dict[str, Any],
        cycle_start: str,
        cycle_end: str,
        changes: list[dict[str, Any]],
        details: list[dict[str, Any]],
    ) -> dict[str, Any]:
        api_name = _text(bill.get("API名称"))
        caller = _text(bill.get("调用方"))
        original_amount = _money(_number(bill.get("费用金额"), 0.0) or 0.0)
        segments = self._segments(api_name, caller, cycle_start, cycle_end)
        self._attach_hits(segments, details)

        allocations = self._allocate(original_amount, segments)
        for segment, allocated in zip(segments, allocations):
            recalculated = self._recalculate(segment)
            segment["allocated_amount"] = allocated
            segment["recalculated_amount"] = recalculated
            segment["delta"] = _money(allocated - recalculated)
            segment["delta_direction"] = (
                "原账单高于按订阅单价重算" if segment["delta"] > MONEY_TOLERANCE
                else "原账单低于按订阅单价重算" if segment["delta"] < -MONEY_TOLERANCE
                else "一致"
            )
            segment["priced_by"] = "订阅单价×命中调用量" if segment.get("unit_price") is not None else "原始用量金额"

        events = self._mark_changes(segments, changes, cycle_start)

        allocated_total = _money(sum(seg["allocated_amount"] for seg in segments))
        recalculated_total = _money(sum(seg["recalculated_amount"] for seg in segments))
        delta_total = _money(original_amount - recalculated_total)
        billed = _text(bill.get("status")) in BILLED_STATUSES
        needs_adjustment = billed and abs(delta_total) >= MONEY_TOLERANCE
        can_correct = needs_adjustment and _text(bill.get("status")) in CORRECTABLE_STATUSES
        blocked_reason = ""
        if not billed:
            blocked_reason = "账单尚未出账，仍按用量计量处理，不能发起更正"
        elif not needs_adjustment:
            blocked_reason = "按订阅生效区间重算后与原账单一致，无需差额调整"
        elif not can_correct:
            blocked_reason = f"账单状态为{_text(bill.get('status'))}，不允许发起更正"

        changed_segments = [
            {
                "subscription_id": segment.get("subscription_id"),
                "application_no": segment["application_no"],
                "hit_start": segment["hit_start"],
                "hit_end": segment["hit_end"],
                "delta": segment["delta"],
            }
            for segment in segments
            if segment["changed"] and abs(segment["delta"]) >= MONEY_TOLERANCE
        ]

        return {
            "bill_id": bill.get("id"),
            "record_no": _text(bill.get("记录编号")),
            "caller": caller,
            "api_name": api_name,
            "billing_cycle": _text(bill.get("计费周期")),
            "bill_status": _text(bill.get("status")),
            "settlement_status": _text(bill.get("结算状态")),
            "billed": billed,
            "original_amount": original_amount,
            "original_calls": _integer(bill.get("调用次数")),
            "original_rows": _integer(bill.get("数据行数")),
            "original_amount_note": "原账单金额按出账结果原样保留，未做改写",
            "segments": segments,
            "changes": events,
            "reconciliation": {
                "basis": "原账单 + 订阅生效区间 + 原始用量记录",
                "original_amount": original_amount,
                "allocated_total": allocated_total,
                "recalculated_total": recalculated_total,
                "delta_total": delta_total,
                "allocation_gap": _money(original_amount - allocated_total),
                "adjustment_type": (
                    "差额冲减" if delta_total > MONEY_TOLERANCE
                    else "差额补收" if delta_total < -MONEY_TOLERANCE
                    else "无需调整"
                ),
                "needs_adjustment": needs_adjustment,
                "can_correct": can_correct,
                "blocked_reason": blocked_reason,
                "changed_segments": changed_segments,
            },
        }

    # ---------- 对外查询 ----------

    def list_bills(
        self,
        billing_cycle: str | None = None,
        caller: str | None = None,
        api_name: str | None = None,
        changed_only: bool = False,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        if billing_cycle and cycle_bounds(billing_cycle) is None:
            raise ValueError("计费周期格式应为 YYYY-MM")
        rows = self._bills(billing_cycle=billing_cycle, caller=caller, api_name=api_name)
        items: list[dict[str, Any]] = []
        for bill in rows:
            cycle = _text(bill.get("计费周期"))
            bounds = cycle_bounds(cycle)
            if bounds is None:
                continue
            api = _text(bill.get("API名称"))
            who = _text(bill.get("调用方"))
            changes = self._changes(cycle, who, api)
            segments = self._segments(api, who, bounds[0], bounds[1])
            self._attach_hits(segments, self._details(cycle, who, api))
            events = self._mark_changes(segments, changes, bounds[0])
            if changed_only and not events:
                continue
            items.append({
                "bill_id": bill.get("id"),
                "record_no": _text(bill.get("记录编号")),
                "caller": who,
                "api_name": api,
                "billing_cycle": cycle,
                "bill_status": _text(bill.get("status")),
                "billed": _text(bill.get("status")) in BILLED_STATUSES,
                "original_amount": _money(_number(bill.get("费用金额"), 0.0) or 0.0),
                "original_calls": _integer(bill.get("调用次数")),
                "original_rows": _integer(bill.get("数据行数")),
                "segment_count": len(segments),
                "change_count": len(events),
                "changed": bool(events),
                "hit_start": min((seg["hit_start"] for seg in segments if seg["hit_start"]), default=""),
                "hit_end": max((seg["hit_end"] for seg in segments if seg["hit_end"]), default=""),
            })
        total = len(items)
        start = max(page - 1, 0) * size
        return items[start:start + size], total

    def list_details(
        self,
        billing_cycle: str | None = None,
        caller: str | None = None,
        api_name: str | None = None,
        application_no: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._details(billing_cycle, caller, api_name)
        if application_no:
            rows = [row for row in rows
                    if _same(row.get("申请编号"), application_no)]
        rows = sorted(rows, key=lambda row: (
            _text(row.get("用量日期")), int(row.get("id", 0))))
        total = len(rows)
        start = max(page - 1, 0) * size
        items = [{
            "detail_id": row.get("id"),
            "detail_no": _text(row.get("用量编号")),
            "caller": _text(row.get("调用方")),
            "api_name": _text(row.get("API名称")),
            "billing_cycle": _text(row.get("计费周期")),
            "usage_date": _date(row.get("用量日期")),
            "application_no": _text(row.get("申请编号")),
            "subscription_id": row.get("订阅ID"),
            "call_count": _integer(row.get("调用次数")),
            "data_rows": _integer(row.get("数据行数")),
            "unit_price": _number(row.get("计费单价")),
            "amount": _money(_number(row.get("费用金额"), 0.0) or 0.0),
        } for row in rows[start:start + size]]
        return items, total

    def list_changes(
        self,
        billing_cycle: str | None = None,
        caller: str | None = None,
        api_name: str | None = None,
    ) -> list[dict[str, Any]]:
        items = []
        for row in self._changes(billing_cycle, caller, api_name):
            items.append({
                "change_id": row.get("id"),
                "change_no": _text(row.get("变更编号")),
                "change_type": _text(row.get("变更类型")),
                "change_date": _date(row.get("变更生效日")),
                "api_name": _text(row.get("订阅API")),
                "caller": _text(row.get("调用方")),
                "from_subscription": _text(row.get("原订阅")),
                "to_subscription": _text(row.get("新订阅")),
                "reason": _text(row.get("变更原因")),
                "status": _text(row.get("status")),
            })
        items.sort(key=lambda item: (item["change_date"], int(item["change_id"] or 0)))
        return items

    def trace(
        self,
        billing_cycle: str | None = None,
        bill_id: int | None = None,
        caller: str | None = None,
        api_name: str | None = None,
    ) -> dict[str, Any]:
        """按账期或按原账单回查换订阅明细；整个过程不产生任何费用。"""
        filters = {"billing_cycle": billing_cycle, "bill_id": bill_id,
                   "caller": caller, "api_name": api_name}

        if bill_id is not None:
            bill = store.find("usage_billing", bill_id)
            if bill is None:
                raise LookupError(f"原账单 {bill_id} 不存在或已归档")
            if billing_cycle and _text(bill.get("计费周期")) != billing_cycle:
                raise ValueError("指定的账单不属于该计费周期")
            if caller and not _same(bill.get("调用方"), caller):
                raise ValueError("指定的账单不属于该调用方")
            if api_name and not _same(bill.get("API名称"), api_name):
                raise ValueError("指定的账单不属于该 API")
            bills = [bill]
        else:
            if not billing_cycle:
                raise ValueError("按账期追溯时必须提供 billing_cycle，或直接提供 bill_id")
            if cycle_bounds(billing_cycle) is None:
                raise ValueError("计费周期格式应为 YYYY-MM")
            bills = self._bills(billing_cycle=billing_cycle, caller=caller, api_name=api_name)

        traces: list[dict[str, Any]] = []
        for bill in bills:
            cycle = _text(bill.get("计费周期"))
            bounds = cycle_bounds(cycle)
            if bounds is None:
                if bill_id is not None:
                    raise ValueError(
                        f"原账单 {bill_id} 的计费周期「{cycle or '空'}」不是 YYYY-MM 格式，"
                        "无法按账期切分，请先补全账单账期")
                continue
            api = _text(bill.get("API名称"))
            who = _text(bill.get("调用方"))
            traces.append(self._bill_trace(
                bill, bounds[0], bounds[1],
                self._changes(cycle, who, api),
                self._details(cycle, who, api),
            ))

        corrections = self._corrections_for([trace["bill_id"] for trace in traces])
        totals = {
            "bill_count": len(traces),
            "original_amount": _money(sum(t["original_amount"] for t in traces)),
            "allocated_total": _money(sum(
                t["reconciliation"]["allocated_total"] for t in traces)),
            "recalculated_total": _money(sum(
                t["reconciliation"]["recalculated_total"] for t in traces)),
            "delta_total": _money(sum(
                t["reconciliation"]["delta_total"] for t in traces)),
            "needs_adjustment_count": sum(
                1 for t in traces if t["reconciliation"]["needs_adjustment"]),
            "correctable_count": sum(
                1 for t in traces if t["reconciliation"]["can_correct"]),
        }

        response = {
            "filters": filters,
            "bills": traces,
            "totals": totals,
            "corrections": corrections,
            "readonly": True,
            "billing_effect": {
                "query_mode": "原账单只读追溯",
                "regenerated_bills": 0,
                "duplicate_charge": False,
                "charge_delta": 0.0,
                "note": "检索拆分仅用于核对，原账单金额不变更，不产生二次收费",
            },
        }
        self._log_trace(response)
        return response

    def create_correction(
        self,
        bill_id: int,
        remark: str | None = None,
        operator: str = "数据工程组",
    ) -> tuple[dict[str, Any] | None, str]:
        """依据原用量记录发起差额更正申请；只登记更正单，不直接改账。"""
        trace_result = self.trace(bill_id=bill_id)
        if not trace_result["bills"]:
            return None, f"原账单 {bill_id} 不存在或已归档"
        bill_trace = trace_result["bills"][0]
        reconciliation = bill_trace["reconciliation"]
        if not reconciliation["can_correct"]:
            return None, reconciliation["blocked_reason"] or "当前账单不满足更正条件"

        rows = store.rows(CORRECTION_MODULE)
        existing = [row for row in rows
                    if int(row.get("bill_id", 0)) == bill_id
                    and _text(row.get("status")) in ("待复核", "已受理")]
        if existing:
            return existing[0], "该账单已有待处理的差额更正申请，未重复登记"

        new_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        entry = {
            "id": new_id,
            "更正单号": f"CORR-{new_id:04d}",
            "bill_id": bill_id,
            "记录编号": bill_trace["record_no"],
            "调用方": bill_trace["caller"],
            "API名称": bill_trace["api_name"],
            "计费周期": bill_trace["billing_cycle"],
            "原账单金额": bill_trace["original_amount"],
            "重算金额": reconciliation["recalculated_total"],
            "差额金额": reconciliation["delta_total"],
            "调整方向": reconciliation["adjustment_type"],
            "命中区间": "、".join(
                f"{seg['hit_start']}~{seg['hit_end']}"
                for seg in bill_trace["segments"] if seg["changed"] and seg["hit_start"]
            ),
            "依据": reconciliation["basis"],
            "备注": remark or "",
            "经办人": operator,
            "status": "待复核",
            "pending": True,
            "abnormal": False,
        }
        rows.append(entry)
        return entry, f"差额更正申请 {entry['更正单号']} 已登记，待财务复核"

    def list_corrections(self) -> list[dict[str, Any]]:
        return sorted(
            (dict(row) for row in store.rows(CORRECTION_MODULE)),
            key=lambda row: int(row.get("id", 0)),
            reverse=True,
        )

    def _corrections_for(self, bill_ids: list[Any]) -> list[dict[str, Any]]:
        wanted = {int(value) for value in bill_ids if value is not None}
        return [row for row in self.list_corrections()
                if int(row.get("bill_id", 0)) in wanted]

    def _log_trace(self, response: dict[str, Any]) -> None:
        """每次追溯写一条零金额日志，证明检索没有产生收费。"""
        rows = store.rows(TRACE_LOG_MODULE)
        new_id = max((int(row.get("id", 0)) for row in rows), default=0) + 1
        rows.append({
            "id": new_id,
            "bill_ids": [trace["bill_id"] for trace in response["bills"]],
            "billing_cycle": (response["filters"] or {}).get("billing_cycle"),
            "caller": (response["filters"] or {}).get("caller"),
            "api_name": (response["filters"] or {}).get("api_name"),
            "charge_delta": 0.0,
            "regenerated_bills": 0,
            "readonly": True,
        })
