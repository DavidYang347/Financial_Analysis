<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { strategyApi, type BacktestRecord, type BacktestReport, type ExtraTable } from '@/api'
import { errorText } from '@/api/http'
import { fmtTime } from '@/utils/format'
import { STAT_LABELS, TABLES, fmt, signOf, type Col } from '../labels'

echarts.use([LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])

const STRATEGY = 'high_odds_v3'
const route = useRoute()
const router = useRouter()

const runs = ref<BacktestRecord[]>([])
const runId = ref<string>('')
const report = ref<BacktestReport | null>(null)
const error = ref('')
const pools = ref<Record<string, unknown>[]>([])
const stats = ref<Record<string, unknown>[]>([])

const tab = ref('x_journal')
const table = ref<ExtraTable | null>(null)
const tableLoading = ref(false)
const search = ref('')
const page = ref(1)
const pageSize = 100
const sortKey = ref<string | undefined>(undefined)
const sortDesc = ref(true)

const cols = computed<Col[]>(() => TABLES[tab.value]?.cols ?? [])

async function loadRuns() {
  try {
    runs.value = (await strategyApi.history(STRATEGY, 100)).items.filter((r) => r.status === 'ok')
    const want = (route.params.runId as string) || runs.value[0]?.run_id || ''
    if (want && want !== runId.value) runId.value = want
    if (!runs.value.length) error.value = '还没有高赔率策略的回测：先在“策略回测”里运行“高赔率低频基本面 v3”'
  } catch (e) {
    error.value = errorText(e)
  }
}

async function loadRun() {
  if (!runId.value) return
  error.value = ''
  try {
    const [rep, p, s] = await Promise.all([
      strategyApi.report(runId.value),
      strategyApi.extra(runId.value, 'x_pools', { limit: 5000, sort: 'date', desc: false }),
      strategyApi.extra(runId.value, 'x_stats', { limit: 100 }),
    ])
    report.value = rep
    pools.value = p.items
    stats.value = s.items
    renderChart()
  } catch (e) {
    error.value = errorText(e)
  }
  page.value = 1
  await loadTable()
}

async function loadTable() {
  if (!runId.value) return
  tableLoading.value = true
  try {
    table.value = await strategyApi.extra(runId.value, tab.value, {
      search: search.value || undefined, offset: (page.value - 1) * pageSize, limit: pageSize,
      sort: sortKey.value ?? TABLES[tab.value]?.sort, desc: sortDesc.value,
    })
  } catch (e) {
    table.value = null
    error.value = errorText(e)
  } finally {
    tableLoading.value = false
  }
}

function onSort(s: { prop: string; order: 'ascending' | 'descending' | null }) {
  sortKey.value = s.order ? s.prop : undefined
  sortDesc.value = s.order !== 'ascending'
  page.value = 1
  loadTable()
}

watch(runId, (id) => {
  if (id && route.params.runId !== id) router.replace({ name: 'highodds-run', params: { runId: id } })
  loadRun()
})
watch(tab, () => {
  page.value = 1
  sortKey.value = undefined
  sortDesc.value = true
  search.value = ''
  loadTable()
})
let timer: number | undefined
watch(search, () => {
  window.clearTimeout(timer)
  timer = window.setTimeout(() => { page.value = 1; loadTable() }, 300)
})

const m = computed(() => report.value?.metrics)
const headline = computed(() => {
  const x = m.value
  if (!x) return []
  const p = (v: unknown) => fmt(v, 'pct')
  const closed = stats.value.find((r) => r.group === 'all')
  return [
    { k: '总收益', v: p(x.total_return), c: signOf(x.total_return, 'pct') },
    { k: '基准', v: p(x.benchmark_return), c: signOf(x.benchmark_return, 'pct') },
    { k: '最大回撤', v: p(x.max_drawdown), c: 'down' },
    { k: '夏普', v: fmt(x.sharpe, 'num') },
    { k: '平仓笔数', v: closed ? fmt(closed.trades, 'int') : '–' },
    { k: '胜率', v: closed ? p(closed.win_rate) : '–' },
    { k: '盈亏比', v: closed ? fmt(closed.payoff, 'num') : '–' },
    { k: '每笔期望', v: closed ? p(closed.expectancy) : '–', c: closed ? signOf(closed.expectancy, 'pct') : '' },
  ]
})
const statRows = computed(() => stats.value.filter((r) => r.group !== 'all'))

// ---- pool chart ------------------------------------------------------------------
const chartEl = ref<HTMLDivElement>()
const chart = shallowRef<echarts.ECharts>()
const POOL_SERIES = [
  { key: 'watch', name: '观察池', color: '#7a8699' },
  { key: 'ammo', name: '弹药池', color: '#2b4c7e' },
  { key: 'holdings', name: '持仓', color: '#d9363e' },
]

function renderChart() {
  if (!chartEl.value) return
  if (!chart.value) chart.value = echarts.init(chartEl.value)
  const x = pools.value.map((r) => String(r.date).slice(0, 10))
  chart.value.setOption({
    animation: false,
    textStyle: { fontFamily: getComputedStyle(document.body).fontFamily },
    legend: { top: 0, left: 8, itemWidth: 16, itemHeight: 2 },
    tooltip: { trigger: 'axis', backgroundColor: '#fff', borderColor: '#d5dbe3', textStyle: { color: '#1b2430', fontSize: 12 } },
    grid: { left: 40, right: 16, top: 28, bottom: 24 },
    xAxis: { type: 'category', data: x, axisLabel: { color: '#5b6677' } },
    yAxis: { type: 'value', minInterval: 1, axisLabel: { color: '#5b6677' }, splitLine: { lineStyle: { color: '#edf0f4' } } },
    series: POOL_SERIES.map((s) => ({
      name: s.name, type: 'line', step: 'end', showSymbol: false, lineStyle: { width: 1.5, color: s.color },
      itemStyle: { color: s.color }, data: pools.value.map((r) => r[s.key]),
    })),
  }, true)
}
const onResize = () => chart.value?.resize()
onMounted(async () => {
  window.addEventListener('resize', onResize)
  await loadRuns()
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  chart.value?.dispose()
})
</script>

<template>
  <div class="page">
    <section class="sheet">
      <div class="sheet-head">
        <h2>高赔率观察</h2>
        <span class="muted">四级池子、赔率卡、决策日志和按通道 / 母题的统计</span>
        <div class="spacer" />
        <el-select v-model="runId" class="rsel" placeholder="选择回测" aria-label="选择回测">
          <el-option v-for="r in runs" :key="r.run_id" :value="r.run_id"
                     :label="`${fmtTime(r.started_at)}  ${r.settings.start} ~ ${r.settings.end}`" />
        </el-select>
        <RouterLink v-if="runId" :to="{ name: 'backtest-report', params: { runId } }">完整回测报告</RouterLink>
      </div>
      <el-alert v-if="error" :title="error" type="warning" :closable="false" show-icon class="err" />
      <template v-if="m">
        <div class="headline">
          <div v-for="h in headline" :key="h.k" class="kpi">
            <span class="kk">{{ h.k }}</span>
            <span class="kv" :class="h.c">{{ h.v }}</span>
          </div>
        </div>
      </template>
      <div class="grid2">
        <div class="cell">
          <h3>池子规模（每周）</h3>
          <div ref="chartEl" class="pchart" role="img" aria-label="观察池、弹药池和持仓数量随时间变化" />
        </div>
        <div class="cell">
          <h3>按通道 / 母题（已平仓）</h3>
          <el-table :data="statRows" size="small" max-height="260">
            <el-table-column label="分组" width="120">
              <template #default="{ row }">{{ row.group === 'channel' ? `通道 ${row.name}` : row.name }}</template>
            </el-table-column>
            <el-table-column v-for="k in ['trades', 'win_rate', 'payoff', 'expectancy', 'pnl']" :key="k"
                             :label="STAT_LABELS[k]" align="right" min-width="76">
              <template #default="{ row }">
                <span :class="k === 'expectancy' || k === 'pnl' ? signOf(row[k], k === 'pnl' ? 'big' : 'pct') : ''">
                  {{ fmt(row[k], k === 'trades' ? 'int' : k === 'payoff' ? 'num' : k === 'pnl' ? 'big' : 'pct') }}
                </span>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </div>
    </section>

    <section v-if="runId" class="sheet">
      <el-tabs v-model="tab" class="tabs">
        <el-tab-pane v-for="(t, k) in TABLES" :key="k" :label="t.title" :name="k" />
      </el-tabs>
      <div class="tbar">
        <span class="muted">{{ TABLES[tab]?.help }}</span>
        <div class="spacer" />
        <el-input v-model="search" placeholder="搜索代码、名称、动作、原因" clearable class="tsearch" aria-label="搜索表格" />
      </div>
      <el-table v-loading="tableLoading" :data="table?.items ?? []" size="small" max-height="620"
                @sort-change="onSort">
        <el-table-column v-for="c in cols" :key="c.key" :prop="c.key" :label="c.label" :min-width="c.width ?? 90"
                         :align="c.fmt && c.fmt !== 'text' && c.fmt !== 'date' ? 'right' : 'left'" sortable="custom"
                         show-overflow-tooltip>
          <template #default="{ row }">
            <RouterLink v-if="c.key === 'symbol' && row.symbol" :to="{ name: 'data-kline', query: { symbol: row.symbol } }">
              {{ row.symbol }}</RouterLink>
            <span v-else :class="signOf(row[c.key], c.fmt)">{{ fmt(row[c.key], c.fmt) }}</span>
          </template>
        </el-table-column>
        <template #empty><div class="empty">没有记录</div></template>
      </el-table>
      <div class="pager">
        <el-pagination v-model:current-page="page" :page-size="pageSize" :total="table?.total ?? 0"
                       layout="total, prev, pager, next" @current-change="loadTable" />
      </div>
    </section>
  </div>
</template>

<style scoped>
.rsel { width: 340px; }
.err { margin: 12px 16px; width: auto; }
.headline { display: flex; flex-wrap: wrap; border-bottom: 1px solid var(--rule-soft); }
.kpi { display: flex; flex-direction: column; gap: 2px; padding: 12px 20px; min-width: 110px; }
.kpi + .kpi { border-left: 1px solid var(--rule-soft); }
.kk { color: var(--muted); font-size: var(--fs-sm, 12px); }
.kv { font-size: 20px; font-variant-numeric: tabular-nums; }
.grid2 { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
.cell { padding: 12px 16px; min-width: 0; }
.cell + .cell { border-left: 1px solid var(--rule-soft); }
.cell h3 { font-size: var(--fs-md, 14px); margin: 0 0 8px; }
.pchart { height: 260px; }
.tabs { padding: 0 16px; }
.tbar { display: flex; align-items: center; gap: 12px; padding: 0 16px 8px; }
.tsearch { width: 260px; }
.pager { display: flex; justify-content: flex-end; padding: 8px 16px; }
@media (max-width: 1100px) {
  .grid2 { grid-template-columns: 1fr; }
  .cell + .cell { border-left: none; border-top: 1px solid var(--rule-soft); }
}
</style>
