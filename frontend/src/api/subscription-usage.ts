/** 订阅账期用量明细接口：按账期/调用方/API 回查换订阅关联的原账单与命中用量。 */

import { request } from './client'

export type BillSummary = {
  bill_id: number
  record_no: string
  caller: string
  api_name: string
  billing_cycle: string
  bill_status: string
  billed: boolean
  original_amount: number
  original_calls: number
  original_rows: number
  segment_count: number
  change_count: number
  changed: boolean
  hit_start: string
  hit_end: string
}

export type BillPage = {
  items: BillSummary[]
  total: number
  page: number
  size: number
}

export type HitRange = { start: string; end: string; days: number }

export type SubscriptionSegment = {
  subscription_id: number | null
  application_no: string
  package: string
  applicant: string
  api_name: string
  effective_start: string
  effective_end: string
  cycle_start: string
  cycle_end: string
  covered_days: number
  unit_price: number | null
  subscription_status: string
  change_type: string
  changed: boolean
  detail_records: number
  detail_calls: number
  detail_rows: number
  detail_amount: number
  hit_start: string
  hit_end: string
  hit_ranges: HitRange[]
  hit_dates: number
  allocated_amount: number
  recalculated_amount: number
  delta: number
  delta_direction: string
  priced_by: string
}

export type SubscriptionChange = {
  change_id: number | null
  change_no: string
  change_type: string
  change_date: string
  from_subscription: string
  to_subscription: string
  reason: string
  source: string
  applied: boolean
}

export type Reconciliation = {
  basis: string
  original_amount: number
  allocated_total: number
  recalculated_total: number
  delta_total: number
  allocation_gap: number
  adjustment_type: string
  needs_adjustment: boolean
  can_correct: boolean
  blocked_reason: string
  changed_segments: {
    subscription_id: number | null
    application_no: string
    hit_start: string
    hit_end: string
    delta: number
  }[]
}

export type BillTrace = {
  bill_id: number
  record_no: string
  caller: string
  api_name: string
  billing_cycle: string
  bill_status: string
  settlement_status: string
  billed: boolean
  original_amount: number
  original_calls: number
  original_rows: number
  original_amount_note: string
  segments: SubscriptionSegment[]
  changes: SubscriptionChange[]
  reconciliation: Reconciliation
}

export type Correction = {
  id: number
  更正单号: string
  bill_id: number
  记录编号: string
  调用方: string
  API名称: string
  计费周期: string
  原账单金额: number
  重算金额: number
  差额金额: number
  调整方向: string
  命中区间: string
  备注: string
  status: string
}

export type TraceResult = {
  filters: {
    billing_cycle: string | null
    bill_id: number | null
    caller: string | null
    api_name: string | null
  }
  bills: BillTrace[]
  totals: {
    bill_count: number
    original_amount: number
    allocated_total: number
    recalculated_total: number
    delta_total: number
    needs_adjustment_count: number
    correctable_count: number
  }
  corrections: Correction[]
  readonly: boolean
  billing_effect: {
    query_mode: string
    regenerated_bills: number
    duplicate_charge: boolean
    charge_delta: number
    note: string
  }
}

export type TraceFilters = {
  billing_cycle?: string
  bill_id?: number
  caller?: string
  api_name?: string
  changed_only?: boolean
}

function toQuery(filters: TraceFilters): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value === undefined || value === null || value === '' || value === false) continue
    params.set(key, String(value))
  }
  return params.toString()
}

export async function listBills(filters: TraceFilters = {}): Promise<BillPage> {
  const response = await request(`/api/subscription-usage/bills?${toQuery(filters)}`)
  if (!response.ok) throw new Error('订阅账期账单读取失败')
  return (await response.json()) as BillPage
}

export async function traceBills(filters: TraceFilters): Promise<TraceResult> {
  const response = await request(`/api/subscription-usage/trace?${toQuery(filters)}`)
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}))
    throw new Error(detail.detail ?? '换订阅明细查询失败')
  }
  return (await response.json()) as TraceResult
}

export async function listCorrections(): Promise<{ items: Correction[]; total: number }> {
  const response = await request('/api/subscription-usage/corrections')
  if (!response.ok) throw new Error('差额更正申请读取失败')
  return await response.json()
}

export async function createCorrection(
  billId: number,
  remark: string,
): Promise<{ ok: boolean; message: string; entry: Correction | null }> {
  const response = await request('/api/subscription-usage/corrections', {
    method: 'POST',
    body: JSON.stringify({ bill_id: billId, remark }),
  })
  return await response.json()
}
