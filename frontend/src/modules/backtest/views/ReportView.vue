<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { strategyApi, type BacktestReport, type Holding, type Trade } from '@/api'
import { errorText } from '@/api/http'
import { fmtBig, fmtDuration, fmtInt, fmtNum, fmtTime } from '@/utils/format'
import NavChart, { type NavSeries } from '../NavChart.vue'
import { FILL_LABEL, REBALANCE_LABEL, benchmarkLabel, num, pct, sign } from '../labels'

const route = useRoute()
const router = useRouter()
const runId = computed(() => String(route.params.runId))

const r = ref<BacktestReport | null>(null)
const error = ref('')
const tab = ref<'monthly' | 'trades' | 'holdings' | 'logs' | 'settings'>('monthly')
const logScale = ref(false)

// trades
const trades = ref<Trade[]>([])
const tradeTotal = ref(0)
const tradePage = ref(1)
const tradeStatus = ref<'' | 'filled' | 'blocked'>('')
const tradeSymbol = ref('')
const tradesLoading = ref(false)
const PAGE = 100

// holdings
const holdDays = ref<string[]>([])
const holdDate = ref<string | null>(null)
const holdings = ref<Holding[]>([])
const holdCash = ref<number | null>(null)
const holdEquity = ref<number | null>(null)
const holdLoading = ref(false)

const m = computed(() => r.value?.metrics)
const hasBench = computed(() => m.value?.benchmark_return !== undefined && m.value?.benchmark_return !== null)

const series = computed<NavSeries[]>(() => {
  const eq = r.value?.equity ?? []
  const out: NavSeries[] = [{
    name: r.value?.strategy_name ?? '策略',
    nav: eq.map((x) => [x.date, x.nav]),
    drawdown: eq.map((x) => [x.date, x.drawdown]),
  }]
  if (eq.length && eq[0].benchmark_nav !== undefined) {
    out.push({
      name: `基准（${benchmarkLabel(r.value?.settings.benchmark)}）`,
      nav: eq.map((x) => [x.date, x.benchmark_nav as number]),
      drawdown: eq.map((x) => [x.date, x.benchmark_drawdown as number]),
      color: '#95a6bf',
      dashed: true,
    })
  }
  return out
})

const headline = computed(() => {
  const x = m.value
  if (!x) return []
  return [
    { k: '总收益', v: pct(x.total_return), c: sign(x.total_return) },
    { k: '年化收益', v: pct(x.annual_return), c: sign(x.annual_return) },
    ...(hasBench.value ? [{ k: '超额收益', v: pct(x.excess_return), c: sign(x.excess_return) }] : []),
    { k: '最大回撤', v: pct(x.max_drawdown, 2, false), c: '' },
    { k: '夏普比率', v: num(x.sharpe), c: '' },
    { k: '期末资产', v: fmtBig(x.final_equity), c: '' },
  ]
})

const detailGroups = computed(() => {
  const x = m.value
  if (!x) return []
  const g: { title: string; rows: [string, string, string?][] }[] = [
    { title: '收益与风险', rows: [
      ['年化波动率', pct(x.annual_volatility, 2, false)],
      ['索提诺比率', num(x.sortino)],
      ['卡玛比率', num(x.calmar)],
      ['最大回撤区间', x.max_dd_peak ? `${x.max_dd_peak} → ${x.max_dd_trough}` : '–'],
      ['回撤修复', x.max_dd_peak ? (x.max_dd_recovery ?? '尚未修复') : '–'],
      ['日胜率', pct(x.win_days, 1, false)],
      ['最好 / 最差单日', `${pct(x.best_day)} / ${pct(x.worst_day)}`],
    ] },
  ]
  if (hasBench.value) {
    g.push({ title: '相对基准', rows: [
      ['基准总收益', pct(x.benchmark_return), sign(x.benchmark_return)],
      ['基准年化', pct(x.benchmark_annual_return), sign(x.benchmark_annual_return)],
      ['基准最大回撤', pct(x.benchmark_max_drawdown, 2, false)],
      ['Alpha（年化）', pct(x.alpha), sign(x.alpha)],
      ['Beta', num(x.beta)],
      ['信息比率', num(x.information_ratio)],
      ['跟踪误差', pct(x.tracking_error, 2, false)],
      ['跑赢基准天数', pct(x.win_days_vs_benchmark, 1, false)],
    ] })
  }
  g.push({ title: '交易', rows: [
    ['成交笔数', `${fmtInt(x.trades)}（买 ${fmtInt(x.buys)} / 卖 ${fmtInt(x.sells)}）`],
    ['未成交委托', `${fmtInt(x.blocked_orders)} 笔`],
    ['卖出胜率', pct(x.win_rate, 1, false)],
    ['盈亏比', num(x.profit_factor)],
    ['平均盈利 / 亏损', `${fmtNum(x.avg_win, 0)} / ${fmtNum(x.avg_loss, 0)}`],
    ['总费用', `${fmtNum(x.total_fees, 0)} 元`],
    ['年换手率', x.annual_turnover !== null ? `${num(x.annual_turnover, 1)} 倍` : '–'],
    ['平均持股 / 仓位', `${num(x.avg_positions, 1)} 只 / ${pct(x.avg_exposure, 0, false)}`],
  ] })
  return g
})

/** Rows = years, columns = Jan..Dec + full year. */
const monthly = computed(() => {
  const rows = new Map<number, (number | null)[]>()
  for (const x of r.value?.monthly ?? []) {
    if (!rows.has(x.year)) rows.set(x.year, Array(13).fill(null))
    rows.get(x.year)![x.month === 0 ? 12 : x.month - 1] = x.ret
  }
  return [...rows.entries()].sort((a, b) => b[0] - a[0]).map(([year, v]) => ({ year, v }))
})

function heat(v: number | null): Record<string, string> {
  if (v === null) return {}
  const a = Math.min(Math.abs(v) / 0.15, 1) * 0.55 + 0.06
  return { background: v >= 0 ? `rgba(217,54,62,${a})` : `rgba(30,158,90,${a})` }
}

async function load() {
  error.value = ''
  try {
    r.value = await strategyApi.report(runId.value)
    if (r.value.status !== 'ok') tab.value = 'settings'
  } catch (e) {
    error.value = errorText(e)
  }
}

async function loadTrades() {
  tradesLoading.value = true
  try {
    const t = await strategyApi.trades(runId.value, {
      status: tradeStatus.value || undefined, symbol: tradeSymbol.value.trim() || undefined,
      offset: (tradePage.value - 1) * PAGE, limit: PAGE,
    })
    trades.value = t.items
    tradeTotal.value = t.total
  } finally {
    tradesLoading.value = false
  }
}

async function loadHoldings(date?: string | null) {
  holdLoading.value = true
  try {
    const h = await strategyApi.holdings(runId.value, date ?? undefined)
    holdDays.value = h.days
    holdDate.value = h.date
    holdings.value = h.items
    holdCash.value = h.cash
    holdEquity.value = h.equity
  } finally {
    holdLoading.value = false
  }
}

const holdIdx = computed({
  get: () => Math.max(0, holdDays.value.indexOf(holdDate.value ?? '')),
  set: (i: number) => loadHoldings(holdDays.value[i]),
})

function stepHold(d: number) {
  const i = Math.min(Math.max(holdIdx.value + d, 0), holdDays.value.length - 1)
  holdIdx.value = i
}

function rerun() {
  if (r.value) router.push({ name: 'backtest-new', query: { strategy: r.value.strategy_id, from: r.value.run_id } })
}

watch(tab, (t) => {
  if (t === 'trades' && !trades.value.length) loadTrades()
  if (t === 'holdings' && !holdDays.value.length) loadHoldings()
})
watch([tradeStatus], () => { tradePage.value = 1; loadTrades() })
watch(tradePage, loadTrades)
watch(runId, () => {
  trades.value = []
  holdDays.value = []
  load()
})
onMounted(load)
</script>

<template>
  <div class="page">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />

    <template v-else-if="r">
      <section class="sheet">
        <div class="sheet-head">
          <RouterLink :to="{ name: 'backtest-history' }">回测记录</RouterLink>
          <span class="muted">/</span>
          <h2>{{ r.strategy_name }}</h2>
          <span class="muted">{{ r.settings.start }} ~ {{ r.settings.end }}，{{ fmtTime(r.started_at) }} 运行，用时 {{ fmtDuration(r.elapsed_sec) }}</span>
          <div class="spacer" />
          <RouterLink :to="{ name: 'strategy', params: { id: r.strategy_id } }">策略说明</RouterLink>
          <el-button @click="rerun">调整参数再跑</el-button>
        </div>

        <el-alert
          v-if="r.status !== 'ok'"
          :title="r.status === 'cancelled' ? '这次回测已取消' : `回测出错：${r.error}`"
          :type="r.status === 'cancelled' ? 'info' : 'error'"
          :closable="false"
          show-icon
          class="err"
        >
          <pre v-if="r.trace" class="log">{{ r.trace }}</pre>
        </el-alert>

        <template v-if="m">
          <div class="headline">
            <div v-for="h in headline" :key="h.k" class="kpi">
              <span class="kk">{{ h.k }}</span>
              <span class="kv" :class="h.c">{{ h.v }}</span>
            </div>
          </div>

          <div class="chart">
            <div class="chart-tools">
              <span class="muted">净值（起点 1.00）与回撤</span>
              <div class="spacer" />
              <el-checkbox v-model="logScale" size="small">对数坐标</el-checkbox>
            </div>
            <NavChart :series="series" :log-scale="logScale" />
          </div>

          <div class="details">
            <div v-for="g in detailGroups" :key="g.title" class="dgroup">
              <h3>{{ g.title }}</h3>
              <dl>
                <template v-for="row in g.rows" :key="row[0]">
                  <dt>{{ row[0] }}</dt>
                  <dd :class="row[2]">{{ row[1] }}</dd>
                </template>
              </dl>
            </div>
          </div>
        </template>
      </section>

      <section class="sheet">
        <el-tabs v-model="tab" class="rtabs">
          <el-tab-pane v-if="m" label="月度收益" name="monthly">
            <div class="mwrap">
              <table class="mtable">
                <thead>
                  <tr>
                    <th scope="col">年份</th>
                    <th v-for="i in 12" :key="i" scope="col">{{ i }}月</th>
                    <th scope="col">全年</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in monthly" :key="row.year">
                    <th scope="row">{{ row.year }}</th>
                    <td v-for="(v, i) in row.v" :key="i" :style="heat(v)" :class="{ year: i === 12 }">
                      {{ v === null ? '' : (v * 100).toFixed(1) }}
                    </td>
                  </tr>
                </tbody>
              </table>
              <p class="muted note">单位 %。红色为盈利，绿色为亏损；首尾月份按回测区间内的部分计算。</p>
            </div>
          </el-tab-pane>

          <el-tab-pane v-if="m" :label="`成交明细 ${fmtInt(m.trades + m.blocked_orders)}`" name="trades">
            <div class="tbar">
              <el-radio-group v-model="tradeStatus" size="small">
                <el-radio-button value="">全部</el-radio-button>
                <el-radio-button value="filled">已成交</el-radio-button>
                <el-radio-button value="blocked">未成交</el-radio-button>
              </el-radio-group>
              <el-input
                v-model="tradeSymbol"
                size="small"
                placeholder="代码或名称"
                clearable
                class="tsearch"
                aria-label="按股票过滤成交"
                @keyup.enter="tradePage = 1; loadTrades()"
                @clear="tradePage = 1; loadTrades()"
              />
              <el-button size="small" @click="tradePage = 1; loadTrades()">查询</el-button>
              <div class="spacer" />
              <el-button size="small" tag="a" :href="strategyApi.tradesCsvUrl(r.run_id)" download>导出 CSV</el-button>
            </div>
            <el-table v-loading="tradesLoading" :data="trades" size="small" max-height="560">
              <el-table-column prop="date" label="日期" width="110" />
              <el-table-column label="股票" min-width="150">
                <template #default="{ row }">
                  <RouterLink :to="{ name: 'data-kline', query: { symbol: row.symbol } }">{{ row.symbol }}</RouterLink>
                  <span class="muted sname">{{ row.name }}</span>
                </template>
              </el-table-column>
              <el-table-column label="方向" width="70">
                <template #default="{ row }">
                  <span :class="row.side === 'buy' ? 'up' : 'down'">{{ row.side === 'buy' ? '买入' : '卖出' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="数量" width="100" align="right">
                <template #default="{ row }">{{ row.status === 'filled' ? fmtInt(row.shares) : '–' }}</template>
              </el-table-column>
              <el-table-column label="价格" width="90" align="right">
                <template #default="{ row }">{{ fmtNum(row.price) }}</template>
              </el-table-column>
              <el-table-column label="金额" width="110" align="right">
                <template #default="{ row }">{{ row.status === 'filled' ? fmtNum(row.amount, 0) : '–' }}</template>
              </el-table-column>
              <el-table-column label="费用" width="90" align="right">
                <template #default="{ row }">{{ row.status === 'filled' ? fmtNum(row.commission + row.tax) : '–' }}</template>
              </el-table-column>
              <el-table-column label="平仓盈亏" width="110" align="right">
                <template #default="{ row }">
                  <span :class="sign(row.pnl)">{{ row.pnl === null ? '' : fmtNum(row.pnl, 0) }}</span>
                </template>
              </el-table-column>
              <el-table-column label="状态" min-width="200">
                <template #default="{ row }">
                  <span v-if="row.status === 'filled'" class="muted">{{ row.reason || '成交' }}</span>
                  <el-tag v-else type="warning" size="small">{{ row.reason }}</el-tag>
                </template>
              </el-table-column>
              <template #empty><div class="empty">没有成交记录</div></template>
            </el-table>
            <el-pagination
              v-if="tradeTotal > PAGE"
              v-model:current-page="tradePage"
              :page-size="PAGE"
              :total="tradeTotal"
              layout="total, prev, pager, next"
              class="pager"
            />
          </el-tab-pane>

          <el-tab-pane v-if="m" label="每日持仓" name="holdings">
            <div class="tbar">
              <el-button size="small" :disabled="holdIdx <= 0" aria-label="前一个交易日" @click="stepHold(-1)">‹</el-button>
              <el-select
                :model-value="holdDate"
                size="small"
                filterable
                class="hdate"
                aria-label="选择日期"
                @update:model-value="loadHoldings($event)"
              >
                <el-option v-for="d in [...holdDays].reverse()" :key="d" :value="d" :label="d" />
              </el-select>
              <el-button size="small" :disabled="holdIdx >= holdDays.length - 1" aria-label="后一个交易日" @click="stepHold(1)">›</el-button>
              <span class="muted">
                总资产 {{ fmtNum(holdEquity, 0) }}，现金 {{ fmtNum(holdCash, 0) }}，持有 {{ holdings.length }} 只
              </span>
            </div>
            <el-slider
              v-if="holdDays.length > 1"
              v-model="holdIdx"
              :min="0"
              :max="holdDays.length - 1"
              :show-tooltip="false"
              class="hslider"
              aria-label="拖动选择日期"
            />
            <el-table v-loading="holdLoading" :data="holdings" size="small" max-height="520">
              <el-table-column label="股票" min-width="150">
                <template #default="{ row }">
                  <RouterLink :to="{ name: 'data-kline', query: { symbol: row.symbol } }">{{ row.symbol }}</RouterLink>
                  <span class="muted sname">{{ row.name }}</span>
                </template>
              </el-table-column>
              <el-table-column label="持股" width="110" align="right">
                <template #default="{ row }">{{ fmtInt(row.shares) }}</template>
              </el-table-column>
              <el-table-column label="收盘价" width="100" align="right">
                <template #default="{ row }">{{ fmtNum(row.price) }}</template>
              </el-table-column>
              <el-table-column label="市值" width="120" align="right">
                <template #default="{ row }">{{ fmtNum(row.value, 0) }}</template>
              </el-table-column>
              <el-table-column label="仓位" width="90" align="right">
                <template #default="{ row }">{{ pct(row.weight, 1, false) }}</template>
              </el-table-column>
              <el-table-column label="浮动盈亏" width="100" align="right">
                <template #default="{ row }">
                  <span :class="sign(row.pnl_pct)">{{ fmtNum(row.pnl_pct) }}%</span>
                </template>
              </el-table-column>
              <template #empty><div class="empty">这一天空仓</div></template>
            </el-table>
          </el-tab-pane>

          <el-tab-pane v-if="m" :label="`策略日志 ${r.logs?.length || ''}`" name="logs">
            <pre v-if="r.logs?.length" class="log">{{ r.logs.join('\n') }}</pre>
            <div v-else class="empty">策略没有写日志。在脚本里用 <code>ctx.log(...)</code> 输出。</div>
          </el-tab-pane>

          <el-tab-pane label="参数与设置" name="settings">
            <div class="sgrid">
              <div>
                <h3>回测设置</h3>
                <dl>
                  <dt>区间</dt><dd>{{ r.settings.start }} ~ {{ r.settings.end }}</dd>
                  <dt>初始资金</dt><dd>{{ fmtNum(r.settings.initial_cash, 0) }} 元</dd>
                  <dt>调仓</dt>
                  <dd>{{ r.settings.rebalance === 'every_n' ? `每 ${r.settings.every_n} 个交易日` : REBALANCE_LABEL[r.settings.rebalance] }}<template v-if="r.signals">，共 {{ r.signals }} 次信号</template></dd>
                  <dt>成交价</dt><dd>{{ FILL_LABEL[r.settings.fill] }}</dd>
                  <dt>基准</dt><dd>{{ benchmarkLabel(r.settings.benchmark) }}</dd>
                  <dt>佣金</dt><dd>{{ (r.settings.commission * 1e4).toFixed(2) }}‱，最低 {{ r.settings.min_commission }} 元</dd>
                  <dt>印花税</dt><dd>{{ (r.settings.stamp_tax * 1e4).toFixed(2) }}‱（卖出）</dd>
                  <dt>滑点</dt><dd>{{ (r.settings.slippage * 1e4).toFixed(1) }}‱</dd>
                  <dt>调仓容差</dt><dd>{{ (r.settings.tolerance * 100).toFixed(1) }}%</dd>
                  <dt>历史窗口</dt><dd>{{ r.settings.lookback }} 个交易日</dd>
                </dl>
              </div>
              <div>
                <h3>策略参数</h3>
                <dl>
                  <template v-for="(v, k) in r.params" :key="k">
                    <dt>{{ k }}</dt>
                    <dd>{{ Array.isArray(v) ? v.join('、') : String(v) }}</dd>
                  </template>
                </dl>
                <p class="muted note">策略版本 {{ r.strategy_version || '–' }}，运行编号 {{ r.run_id }}</p>
              </div>
            </div>
          </el-tab-pane>
        </el-tabs>
      </section>
    </template>
  </div>
</template>

<style scoped>
.err {
  margin: 12px 16px;
  width: auto;
}

.headline {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  border-bottom: 1px solid var(--rule-soft);
}

.kpi {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 14px 18px;
}

.kpi + .kpi {
  border-left: 1px solid var(--rule-soft);
}

.kk {
  color: var(--muted);
  font-size: var(--fs-sm);
}

.kv {
  font-size: var(--fs-2xl);
  font-weight: 600;
  line-height: 1.1;
  color: var(--ink);
}

.kv.up {
  color: var(--up);
}

.kv.down {
  color: var(--down);
}

.chart {
  padding: 10px 16px 4px;
}

.chart-tools {
  display: flex;
  align-items: center;
  font-size: var(--fs-sm);
}

.details {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  border-top: 1px solid var(--rule-soft);
}

.dgroup {
  padding: 14px 18px 16px;
}

.dgroup + .dgroup {
  border-left: 1px solid var(--rule-soft);
}

.dgroup h3,
.sgrid h3 {
  margin: 0 0 8px;
  font-size: var(--fs-md);
}

dl {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 5px 18px;
  margin: 0;
  font-size: var(--fs-sm);
}

dt {
  color: var(--muted);
}

dd {
  margin: 0;
  color: var(--ink);
  text-align: right;
}

.sgrid dd {
  text-align: left;
}

.rtabs {
  padding: 4px 16px 16px;
}

.mwrap {
  overflow-x: auto;
}

.mtable {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--fs-sm);
}

.mtable th,
.mtable td {
  padding: 6px 8px;
  border: 1px solid var(--rule-soft);
  text-align: right;
  white-space: nowrap;
}

.mtable thead th {
  background: #f5f7fa;
  color: var(--ink-2);
  font-weight: 500;
}

.mtable tbody th {
  color: var(--ink-2);
  font-weight: 500;
  text-align: left;
}

.mtable td.year {
  font-weight: 600;
  border-left: 2px solid var(--rule);
}

.note {
  margin: 8px 0 0;
  font-size: var(--fs-xs);
}

.tbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  font-size: var(--fs-sm);
}

.tsearch {
  width: 160px;
}

.hdate {
  width: 140px;
}

.hslider {
  margin: 0 8px 8px;
}

.sname {
  margin-left: 8px;
}

.pager {
  margin-top: 10px;
  justify-content: flex-end;
}

.sgrid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
  gap: 32px;
  max-width: 900px;
}

@media (max-width: 900px) {
  .kpi + .kpi,
  .dgroup + .dgroup {
    border-left: 0;
  }

  .tbar {
    flex-wrap: wrap;
  }
}
</style>
