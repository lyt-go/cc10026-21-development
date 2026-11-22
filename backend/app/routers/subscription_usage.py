"""订阅账期用量明细接口：按换订阅回查原账单、命中区间与差额。

只读追溯接口不挂在通用资源路由下：通用 `/api/{module}` 只能按模块清单翻页，
而这里要按账期、调用方、API 和原账单一起定位，并返回账单与订阅段的关联结果。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult
from app.services.subscription_usage import SubscriptionUsageService

router = APIRouter(prefix="/api/subscription-usage", tags=["订阅账期用量明细"])
service = SubscriptionUsageService()


@router.get("/bills")
def list_bills(
    billing_cycle: str | None = Query(default=None, description="计费周期，格式 YYYY-MM"),
    caller: str | None = Query(default=None, description="按调用方筛选"),
    api_name: str | None = Query(default=None, description="按 API 名称筛选"),
    changed_only: bool = Query(default=False, description="只看发生过换订阅的账单"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    try:
        items, total = service.list_bills(
            billing_cycle=billing_cycle, caller=caller, api_name=api_name,
            changed_only=changed_only, page=page, size=size)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"items": items, "total": total, "page": page, "size": size}


@router.get("/details")
def list_details(
    billing_cycle: str | None = Query(default=None),
    caller: str | None = Query(default=None),
    api_name: str | None = Query(default=None),
    application_no: str | None = Query(default=None, description="按订阅申请编号定位"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    items, total = service.list_details(
        billing_cycle=billing_cycle, caller=caller, api_name=api_name,
        application_no=application_no, page=page, size=size)
    return {"items": items, "total": total, "page": page, "size": size}


@router.get("/changes")
def list_changes(
    billing_cycle: str | None = Query(default=None),
    caller: str | None = Query(default=None),
    api_name: str | None = Query(default=None),
) -> dict[str, Any]:
    items = service.list_changes(
        billing_cycle=billing_cycle, caller=caller, api_name=api_name)
    return {"items": items, "total": len(items)}


@router.get("/trace")
def trace_bills(
    billing_cycle: str | None = Query(default=None),
    bill_id: int | None = Query(default=None, description="打开原账单后按账单编号回查"),
    caller: str | None = Query(default=None),
    api_name: str | None = Query(default=None),
) -> dict[str, Any]:
    if bill_id is None and not billing_cycle:
        raise HTTPException(status_code=422, detail="请提供 billing_cycle 或 bill_id")
    try:
        return service.trace(
            billing_cycle=billing_cycle, bill_id=bill_id,
            caller=caller, api_name=api_name)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/corrections")
def list_corrections() -> dict[str, Any]:
    items = service.list_corrections()
    return {"items": items, "total": len(items)}


@router.post("/corrections", response_model=ActionResult)
def create_correction(payload: dict[str, Any]) -> ActionResult:
    bill_id = payload.get("bill_id")
    try:
        bill_id = int(bill_id)
    except (TypeError, ValueError):
        return ActionResult(ok=False, message="缺少有效的原账单编号 bill_id")
    entry, message = service.create_correction(
        bill_id, remark=payload.get("remark"), operator=payload.get("operator") or "数据工程组")
    return ActionResult(ok=entry is not None, message=message, entry=entry)
