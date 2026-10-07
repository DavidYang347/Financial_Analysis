<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { strategyApi, type BacktestMetrics, type BacktestReport } from '@/api'
import { errorText } from '@/api/http'
import { fmtBig, fmtTime } from '@/utils/format'
import NavChart, { type NavSeries } from '../NavChart.vue'
import { SERIES_COLORS, num, paramText, pct, settingsText, sign } from '../labels'

const route = useRoute()
const reports = ref<BacktestReport[]>([])
const error = ref('')
const loading = ref(false)
const logScale = ref(false)

function label(r: BacktestReport, i: number): string {
  return `#${i + 1} ${r.strategy_name}`
}

const series = computed<NavSeries[]>(() =>
  reports.value.map((r, i) => ({
    name: label(r, i),
    nav: r.equity.map((x) => [x.date, x.nav]),
    drawdown: r.equity.map((x) => [x.date, x.drawdown]),
    color: SERIES_COLORS[i % SERIES_COLORS.length],
  })),
)

type Row = { k: string; get: (m: BacktestMetrics) => string; val?: (m: BacktestMetrics) => number | null | undefined; better?: 'high' | 'low' }
const ROWS: Row[] = [
  { k: '总收益', get: (m) => pct(m.total_return), val: (m) => m.total_return, better: 'high' },
  { k: '年化收益', get: (m) => pct(m.annual_return), val: (m) => m.annual_return, better: 'high' },
  { k: '超额收益', get: (m) => pct(m.excess_return), val: (m) => m.excess_return, better: 'high' },
  { k: '最大回撤', get: (m) => pct(m.max_drawdown, 2, false), val: (m) => m.max_drawdown, better: 'high' },
  { k: '年化波动率', get: (m) => pct(m.annual_volatility, 2, false), val: (m) => m.annual_volatility, better: 'low' },
  { k: '夏普比率', get: (m) => num(m.sharpe), val: (m) => m.sharpe, better: 'high' },
  { k: '索提诺比率', get: (m) => num(m.sortino), val: (m) => m.sortino, better: 'high' },
  { k: '卡玛比率', get: (m) => num(m.calmar), val: (m) => m.calmar, better: 'high' },
  { k: '信息比率', get: (m) => num(m.information_ratio), val: (m) => m.information_ratio, better: 'high' },
  { k: 'Beta', get: (m) => num(m.beta) },
  { k: '卖出胜率', get: (m) => pct(m.win_rate, 1, false), val: (m) => m.win_rate, better: 'high' },
  { k: '盈亏比', get: (m) => num(m.profit_factor), val: (m) => m.profit_factor, better: 'high' },
  { k: '成交笔数', get: (m) => String(m.trades) },
  { k: '年换手率', get: (m) => (m.annual_turnover !== null ? `${num(m.annual_turnover, 1)} 倍` : '–'), val: (m) => m.annual_turnover, better: 'low' },
  { k: '总费用', get: (m) => fmtBig(m.total_fees) },
  { k: '期末资产', get: (m) => fmtBig(m.final_equity) },
]

function best(row: Row): number {
  if (!row.better || !row.val || reports.value.length < 2) return -1
  let bi = -1
  let bv = 0
  reports.value.forEach((r, i) => {
    const v = row.val!(r.metrics)
    if (v === null || v === undefined || !Number.isFinite(v)) return
    if (bi < 0 || (row.better === 'high' ? v > bv : v < bv)) { bi = i; bv = v }
  })
  return bi
}

const periodsDiffer = computed(() => new Set(reports.value.map((r) => `${r.settings.start}~${r.settings.end}`)).size > 1)

onMounted(async () => {
  const ids = String(route.query.ids ?? '').split(',').filter(Boolean).slice(0, 6)
  if (ids.length < 2) {
    error.value = '至少选两条回测记录来对比'
    return
  }
  loading.value = true
  try {
    reports.value = await Promise.all(ids.map((id) => strategyApi.report(id)))
  } catch (e) {
    error.value = errorText(e)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div v-loading="loading" class="page">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <template v-else-if="reports.length">
      <section class="sheet">
        <div class="sheet-head">
          <RouterLink :to="{ name: 'backtest-history' }">回测记录</RouterLink>
          <span class="muted">/</span>
          <h2>回测对比</h2>
          <span class="muted">{{ reports.length }} 条</span>
          <div class="spacer" />
          <el-checkbox v-model="logScale" size="small">对数坐标</el-checkbox>
        </div>
        <el-alert
          v-if="periodsDiffer"
          title="这几次回测的区间不同，曲线和指标不能直接横向比较"
          type="warning"
          :closable="false"
          show-icon
          class="warn"
        />
        <div class="chart">
          <NavChart :series="series" :log-scale="logScale" height="440px" />
        </div>
      </section>

      <section class="sheet">
        <div class="cwrap">
          <table class="ctable">
            <thead>
              <tr>
                <th scope="col" class="rk">指标</th>
                <th v-for="(r, i) in reports" :key="r.run_id" scope="col">
                  <span class="swatch" :style="{ background: SERIES_COLORS[i % SERIES_COLORS.length] }" />
                  <RouterLink :to="{ name: 'backtest-report', params: { runId: r.run_id } }">{{ label(r, i) }}</RouterLink>
                  <div class="sub muted">{{ fmtTime(r.started_at) }}</div>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in ROWS" :key="row.k">
                <th scope="row" class="rk">{{ row.k }}</th>
                <td
                  v-for="(r, i) in reports"
                  :key="r.run_id"
                  :class="[{ best: best(row) === i }, row.val && ['总收益', '年化收益', '超额收益'].includes(row.k) ? sign(row.val(r.metrics)) : '']"
                >
                  {{ row.get(r.metrics) }}
                </td>
              </tr>
              <tr>
                <th scope="row" class="rk">设置</th>
                <td v-for="r in reports" :key="r.run_id" class="txt">{{ settingsText(r.settings) }}</td>
              </tr>
              <tr>
                <th scope="row" class="rk">参数</th>
                <td v-for="r in reports" :key="r.run_id" class="txt">{{ paramText(r.params) }}</td>
              </tr>
            </tbody>
          </table>
          <p class="muted note">加粗为同一行里表现最好的一项。</p>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.warn {
  margin: 12px 16px 0;
  width: auto;
}

.chart {
  padding: 10px 16px 4px;
}

.cwrap {
  padding: 12px 16px 16px;
  overflow-x: auto;
}

.ctable {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-sm);
}

.ctable th,
.ctable td {
  padding: 7px 12px;
  border-bottom: 1px solid var(--rule-soft);
  text-align: right;
  vertical-align: top;
}

.ctable thead th {
  font-weight: 500;
  border-bottom: 1px solid var(--rule);
}

.ctable .rk {
  text-align: left;
  color: var(--ink-2);
  font-weight: 500;
  white-space: nowrap;
}

.ctable td.best {
  font-weight: 700;
}

.ctable td.txt {
  max-width: 260px;
  color: var(--ink-2);
  font-size: var(--fs-xs);
  text-align: left;
  word-break: break-all;
}

.swatch {
  display: inline-block;
  width: 10px;
  height: 3px;
  margin-right: 6px;
  vertical-align: middle;
}

.sub {
  font-size: var(--fs-xs);
}

.note {
  margin: 8px 0 0;
  font-size: var(--fs-xs);
}
</style>
