<template>
  <section class="page" data-module="subscription_usage">
    <header class="page-head">
      <div>
        <h2>订阅账期用量明细</h2>
        <p class="page-desc">
          按账期回查已出账账单，结合订阅生效区间还原一次换订阅涉及的用量；
          原账单金额与检索拆分分开呈现，检索本身不产生二次收费。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="reload-corrections" type="button" @click="loadCorrections">
          刷新更正申请
        </button>
      </div>
    </header>

    <form class="filter-bar" data-testid="trace-filters" @submit.prevent="runTrace()">
      <label class="filter-item">
        <span>计费周期</span>
        <input v-model="form.billing_cycle" data-testid="filter-cycle" placeholder="YYYY-MM" />
      </label>
      <label class="filter-item">
        <span>调用方</span>
        <input v-model="form.caller" data-testid="filter-caller" placeholder="按调用方检索" />
      </label>
      <label class="filter-item">
        <span>API名称</span>
        <input v-model="form.api_name" data-testid="filter-api" placeholder="按API名称检索" />
      </label>
      <label class="filter-item checkbox">
        <input v-model="form.changed_only" data-testid="filter-changed-only" type="checkbox" />
        <span>只看换订阅账单</span>
      </label>
      <button class="btn" data-testid="query-bills" type="submit" :disabled="loading">
        {{ loading ? '追溯中…' : '按账期查询账单' }}
      </button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <p v-if="errorMessage" class="error-text" data-testid="trace-error">{{ errorMessage }}</p>

    <aside class="readonly-note" data-testid="readonly-note">
      <strong>只读追溯：</strong>
      原账单金额不因检索拆分而改写；本次查询新增账单 {{ trace?.billing_effect.regenerated_bills ?? 0 }} 笔，
      收费变动 {{ trace?.billing_effect.charge_delta ?? 0 }} 元，不构成二次收费。
    </aside>

    <table class="data-table" data-testid="bill-table">
      <thead>
        <tr>
          <th>原账单号</th>
          <th>计费周期</th>
          <th>调用方</th>
          <th>API名称</th>
          <th>原账单金额</th>
          <th>调用次数</th>
          <th>订阅段</th>
          <th>换订阅</th>
          <th>命中区间</th>
          <th>账单状态</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="bill in bills" :key="bill.bill_id" :data-testid="`bill-${bill.bill_id}`">
          <td>{{ bill.record_no }}</td>
          <td>{{ bill.billing_cycle }}</td>
          <td>{{ bill.caller }}</td>
          <td>{{ bill.api_name }}</td>
          <td>{{ money(bill.original_amount) }}</td>
          <td>{{ bill.original_calls }}</td>
          <td>{{ bill.segment_count }}</td>
          <td>
            <span v-if="bill.changed" class="tag warn">换订阅 {{ bill.change_count }}</span>
            <span v-else class="tag">无变更</span>
          </td>
          <td>{{ bill.hit_start && bill.hit_end ? `${bill.hit_start} ~ ${bill.hit_end}` : '—' }}</td>
          <td>{{ bill.bill_status }}</td>
          <td class="row-actions">
            <button class="link" type="button" :data-testid="`open-bill-${bill.bill_id}`" @click="runTrace(bill.bill_id)">
              打开原账单
            </button>
            <button class="link" type="button" :data-testid="`open-caller-${bill.bill_id}`" @click="openCaller(bill)">
              按调用方定位
            </button>
          </td>
        </tr>
        <tr v-if="!bills.length">
          <td colspan="11" class="empty-state">暂无账单，请先输入账期查询</td>
        </tr>
      </tbody>
    </table>

    <section v-if="trace" class="trace-panel">
      <header class="trace-head">
        <h3>换订阅明细</h3>
        <p class="page-desc">
          核对依据：{{ trace.bills[0]?.reconciliation.basis }}；
          原账单金额 {{ money(trace.totals.original_amount) }} 元，
          分摊合计 {{ money(trace.totals.allocated_total) }} 元，
          按订阅单价重算 {{ money(trace.totals.recalculated_total) }} 元。
        </p>
      </header>

      <article
        v-for="bill in trace.bills"
        :key="`trace-${bill.bill_id}`"
        class="trace-bill"
        :data-testid="`trace-bill-${bill.bill_id}`"
      >
        <div class="trace-bill-head">
          <div>
            <strong>{{ bill.record_no }}</strong>
            <span class="muted">（{{ bill.caller }} / {{ bill.api_name }} / {{ bill.billing_cycle }}）</span>
          </div>
          <div>
            <span class="tag">原账单金额 {{ money(bill.original_amount) }}</span>
            <span class="tag" :class="{ warn: bill.reconciliation.needs_adjustment }">
              差额 {{ money(bill.reconciliation.delta_total) }}
            </span>
            <span class="tag" :class="{ warn: bill.reconciliation.can_correct, ok: !bill.reconciliation.needs_adjustment }">
              {{ bill.reconciliation.needs_adjustment ? bill.reconciliation.adjustment_type : '无需调整' }}
            </span>
          </div>
        </div>

        <div v-if="bill.changes.length" class="change-list">
          <div v-for="change in bill.changes" :key="`${bill.bill_id}-${change.change_no || change.change_date}`" class="change-item">
            <span class="tag warn">{{ change.change_type }}</span>
            {{ change.change_date }}：{{ change.from_subscription }} → {{ change.to_subscription }}
            <span class="muted">（{{ change.source }}<template v-if="change.reason">，{{ change.reason }}</template>）</span>
          </div>
        </div>
        <p v-else class="muted">该账单在账期内没有订阅变更记录。</p>

        <table class="data-table" :data-testid="`segment-table-${bill.bill_id}`">
          <thead>
            <tr>
              <th>订阅申请</th>
              <th>套餐</th>
              <th>订阅生效区间</th>
              <th>命中区间</th>
              <th>命中调用量</th>
              <th>原始用量金额</th>
              <th>分摊原账单</th>
              <th>按订阅单价重算</th>
              <th>差额</th>
              <th>是否换订阅</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="segment in bill.segments"
              :key="`${bill.bill_id}-${segment.application_no || segment.package}`"
              :data-testid="`segment-${bill.bill_id}-${segment.application_no || 'na'}`"
            >
              <td>{{ segment.application_no || '未匹配订阅' }}</td>
              <td>{{ segment.package || '—' }}</td>
              <td>
                {{ segment.effective_start || '—' }} ~ {{ segment.effective_end || '—' }}
                <span class="muted">（账期内 {{ segment.cycle_start }} ~ {{ segment.cycle_end }}）</span>
              </td>
              <td>
                <template v-if="segment.hit_ranges.length">
                  <div v-for="range in segment.hit_ranges" :key="range.start">
                    {{ range.start }} ~ {{ range.end }}（{{ range.days }} 天）
                  </div>
                </template>
                <template v-else>—</template>
              </td>
              <td>{{ segment.detail_calls }}</td>
              <td>{{ money(segment.detail_amount) }}</td>
              <td>{{ money(segment.allocated_amount) }}</td>
              <td>
                {{ money(segment.recalculated_amount) }}
                <span class="muted">{{ segment.priced_by }}</span>
              </td>
              <td :class="{ 'delta-alert': Math.abs(segment.delta) >= 0.01 }">{{ money(segment.delta) }}</td>
              <td>
                <span v-if="segment.changed" class="tag warn">是</span>
                <span v-else class="tag">否</span>
              </td>
            </tr>
          </tbody>
        </table>

        <div class="trace-foot">
          <span>
            分摊合计 {{ money(bill.reconciliation.allocated_total) }} 元，
            原账单 {{ money(bill.reconciliation.original_amount) }} 元，
            分摊差额 {{ money(bill.reconciliation.allocation_gap) }} 元。
          </span>
          <span v-if="bill.reconciliation.blocked_reason" class="muted">
            {{ bill.reconciliation.blocked_reason }}
          </span>
          <span v-else class="muted">可依据原用量记录发起差额更正。</span>
          <button
            class="btn primary"
            type="button"
            :disabled="!bill.reconciliation.can_correct"
            :data-testid="`create-correction-${bill.bill_id}`"
            @click="submitCorrection(bill)"
          >
            发起差额更正
          </button>
        </div>
      </article>

      <p v-if="!trace.bills.length" class="empty-state">没有符合条件的账单。</p>
    </section>

    <section class="trace-panel">
      <header class="trace-head">
        <h3>差额更正申请</h3>
        <p class="page-desc">更正申请只登记核对结论，不会直接改写原账单或另出一笔账单。</p>
      </header>
      <table class="data-table" data-testid="correction-table">
        <thead>
          <tr>
            <th>更正单号</th>
            <th>原账单</th>
            <th>调用方</th>
            <th>计费周期</th>
            <th>原账单金额</th>
            <th>重算金额</th>
            <th>差额</th>
            <th>调整方向</th>
            <th>命中区间</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in corrections" :key="item.更正单号">
            <td>{{ item.更正单号 }}</td>
            <td>{{ item.记录编号 }}</td>
            <td>{{ item.调用方 }}</td>
            <td>{{ item.计费周期 }}</td>
            <td>{{ money(item.原账单金额) }}</td>
            <td>{{ money(item.重算金额) }}</td>
            <td :class="{ 'delta-alert': Math.abs(item.差额金额) >= 0.01 }">{{ money(item.差额金额) }}</td>
            <td>{{ item.调整方向 }}</td>
            <td>{{ item.命中区间 || '—' }}</td>
            <td>{{ item.status }}</td>
          </tr>
          <tr v-if="!corrections.length">
            <td colspan="10" class="empty-state">暂无差额更正申请</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>{{ trace ? `本次追溯 ${trace.totals.bill_count} 张账单` : '尚未追溯' }}</span>
      <span data-testid="action-message">{{ actionMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import {
  createCorrection,
  listBills,
  listCorrections,
  traceBills,
} from '@/api/subscription-usage'
import type { BillSummary, BillTrace, Correction, TraceResult } from '@/api/subscription-usage'

const DEFAULT_CYCLE = '2026-10'

const form = reactive({
  billing_cycle: DEFAULT_CYCLE,
  caller: '',
  api_name: '',
  changed_only: false,
})
const bills = ref<BillSummary[]>([])
const trace = ref<TraceResult | null>(null)
const corrections = ref<Correction[]>([])
const errorMessage = ref('')
const actionMessage = ref('')
const loading = ref(false)

function money(value: number | null | undefined): string {
  const amount = Number(value ?? 0)
  return amount.toFixed(2)
}

async function runTrace(billId?: number) {
  errorMessage.value = ''
  actionMessage.value = ''
  loading.value = true
  try {
    const payload = await traceBills(
      billId
        ? { bill_id: billId }
        : {
            billing_cycle: form.billing_cycle,
            caller: form.caller,
            api_name: form.api_name,
          },
    )
    trace.value = payload
    corrections.value = payload.corrections
    if (billId) {
      // 按原账单回查时，列表保持为账期内全部账单，避免把检索当成新账单。
      await loadBills()
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '换订阅明细查询失败'
  } finally {
    loading.value = false
  }
}

async function loadBills() {
  try {
    const payload = await listBills({
      billing_cycle: form.billing_cycle,
      caller: form.caller,
      api_name: form.api_name,
      changed_only: form.changed_only,
    })
    bills.value = payload.items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '账单读取失败'
  }
}

async function loadCorrections() {
  try {
    const payload = await listCorrections()
    corrections.value = payload.items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '更正申请读取失败'
  }
}

async function openCaller(bill: BillSummary) {
  form.billing_cycle = bill.billing_cycle
  form.caller = bill.caller
  form.api_name = bill.api_name
  await loadBills()
  await runTrace()
}

async function submitCorrection(bill: BillTrace) {
  actionMessage.value = ''
  errorMessage.value = ''
  try {
    const result = await createCorrection(bill.bill_id, '订阅账期用量明细核对后发起')
    actionMessage.value = result.message
    if (!result.ok) errorMessage.value = result.message
    await loadCorrections()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '差额更正发起失败'
  }
}

async function resetFilters() {
  form.billing_cycle = DEFAULT_CYCLE
  form.caller = ''
  form.api_name = ''
  form.changed_only = false
  trace.value = null
  await loadBills()
}

onMounted(async () => {
  await loadBills()
  await loadCorrections()
})
</script>

<style scoped>
.readonly-note {
  border: 1px solid #bfdbfe;
  background: #eff6ff;
  color: #1e3a8a;
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 13px;
  margin-bottom: 12px;
}
.filter-item.checkbox {
  flex-direction: row;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--muted);
}
.trace-panel {
  margin-top: 18px;
}
.trace-head h3 {
  margin: 0 0 4px;
  font-size: 15px;
}
.trace-bill {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
}
.trace-bill-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.tag {
  display: inline-block;
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 1px 8px;
  font-size: 12px;
  margin-left: 4px;
  color: var(--muted);
}
.tag.warn {
  border-color: #fca5a5;
  background: #fef2f2;
  color: #b42318;
}
.tag.ok {
  border-color: #bbf7d0;
  background: #f0fdf4;
  color: #15803d;
}
.change-list {
  margin: 6px 0;
  font-size: 13px;
}
.change-item {
  padding: 4px 0;
}
.muted {
  color: var(--muted);
  font-size: 12px;
  margin-left: 4px;
}
.delta-alert {
  color: #b42318;
  font-weight: 600;
}
.trace-foot {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 12px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--muted);
}
</style>
