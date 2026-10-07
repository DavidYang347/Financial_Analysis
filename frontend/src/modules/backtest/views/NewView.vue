<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, toRaw, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { strategyApi, type BacktestJob, type BacktestSettings, type StrategyDetail, type StrategySummary } from '@/api'
import { errorText } from '@/api/http'
import ParamForm from '@/components/ParamForm.vue'
import { dataStatus } from '@/stores/dataStatus'
import { fmtDuration } from '@/utils/format'
import { FILL_LABEL, REBALANCE_LABEL } from '../labels'

const route = useRoute()
const router = useRouter()

const list = ref<StrategySummary[]>([])
const sid = ref<string>('')
const detail = ref<StrategyDetail | null>(null)
const values = ref<Record<string, unknown>>({})
const job = ref<BacktestJob>({ status: 'idle' })
const starting = ref(false)

/** Backtest settings shown as percentages / yuan in the form, converted on submit. */
interface Form {
  range: [string, string] | null
  initial_cash: number
  rebalance: BacktestSettings['rebalance']
  every_n: number
  fill: BacktestSettings['fill']
  benchmarkMode: 'equal' | 'none' | 'symbol'
  benchmarkSymbol: string
  commission_bp: number // 万分之
  min_commission: number
  stamp_tax_bp: number
  slippage_bp: number
  tolerance_pct: number
  risk_free_pct: number
  lookback: number
}

const BASE: Form = {
  range: null, initial_cash: 1_000_000, rebalance: 'monthly', every_n: 5, fill: 'next_open',
  benchmarkMode: 'equal', benchmarkSymbol: '', commission_bp: 2.5, min_commission: 5, stamp_tax_bp: 5,
  slippage_bp: 5, tolerance_pct: 1, risk_free_pct: 0, lookback: 120,
}
const form = ref<Form>({ ...BASE })

const running = computed(() => job.value.status === 'running')
const pctDone = computed(() => {
  const t = job.value.total ?? 0
  return t > 0 ? Math.round(((job.value.done ?? 0) / t) * 100) : 0
})

function defaultRange(): [string, string] {
  const end = dataStatus.value?.last_date ?? new Date().toISOString().slice(0, 10)
  const d = new Date(end)
  d.setFullYear(d.getFullYear() - 3)
  return [d.toISOString().slice(0, 10), end]
}

function applyDefaults(d: StrategyDetail) {
  const df = d.defaults ?? {}
  const f: Form = { ...BASE, range: defaultRange() }
  if (df.start) f.range = [df.start, f.range![1]]
  if (df.initial_cash !== undefined) f.initial_cash = df.initial_cash
  if (df.rebalance) f.rebalance = df.rebalance
  if (df.every_n !== undefined) f.every_n = df.every_n
  if (df.fill) f.fill = df.fill
  if (df.lookback !== undefined) f.lookback = df.lookback
  if (df.commission !== undefined) f.commission_bp = df.commission * 1e4
  if (df.min_commission !== undefined) f.min_commission = df.min_commission
  if (df.stamp_tax !== undefined) f.stamp_tax_bp = df.stamp_tax * 1e4
  if (df.slippage !== undefined) f.slippage_bp = df.slippage * 1e4
  if (df.tolerance !== undefined) f.tolerance_pct = df.tolerance * 100
  if (df.benchmark) {
    if (df.benchmark === 'equal' || df.benchmark === 'none') f.benchmarkMode = df.benchmark
    else { f.benchmarkMode = 'symbol'; f.benchmarkSymbol = df.benchmark }
  }
  form.value = f
  values.value = Object.fromEntries(toRaw(d).params.map((p) => [p.key, structuredClone(toRaw(p.default))]))
}

function settingsPayload(): Partial<BacktestSettings> {
  const f = form.value
  const r = f.range ?? defaultRange()
  return {
    start: r[0], end: r[1], initial_cash: f.initial_cash, rebalance: f.rebalance, every_n: f.every_n, fill: f.fill,
    benchmark: f.benchmarkMode === 'symbol' ? f.benchmarkSymbol.trim() : f.benchmarkMode,
    commission: f.commission_bp / 1e4, min_commission: f.min_commission, stamp_tax: f.stamp_tax_bp / 1e4,
    slippage: f.slippage_bp / 1e4, tolerance: f.tolerance_pct / 100, risk_free: f.risk_free_pct / 100,
    lookback: f.lookback,
  }
}

async function loadList() {
  try {
    list.value = (await strategyApi.list()).items
    const want = String(route.query.strategy ?? '')
    sid.value = list.value.find((s) => s.id === want && s.ok)?.id ?? list.value.find((s) => s.ok)?.id ?? ''
  } catch (e) {
    ElMessage.error(errorText(e))
  }
}

async function loadDetail(id: string) {
  detail.value = null
  if (!id) return
  try {
    detail.value = await strategyApi.detail(id)
    applyDefaults(detail.value)
    // Prefill from a previous run ("用这组参数再跑一次").
    const from = route.query.from ? String(route.query.from) : ''
    if (from && route.query.strategy === id) await prefill(from)
  } catch (e) {
    ElMessage.error(errorText(e))
  }
}

async function prefill(runId: string) {
  try {
    const r = await strategyApi.report(runId)
    values.value = { ...values.value, ...r.params }
    const s = r.settings
    const b = s.benchmark
    form.value = {
      range: [s.start, s.end], initial_cash: s.initial_cash, rebalance: s.rebalance, every_n: s.every_n, fill: s.fill,
      benchmarkMode: b === 'equal' || b === 'none' ? b : 'symbol', benchmarkSymbol: b === 'equal' || b === 'none' ? '' : b,
      commission_bp: s.commission * 1e4, min_commission: s.min_commission, stamp_tax_bp: s.stamp_tax * 1e4,
      slippage_bp: s.slippage * 1e4, tolerance_pct: s.tolerance * 100, risk_free_pct: s.risk_free * 100,
      lookback: s.lookback,
    }
    ElMessage.info('已填入那次回测的参数和设置')
  } catch {
    /* the old run may have been removed; keep defaults */
  }
}

let timer: number | undefined
async function poll() {
  try {
    const prev = job.value.status
    job.value = await strategyApi.job()
    if (prev === 'running' && job.value.status !== 'running') {
      if (job.value.status === 'ok' && job.value.run_id) {
        ElMessage.success(`回测完成，用时 ${fmtDuration(job.value.elapsed_sec)}`)
        router.push({ name: 'backtest-report', params: { runId: job.value.run_id } })
        return
      }
      if (job.value.status === 'failed') ElMessage.error(`回测出错：${job.value.error ?? ''}`)
    }
  } catch {
    /* backend down: the top bar already says so */
  }
  window.clearTimeout(timer)
  timer = window.setTimeout(poll, job.value.status === 'running' ? 600 : 5000)
}

async function start() {
  if (!detail.value) return
  if (form.value.benchmarkMode === 'symbol' && !form.value.benchmarkSymbol.trim()) {
    ElMessage.warning('填写基准股票代码，或选“全市场等权”')
    return
  }
  starting.value = true
  try {
    job.value = await strategyApi.start(detail.value.id, values.value, settingsPayload())
    poll()
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    starting.value = false
  }
}

async function cancel() {
  job.value = await strategyApi.cancel()
}

function reset() {
  if (detail.value) applyDefaults(detail.value)
}

watch(sid, (id) => {
  loadDetail(id)
  if (id && route.query.strategy !== id) router.replace({ query: { strategy: id } })
})
onMounted(async () => {
  await loadList()
  poll()
})
onBeforeUnmount(() => window.clearTimeout(timer))
</script>

<template>
  <div class="page">
    <section class="sheet">
      <div class="sheet-head">
        <h2>新建回测</h2>
        <el-select v-model="sid" placeholder="选择策略" filterable class="pick" aria-label="选择策略" :disabled="running">
          <el-option v-for="s in list" :key="s.id" :value="s.id" :label="s.name" :disabled="!s.ok">
            <span>{{ s.name }}</span>
            <span class="muted opt-file">{{ s.ok ? s.file : '加载失败' }}</span>
          </el-option>
        </el-select>
        <RouterLink v-if="detail" :to="{ name: 'strategy', params: { id: detail.id } }" class="muted">策略说明与源码</RouterLink>
        <div class="spacer" />
        <RouterLink :to="{ name: 'backtest-history' }">回测记录</RouterLink>
      </div>

      <div v-if="running" class="progress">
        <div class="ptext">
          <strong>{{ job.strategy_name }}</strong>
          <span>回测中 {{ job.done ?? 0 }} / {{ job.total || '…' }} 个交易日</span>
          <div class="spacer" />
          <el-button size="small" @click="cancel">取消</el-button>
        </div>
        <el-progress :percentage="pctDone" :stroke-width="6" :show-text="false" />
      </div>
      <el-alert
        v-else-if="job.status === 'failed'"
        :title="`上次回测出错：${job.error}`"
        type="error"
        show-icon
        :closable="false"
        class="lasterr"
      />

      <div v-if="detail" class="body">
        <div class="col">
          <h3>策略参数</h3>
          <ParamForm v-if="detail.params.length" v-model="values" :params="detail.params" :disabled="running" />
          <p v-else class="muted">这个策略没有可调参数。</p>
        </div>

        <div class="col">
          <h3>回测设置</h3>
          <el-form label-position="top" :disabled="running" class="sform" @submit.prevent>
            <fieldset>
              <legend>区间与资金</legend>
              <el-form-item label="回测区间">
                <el-date-picker
                  v-model="form.range"
                  type="daterange"
                  value-format="YYYY-MM-DD"
                  range-separator="至"
                  start-placeholder="开始"
                  end-placeholder="结束"
                  :clearable="false"
                />
              </el-form-item>
              <el-form-item label="初始资金">
                <div class="field">
                  <el-input-number v-model="form.initial_cash" :min="10000" :step="100000" controls-position="right" />
                  <span class="unit">元</span>
                </div>
              </el-form-item>
              <el-form-item label="基准">
                <div class="field">
                  <el-radio-group v-model="form.benchmarkMode">
                    <el-radio-button value="equal">全市场等权</el-radio-button>
                    <el-radio-button value="symbol">股票</el-radio-button>
                    <el-radio-button value="none">无</el-radio-button>
                  </el-radio-group>
                  <el-input
                    v-if="form.benchmarkMode === 'symbol'"
                    v-model="form.benchmarkSymbol"
                    placeholder="如 600519.SH"
                    class="bsym"
                    aria-label="基准股票代码"
                  />
                </div>
              </el-form-item>
            </fieldset>

            <fieldset>
              <legend>调仓与成交</legend>
              <el-form-item label="调仓频率">
                <div class="field">
                  <el-select v-model="form.rebalance" class="w160">
                    <el-option v-for="(l, k) in REBALANCE_LABEL" :key="k" :value="k" :label="l" />
                  </el-select>
                  <template v-if="form.rebalance === 'every_n'">
                    <el-input-number v-model="form.every_n" :min="1" :max="250" controls-position="right" />
                    <span class="unit">个交易日</span>
                  </template>
                </div>
              </el-form-item>
              <el-form-item label="成交价">
                <el-radio-group v-model="form.fill">
                  <el-radio-button v-for="(l, k) in FILL_LABEL" :key="k" :value="k">{{ l }}</el-radio-button>
                </el-radio-group>
              </el-form-item>
              <el-form-item>
                <template #label>
                  <span>调仓容差</span>
                  <el-tooltip content="目标仓位和当前仓位相差小于这个比例时不交易，减少无谓的小额调仓" placement="top">
                    <span class="q" tabindex="0">?</span>
                  </el-tooltip>
                </template>
                <div class="field">
                  <el-input-number v-model="form.tolerance_pct" :min="0" :max="10" :step="0.5" controls-position="right" />
                  <span class="unit">% 总资产</span>
                </div>
              </el-form-item>
              <el-form-item>
                <template #label>
                  <span>历史窗口</span>
                  <el-tooltip content="策略在开始日之前需要多少根 K 线（算均线、动量等）" placement="top">
                    <span class="q" tabindex="0">?</span>
                  </el-tooltip>
                </template>
                <div class="field">
                  <el-input-number v-model="form.lookback" :min="0" :max="2000" :step="10" controls-position="right" />
                  <span class="unit">个交易日</span>
                </div>
              </el-form-item>
            </fieldset>

            <fieldset>
              <legend>费用</legend>
              <div class="fees">
                <el-form-item label="佣金">
                  <div class="field">
                    <el-input-number v-model="form.commission_bp" :min="0" :max="30" :step="0.5" :precision="2" controls-position="right" />
                    <span class="unit">‱ 双边</span>
                  </div>
                </el-form-item>
                <el-form-item label="最低佣金">
                  <div class="field">
                    <el-input-number v-model="form.min_commission" :min="0" :max="100" :step="1" controls-position="right" />
                    <span class="unit">元/笔</span>
                  </div>
                </el-form-item>
                <el-form-item label="印花税">
                  <div class="field">
                    <el-input-number v-model="form.stamp_tax_bp" :min="0" :max="30" :step="0.5" :precision="2" controls-position="right" />
                    <span class="unit">‱ 卖出</span>
                  </div>
                </el-form-item>
                <el-form-item label="滑点">
                  <div class="field">
                    <el-input-number v-model="form.slippage_bp" :min="0" :max="100" :step="1" :precision="1" controls-position="right" />
                    <span class="unit">‱</span>
                  </div>
                </el-form-item>
                <el-form-item label="无风险利率">
                  <div class="field">
                    <el-input-number v-model="form.risk_free_pct" :min="0" :max="10" :step="0.25" :precision="2" controls-position="right" />
                    <span class="unit">% 年化</span>
                  </div>
                </el-form-item>
              </div>
            </fieldset>
          </el-form>
        </div>
      </div>
      <div v-else-if="!list.length" class="empty">
        <strong>还没有策略</strong>在 strategy/strategies/ 下新建一个 .py 文件
      </div>

      <footer v-if="detail" class="foot">
        <el-button type="primary" size="large" :loading="starting || running" :disabled="running" @click="start">
          {{ running ? '回测中…' : '开始回测' }}
        </el-button>
        <el-button :disabled="running" @click="reset">恢复默认</el-button>
        <span class="muted note">
          A 股规则：T+1、整手、涨停买不进 / 跌停卖不出、停牌不成交，含佣金、印花税和滑点。回测只读本地行情库，结果自动保存。
        </span>
      </footer>
    </section>
  </div>
</template>

<style scoped>
.pick {
  width: 240px;
}

.opt-file {
  float: right;
  margin-left: 16px;
  font-size: var(--fs-xs);
}

.progress {
  padding: 12px 16px;
  border-bottom: 1px solid var(--rule-soft);
  background: var(--accent-soft);
}

.ptext {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
  font-size: var(--fs-sm);
}

.lasterr {
  margin: 12px 16px 0;
  width: auto;
}

.body {
  display: grid;
  grid-template-columns: minmax(260px, 340px) minmax(0, 1fr);
  gap: 32px;
  padding: 16px 20px;
}

.col h3 {
  margin: 0 0 12px;
  font-size: var(--fs-md);
}

.col + .col {
  padding-left: 32px;
  border-left: 1px solid var(--rule-soft);
}

.sform fieldset {
  margin: 0 0 8px;
  padding: 0;
  border: 0;
}

.sform legend {
  width: 100%;
  margin-bottom: 8px;
  padding-bottom: 4px;
  border-bottom: 1px solid var(--rule-soft);
  color: var(--ink-2);
  font-size: var(--fs-sm);
  font-weight: 600;
}

.sform :deep(.el-form-item) {
  margin-bottom: 12px;
}

.sform :deep(.el-form-item__label) {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 4px;
  line-height: 1.4;
}

.fees {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 0 20px;
}

.field {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.unit {
  color: var(--muted);
  font-size: var(--fs-sm);
}

.bsym {
  width: 140px;
}

.w160 {
  width: 160px;
}

.q {
  display: inline-grid;
  place-items: center;
  width: 16px;
  height: 16px;
  border: 1px solid var(--rule);
  border-radius: 50%;
  color: var(--muted);
  font-size: 11px;
  cursor: help;
}

.foot {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 20px;
  border-top: 1px solid var(--rule-soft);
}

.note {
  font-size: var(--fs-xs);
  line-height: 1.5;
}

@media (max-width: 1000px) {
  .body {
    grid-template-columns: 1fr;
  }

  .col + .col {
    padding-left: 0;
    border-left: 0;
  }

  .foot {
    flex-wrap: wrap;
  }
}
</style>
