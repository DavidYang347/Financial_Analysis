<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, type MaintenanceJob, type QualityReport, type SourceProbe } from '@/api'
import { errorText } from '@/api/http'
import RunLog from '@/components/RunLog.vue'
import { dataStatus, refreshDataStatus } from '@/stores/dataStatus'
import { RUN_MODE_LABEL, SOURCE_LABEL, fmtDuration, fmtInt, fmtTime } from '@/utils/format'

const job = ref<MaintenanceJob>({ status: 'idle' })
const kind = ref<'update' | 'repair'>('update')
const scope = ref<'all' | 'some'>('all')
const symbolsText = ref('')
const refreshMeta = ref(true)
const starting = ref(false)

const check = ref<QualityReport | null>(null)
const checking = ref(false)
const sources = ref<SourceProbe | null>(null)
const probing = ref(false)

const running = computed(() => job.value.status === 'running')
const pct = computed(() => {
  const t = job.value.total ?? 0
  return t > 0 ? Math.round(((job.value.done ?? 0) / t) * 100) : 0
})

function parseSymbols(): string[] {
  return symbolsText.value.split(/[\s,，;；]+/).map((s) => s.trim()).filter(Boolean)
}

let timer: number | undefined
async function pollJob() {
  try {
    const prev = job.value.status
    job.value = await api.job()
    if (prev === 'running' && job.value.status !== 'running') {
      refreshDataStatus()
      if (job.value.status === 'finished') ElMessage.success('数据更新完成')
      else ElMessage.error(`数据更新失败：${job.value.error ?? ''}`)
    }
  } catch {
    /* backend down: the top bar already says so */
  }
  window.clearTimeout(timer)
  timer = window.setTimeout(pollJob, job.value.status === 'running' ? 1000 : 5000)
}

async function start() {
  const symbols = scope.value === 'some' || kind.value === 'repair' ? parseSymbols() : null
  if ((kind.value === 'repair' || scope.value === 'some') && !symbols?.length) {
    ElMessage.warning('填写至少一只股票代码')
    return
  }
  starting.value = true
  try {
    job.value = await api.startUpdate({ kind: kind.value, symbols, refresh_meta: refreshMeta.value })
    pollJob()
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    starting.value = false
  }
}

async function runCheck() {
  checking.value = true
  try {
    check.value = await api.check()
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    checking.value = false
  }
}

async function probe() {
  probing.value = true
  try {
    sources.value = await api.sources()
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    probing.value = false
  }
}

const sourceRows = computed(() =>
  sources.value
    ? Object.entries(sources.value).filter(([k]) => !k.startsWith('_')).map(([name, v]) => ({ name, ...v }))
    : [],
)

onMounted(pollJob)
onBeforeUnmount(() => window.clearTimeout(timer))
</script>

<template>
  <div class="page">
    <div class="grid">
      <section class="sheet">
        <div class="sheet-head"><h2>更新行情</h2></div>
        <div class="sheet-body">
          <el-form label-position="top" :disabled="running" @submit.prevent="start">
            <el-form-item label="操作">
              <el-radio-group v-model="kind">
                <el-radio value="update">增量更新</el-radio>
                <el-radio value="repair">修复指定股票</el-radio>
              </el-radio-group>
              <p class="hint">
                <template v-if="kind === 'update'">补齐每只股票上次之后的日线，并为新出现除权除息的股票重算复权因子。</template>
                <template v-else>删掉这些股票已存的全部日线，从上市日重新下载。</template>
              </p>
            </el-form-item>

            <el-form-item v-if="kind === 'update'" label="范围">
              <el-radio-group v-model="scope">
                <el-radio value="all">全部在市股票</el-radio>
                <el-radio value="some">指定股票</el-radio>
              </el-radio-group>
            </el-form-item>

            <el-form-item v-if="kind === 'repair' || scope === 'some'" label="股票代码">
              <el-input
                v-model="symbolsText"
                type="textarea"
                :rows="3"
                placeholder="600000.SH, 000001  一行一个或用逗号分隔"
              />
            </el-form-item>

            <el-form-item v-if="kind === 'update'">
              <el-checkbox v-model="refreshMeta">同时刷新股票列表和交易日历</el-checkbox>
              <p class="hint">新股上市、退市都靠这一步发现，约多花 1 分钟。</p>
            </el-form-item>

            <el-button type="primary" native-type="submit" :loading="starting || running">
              {{ running ? '正在更新' : kind === 'update' ? '开始更新' : '开始修复' }}
            </el-button>
          </el-form>

          <div v-if="job.status !== 'idle'" class="job">
            <div class="job-head">
              <strong>
                {{ job.status === 'running' ? job.phase_label : job.status === 'finished' ? '上次更新已完成' : '上次更新失败' }}
              </strong>
              <span class="muted">{{ job.run_id }}</span>
            </div>
            <el-progress
              v-if="running"
              :percentage="pct"
              :stroke-width="10"
              :format="() => (job.total ? `${fmtInt(job.done)} / ${fmtInt(job.total)}` : '')"
            />
            <el-alert v-if="job.status === 'failed'" :title="job.error" type="error" :closable="false" show-icon />
            <dl v-if="job.status === 'finished' && job.report" class="facts">
              <div><dt>目标交易日</dt><dd>{{ job.report.target_date }}</dd></div>
              <div><dt>更新股票</dt><dd>{{ fmtInt(job.report.symbols_updated) }}</dd></div>
              <div><dt>写入行数</dt><dd>{{ fmtInt(job.report.rows_written) }}</dd></div>
              <div><dt>重算复权</dt><dd>{{ fmtInt(job.report.factors_recomputed) }}</dd></div>
              <div><dt>失败</dt><dd :class="{ up: job.report.symbols_failed }">{{ fmtInt(job.report.symbols_failed) }}</dd></div>
              <div><dt>耗时</dt><dd>{{ fmtDuration(job.elapsed_sec) }}</dd></div>
            </dl>
            <RunLog v-if="job.run_id" :run-id="job.run_id" />
          </div>
        </div>
      </section>

      <section class="sheet">
        <div class="sheet-head"><h2>数据现状</h2></div>
        <div v-if="dataStatus.value" class="sheet-body">
          <dl class="stat">
            <div><dt>行情区间</dt><dd>{{ dataStatus.value.first_date }} 至 {{ dataStatus.value.last_date }}</dd></div>
            <div><dt>日线行数</dt><dd>{{ fmtInt(dataStatus.value.rows) }}</dd></div>
            <div>
              <dt>股票</dt>
              <dd>
                {{ fmtInt(dataStatus.value.symbols) }} 只有行情<span
                  v-for="u in dataStatus.value.universe ?? []"
                  :key="u.status"
                  class="muted"
                >，{{ u.status === 'listed' ? '在市' : '退市' }} {{ fmtInt(u.n) }}</span>
              </dd>
            </div>
            <div><dt>最新交易日有行情</dt><dd>{{ fmtInt(dataStatus.value.latest_day_symbols) }} 只</dd></div>
            <div><dt>有复权因子</dt><dd>{{ fmtInt(dataStatus.value.adj_factor_symbols) }} 只</dd></div>
            <div><dt>占用空间</dt><dd>{{ dataStatus.value.disk_mb }} MB</dd></div>
            <div>
              <dt>来源</dt>
              <dd>
                <span v-for="s in dataStatus.value.by_source ?? []" :key="s.source" class="src">
                  {{ SOURCE_LABEL[s.source] ?? s.source }} {{ fmtInt(s.n) }}
                </span>
              </dd>
            </div>
          </dl>
          <p class="hint">最近一次：
            <template v-if="dataStatus.value.job?.status === 'running'">正在运行</template>
            <RouterLink v-else :to="{ name: 'data-runs' }">查看更新日志与历史</RouterLink>
          </p>
        </div>
        <div v-else class="empty">{{ dataStatus.error || '加载中' }}</div>
      </section>
    </div>

    <div class="grid">
      <section class="sheet">
        <div class="sheet-head">
          <h2>质量检查</h2>
          <div class="spacer" />
          <el-button :loading="checking" @click="runCheck">检查数据</el-button>
        </div>
        <div class="sheet-body">
          <p v-if="!check" class="muted">检查重复行、异常价格、缺失交易日和复权后不合理的跳空，大约需要 10 秒。</p>
          <template v-else>
            <el-alert
              :title="check.ok ? '没有发现重复数据' : `发现 ${check.duplicates} 条重复行，运行修复处理`"
              :type="check.ok ? 'success' : 'error'"
              :closable="false"
              show-icon
            />
            <dl class="stat">
              <div><dt>重复行</dt><dd>{{ check.duplicates }}</dd></div>
              <div><dt>价格异常（高 &lt; 收等）</dt><dd>{{ check.bad_ohlc }}</dd></div>
              <div><dt>非交易日的行情</dt><dd>{{ fmtInt(check.non_trading_day_rows) }}<span class="muted">（1990 年代周六交易，正常）</span></dd></div>
              <div><dt>在市但最新交易日无行情</dt><dd>{{ check.listed_missing_latest }}<span class="muted">（多为停牌）</span></dd></div>
              <div><dt>在市但没有任何行情</dt><dd>{{ check.listed_without_any_bars.length }}<span class="muted">（多为尚未开始交易的新股）</span></dd></div>
              <div><dt>复权后单日涨跌超 45%</dt><dd>{{ check.suspicious_jumps_hfq_count }}</dd></div>
            </dl>
            <el-table v-if="check.suspicious_jumps_hfq.length" :data="check.suspicious_jumps_hfq" max-height="260" size="small">
              <el-table-column prop="symbol" label="代码" width="110">
                <template #default="{ row }">
                  <RouterLink :to="{ name: 'data-kline', query: { symbol: row.symbol, adjust: 'hfq', range: 'all' } }">{{ row.symbol }}</RouterLink>
                </template>
              </el-table-column>
              <el-table-column prop="prev_date" label="前一交易日" width="120" />
              <el-table-column prop="date" label="日期" width="120" />
              <el-table-column label="复权涨跌">
                <template #default="{ row }"><span :class="row.ret > 0 ? 'up' : 'down'">{{ (row.ret * 100).toFixed(1) }}%</span></template>
              </el-table-column>
            </el-table>
          </template>
        </div>
      </section>

      <section class="sheet">
        <div class="sheet-head">
          <h2>数据源</h2>
          <div class="spacer" />
          <el-button :loading="probing" @click="probe">测试连接</el-button>
        </div>
        <div class="sheet-body">
          <p class="muted">更新时按顺序尝试，前一个失败或没有数据才用下一个。</p>
          <ol class="chain">
            <li v-for="s in ['tencent', 'mootdx', 'eastmoney', 'baostock', 'akshare', 'tushare']" :key="s">
              <span class="src-name">{{ SOURCE_LABEL[s] }}</span>
              <template v-if="sources && sources[s]">
                <el-tag v-if="sources[s].ok" type="success" size="small">可用 {{ sources[s].sec }}s</el-tag>
                <el-tooltip v-else :content="sources[s].error ?? '无数据'" placement="top">
                  <el-tag type="danger" size="small">不可用</el-tag>
                </el-tooltip>
              </template>
            </li>
          </ol>
          <p v-if="sourceRows.some((r) => r.name === 'tushare' && !r.ok)" class="hint">
            Tushare 需要在项目根目录的 .env 里填 TUSHARE_TOKEN，重启后端生效。
          </p>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.grid {
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
  gap: 16px;
  align-items: start;
}

.hint {
  width: 100%;
  margin: 4px 0 0;
  color: var(--muted);
  font-size: var(--fs-sm);
  line-height: 1.5;
}

.job {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid var(--rule-soft);
}

.job-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.facts {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 24px;
  margin: 0;
}

.facts div,
.stat div {
  display: flex;
  gap: 8px;
  align-items: baseline;
}

.facts dt,
.stat dt {
  color: var(--muted);
  font-size: var(--fs-sm);
}

.facts dd,
.stat dd {
  margin: 0;
  font-weight: 500;
}

.stat {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 12px 0;
}

.stat dt {
  flex: 0 0 160px;
}

.src {
  margin-right: 12px;
}

.chain {
  margin: 12px 0 0;
  padding-left: 22px;
}

.chain li {
  padding: 6px 0;
  border-bottom: 1px solid var(--rule-soft);
}

.chain li:last-child {
  border-bottom: 0;
}

.src-name {
  display: inline-block;
  width: 96px;
}

@media (max-width: 1100px) {
  .grid {
    grid-template-columns: 1fr;
  }
}
</style>
