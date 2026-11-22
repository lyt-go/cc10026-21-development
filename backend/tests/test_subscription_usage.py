"""换订阅追溯场景测试：同一 API 的两位调用方，其中一位在账期内有两段订阅。

覆盖：
1. 月底账单回查一次实际换订阅，命中区间与订阅生效区间一致；
2. 原账单金额原样保留，检索拆分不产生二次收费；
3. 同一 API 另一位调用方（单段订阅）不会被误判为换订阅；
4. 按调用方筛选能直接定位实际变动；
5. 差额核对结论与更正发起条件。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["DATA_ROOT"] = tempfile.mkdtemp(prefix="cc10026_sub_usage_")

API = "订单明细查询API"
CYCLE = "2026-10"
CALLER_SWITCHED = "陈嘉禾"
CALLER_STEADY = "林墨"


def _reset():
    from app.seed import SEED_ROWS
    from app.store import store

    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}


def _service():
    from app.services.subscription_usage import SubscriptionUsageService

    return SubscriptionUsageService()


def _bill_by_caller(caller):
    from app.store import store

    for row in store.rows("usage_billing"):
        if row.get("调用方") == caller and row.get("计费周期") == CYCLE:
            return row
    raise AssertionError(f"缺少 {caller} 在 {CYCLE} 的账单")


def test_bills_listed_by_cycle_keep_original_amount():
    """按账期只能看到一行合并账单，原金额与调用量保留在账单上。"""
    _reset()
    service = _service()
    items, total = service.list_bills(billing_cycle=CYCLE)
    assert total == 2
    switched = next(item for item in items if item["caller"] == CALLER_SWITCHED)
    steady = next(item for item in items if item["caller"] == CALLER_STEADY)
    assert switched["original_amount"] == 600.0
    assert switched["original_calls"] == 3000
    assert switched["changed"] is True
    assert steady["changed"] is False


def test_trace_by_month_finds_switched_subscription():
    """自月底账单回查一次实际换订阅，命中两段先后生效的订阅区间。"""
    _reset()
    trace = _service().trace(billing_cycle=CYCLE, caller=CALLER_SWITCHED)
    assert len(trace["bills"]) == 1
    bill = trace["bills"][0]
    assert bill["original_amount"] == 600.0
    assert trace["readonly"] is True
    assert trace["billing_effect"]["duplicate_charge"] is False

    segments = [seg for seg in bill["segments"] if seg["subscription_id"] is not None]
    assert len(segments) == 2, "同一账期内应还原出两段先后生效的订阅"
    first, second = segments
    assert (first["application_no"], second["application_no"]) == ("SUBS-0011", "SUBS-0012")
    assert (first["effective_start"], first["effective_end"]) == ("2026-10-01", "2026-10-15")
    assert (second["effective_start"], second["effective_end"]) == ("2026-10-16", "2026-10-31")
    assert (first["hit_start"], first["hit_end"]) == ("2026-10-01", "2026-10-15")
    assert (second["hit_start"], second["hit_end"]) == ("2026-10-16", "2026-10-31")
    assert first["detail_calls"] == 1800
    assert second["detail_calls"] == 1200
    assert first["detail_calls"] + second["detail_calls"] == bill["original_calls"]

    assert len(bill["changes"]) == 1
    change = bill["changes"][0]
    assert change["from_subscription"] == "SUBS-0011"
    assert change["to_subscription"] == "SUBS-0012"
    assert change["change_date"] == "2026-10-16"


def test_trace_is_readonly_and_not_second_charge():
    """打开原账单再查询换订阅明细，不能把检索拆分视为二次收费。"""
    _reset()
    from app.store import store

    bill = _bill_by_caller(CALLER_SWITCHED)
    before_amount = bill["费用金额"]
    before_status = bill["status"]
    before_rows = len(store.rows("usage_billing"))
    before_details = len(store.rows("_usage_detail"))

    service = _service()
    first = service.trace(bill_id=bill["id"])
    second = service.trace(billing_cycle=CYCLE, caller=CALLER_SWITCHED)

    for response in (first, second):
        effect = response["billing_effect"]
        assert effect["regenerated_bills"] == 0
        assert effect["duplicate_charge"] is False
        assert effect["charge_delta"] == 0.0
        for traced in response["bills"]:
            reconciliation = traced["reconciliation"]
            assert reconciliation["allocation_gap"] == 0.0
            assert reconciliation["allocated_total"] == traced["original_amount"]

    assert bill["费用金额"] == before_amount
    assert bill["status"] == before_status
    assert len(store.rows("usage_billing")) == before_rows, "追溯不应新增账单"
    assert len(store.rows("_usage_detail")) == before_details, "追溯不应复制用量记录"

    logs = store.rows("_billing_trace_log")
    assert logs and all(log["charge_delta"] == 0.0 for log in logs)


def test_second_caller_same_api_has_no_switch():
    """同一 API 的另一位调用方只有一段订阅，不应被判为换订阅。"""
    _reset()
    trace = _service().trace(billing_cycle=CYCLE, caller=CALLER_STEADY)
    bill = trace["bills"][0]
    assert bill["original_amount"] == 240.0
    assert bill["changes"] == []
    assert all(not seg["changed"] for seg in bill["segments"])
    assert len([seg for seg in bill["segments"] if seg["subscription_id"] is not None]) == 1
    assert bill["reconciliation"]["needs_adjustment"] is False
    assert bill["reconciliation"]["can_correct"] is False


def test_filter_by_caller_locates_actual_change():
    """按调用方筛选能定位实际变动，不把同一 API 的其他账单算进来。"""
    _reset()
    service = _service()
    items, total = service.list_bills(
        billing_cycle=CYCLE, caller=CALLER_SWITCHED, changed_only=True)
    assert total == 1
    assert items[0]["caller"] == CALLER_SWITCHED
    assert items[0]["change_count"] == 1
    assert (items[0]["hit_start"], items[0]["hit_end"]) == ("2026-10-01", "2026-10-31")

    steady, steady_total = service.list_bills(
        billing_cycle=CYCLE, caller=CALLER_STEADY, changed_only=True)
    assert steady_total == 0 and steady == []

    all_items, all_total = service.list_bills(billing_cycle=CYCLE, changed_only=True)
    assert all_total == 1
    assert all_items[0]["bill_id"] == items[0]["bill_id"]


def test_detail_records_follow_subscription_windows():
    """订阅账期用量明细按订阅申请编号回看命中范围。"""
    _reset()
    service = _service()
    items, total = service.list_details(
        billing_cycle=CYCLE, caller=CALLER_SWITCHED, application_no="SUBS-0012")
    assert total == 16
    assert {item["usage_date"] for item in items} == {
        f"2026-10-{day:02d}" for day in range(16, 32)}
    assert sum(item["call_count"] for item in items) == 1200
    assert all(item["subscription_id"] == 5 for item in items)

    old_items, old_total = service.list_details(
        billing_cycle=CYCLE, caller=CALLER_SWITCHED, application_no="SUBS-0011")
    assert old_total == 15
    assert sum(item["call_count"] for item in old_items) == 1800

    # 全覆盖：原始用量没有落在订阅区间之外。
    leftover = [seg for seg in service.trace(billing_cycle=CYCLE, caller=CALLER_SWITCHED)
                ["bills"][0]["segments"] if seg["subscription_id"] is None]
    assert leftover == []


def test_difference_requires_adjustment_and_correction():
    """差额核对：原账单少收 60 元，满足更正条件才允许用原用量记录发起更正。"""
    _reset()
    service = _service()
    bill = service.trace(billing_cycle=CYCLE, caller=CALLER_SWITCHED)["bills"][0]
    reconciliation = bill["reconciliation"]
    assert reconciliation["original_amount"] == 600.0
    assert reconciliation["recalculated_total"] == 660.0
    assert reconciliation["delta_total"] == -60.0
    assert reconciliation["needs_adjustment"] is True
    assert reconciliation["can_correct"] is True
    assert reconciliation["adjustment_type"] == "差额补收"
    assert reconciliation["changed_segments"][0]["application_no"] == "SUBS-0012"
    assert reconciliation["changed_segments"][0]["hit_start"] == "2026-10-16"

    entry, message = service.create_correction(
        bill["bill_id"], remark="换订阅后单价上调，核对差额")
    assert entry is not None, message
    assert entry["status"] == "待复核"
    assert entry["差额金额"] == -60.0
    assert entry["命中区间"] == "2026-10-16~2026-10-31"
    assert entry["原账单金额"] == 600.0

    # 重复登记不会生成第二张更正单。
    again, again_message = service.create_correction(bill["bill_id"])
    assert again["更正单号"] == entry["更正单号"]
    assert "未重复登记" in again_message

    # 无需调整的账单不能发起更正。
    steady = service.trace(billing_cycle=CYCLE, caller=CALLER_STEADY)["bills"][0]
    blocked, blocked_message = service.create_correction(steady["bill_id"])
    assert blocked is None
    assert "无需差额调整" in blocked_message

    # 更正只是登记申请，原账单金额仍未改动。
    from app.store import store

    bill_row = store.find("usage_billing", bill["bill_id"])
    assert bill_row["费用金额"] == 600.0


def test_api_endpoints_expose_trace_and_corrections():
    """HTTP 接口回传同样的追溯结果，非法参数返回 4xx。"""
    _reset()
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    response = client.get("/api/subscription-usage/trace",
                          params={"billing_cycle": CYCLE, "caller": CALLER_SWITCHED})
    assert response.status_code == 200
    payload = response.json()
    assert payload["totals"]["bill_count"] == 1
    assert payload["billing_effect"]["duplicate_charge"] is False
    assert payload["bills"][0]["reconciliation"]["original_amount"] == 600.0

    missing = client.get("/api/subscription-usage/trace")
    assert missing.status_code == 422
    unknown = client.get("/api/subscription-usage/trace", params={"bill_id": 999})
    assert unknown.status_code == 404
    bad_cycle = client.get("/api/subscription-usage/bills",
                           params={"billing_cycle": "2026-1"})
    assert bad_cycle.status_code == 422

    created = client.post("/api/subscription-usage/corrections",
                          json={"bill_id": payload["bills"][0]["bill_id"]})
    assert created.status_code == 200
    assert created.json()["ok"] is True
    listed = client.get("/api/subscription-usage/corrections").json()
    assert listed["total"] == 1
