<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { strategyApi, type BacktestRecord, type StrategyDetail, type StrategySummary } from '@/api'
import { errorText } from '@/api/http'
import CodeView from '@/components/CodeView.vue'
import MarkdownView from '@/components/MarkdownView.vue'
import { fmtTime } from '@/utils/format'
import { FILL_LABEL, REBALANCE_LABEL, benchmarkLabel, num, pct, settingsText, sign } from '@/modules/backtest/labels'

const route = useRoute()
const router = useRouter()

const list = ref<StrategySummary[]>([])
const directory = ref('')
const listError = ref('')
const filter = ref('')
const detail = ref<StrategyDetail | null>(null)
const source = ref('')
const tab = ref<'about' | 'source' | 'history'>('about')
const history = ref<BacktestRecord[]>([])

const currentId = computed(() => String(route.params.id ?? ''))
const shown = computed(() => {
  const k = filter.value.trim().toLowerCase()
  if (!k) return list.value
  return list.value.filter((s) => [s.name, s.id, s.brief, ...s.tags].join(' ').toLowerCase().includes(k))
})

const defaultRows = computed(() => {
  const d = detail.value?.defaults ?? {}
  const rows: { k: string; v: string }[] = []
  if (d.rebalance) rows.push({ k: '调仓频率', v: d.rebalance === 'every_n' ? `每 ${d.every_n ?? 5} 个交易日` : REBALANCE_LABEL[d.rebalance] })
  if (d.fill) rows.push({ k: '成交价', v: FILL_LABEL[d.fill] })
  if (d.benchmark) rows.push({ k: '基准', v: benchmarkLabel(d.benchmark) })
  if (d.lookback !== undefined) rows.push({ k: '需要的历史 K 线', v: `${d.lookback} 根` })
  if (d.initial_cash !== undefined) rows.push({ k: '初始资金', v: `${d.initial_cash.toLocaleString('zh-CN')} 元` })
  if (d.start) rows.push({ k: '默认开始日期', v: d.start })
  return rows
})

async function loadList() {
  try {
    const r = await strategyApi.list()
    list.value = r.items
    directory.value = r.directory
    listError.value = ''
    if (!currentId.value && r.items.length) router.replace({ name: 'strategy', params: { id: r.items[0].id } })
  } catch (e) {
    listError.value = errorText(e)
  }
}

async function loadDetail(id: string) {
  if (!id) return
  source.value = ''
  try {
    detail.value = await strategyApi.detail(id)
    if (!detail.value.ok) tab.value = 'source'
    loadHistory()
    if (tab.value === 'source') loadSource()
  } catch (e) {
    detail.value = null
    ElMessage.error(errorText(e))
  }
}

async function loadSource() {
  if (!detail.value || source.value) return
  source.value = (await strategyApi.source(detail.value.id)).source
}

async function loadHistory() {
  if (!detail.value) return
  history.value = (await strategyApi.history(detail.value.id, 50)).items
}

function backtest() {
  if (detail.value) router.push({ name: 'backtest-new', query: { strategy: detail.value.id } })
}

watch(tab, (t) => t === 'source' && loadSource())
watch(currentId, loadDetail)
onMounted(async () => {
  await loadList()
  loadDetail(currentId.value)
})
</script>

<template>
  <div class="page">
    <div class="layout">
      <aside class="sheet slist" aria-label="策略列表">
        <div class="sheet-head">
          <h2>策略</h2>
          <span class="muted">{{ list.length }}</span>
          <div class="spacer" />
          <el-tooltip content="重新扫描脚本目录" placement="top">
            <el-button text aria-label="刷新策略列表" @click="loadList">刷新</el-button>
          </el-tooltip>
        </div>
        <div class="search">
          <el-input v-model="filter" placeholder="按名称或标签过滤" clearable aria-label="过滤策略" />
        </div>
        <el-alert v-if="listError" :title="listError" type="error" :closable="false" show-icon />
        <ul class="items">
          <li v-for="s in shown" :key="s.id">
            <RouterLink
              :to="{ name: 'strategy', params: { id: s.id } }"
              class="item"
              :class="{ on: s.id === currentId, broken: !s.ok }"
            >
              <span class="iname">{{ s.name }}</span>
              <span class="ifile">{{ s.file }}</span>
              <span class="ibrief">{{ s.ok ? s.brief : `加载失败：${s.error}` }}</span>
              <span v-if="s.tags.length" class="itags">
                <span v-for="t in s.tags" :key="t" class="tag">{{ t }}</span>
              </span>
            </RouterLink>
          </li>
        </ul>
        <p class="howto muted">
          一个策略对应 <code>strategy/strategies/</code> 下的一个 .py 文件。新增或删除文件后点“刷新”。写法见同目录 README.md。
        </p>
      </aside>

      <section v-if="detail" class="sheet main">
        <header class="dhead">
          <div class="dtitle">
            <h1>{{ detail.name }}</h1>
            <span class="muted">{{ detail.file }}<template v-if="detail.version">，v{{ detail.version }}</template></span>
          </div>
          <div class="dtags">
            <span v-for="t in detail.tags" :key="t" class="tag">{{ t }}</span>
          </div>
          <el-button type="primary" :disabled="!detail.ok" @click="backtest">回测这个策略</el-button>
        </header>

        <el-alert
          v-if="!detail.ok"
          :title="`脚本加载失败：${detail.error}`"
          description="修改脚本后点左侧“刷新”。错误详情见“说明”。"
          type="error"
          :closable="false"
          show-icon
          class="broken-alert"
        />

        <el-tabs v-model="tab" class="dtabs">
          <el-tab-pane label="说明" name="about">
            <div class="about">
              <MarkdownView :source="detail.description" />

              <h3 v-if="detail.params.length">参数</h3>
              <el-table v-if="detail.params.length" :data="detail.params" size="small" class="ptable">
                <el-table-column prop="label" label="参数" min-width="160" />
                <el-table-column prop="key" label="脚本中的名字" width="140" />
                <el-table-column label="默认值" width="160">
                  <template #default="{ row }">
                    {{ Array.isArray(row.default) ? row.default.join('、') : String(row.default) }} {{ row.unit }}
                  </template>
                </el-table-column>
                <el-table-column label="范围" width="120">
                  <template #default="{ row }">
                    <template v-if="row.min !== null || row.max !== null">{{ row.min ?? '' }} ~ {{ row.max ?? '' }}</template>
                  </template>
                </el-table-column>
                <el-table-column prop="help" label="说明" min-width="160" />
              </el-table>

              <h3 v-if="defaultRows.length">默认回测设置</h3>
              <dl v-if="defaultRows.length" class="defaults">
                <template v-for="r in defaultRows" :key="r.k">
                  <dt>{{ r.k }}</dt>
                  <dd>{{ r.v }}</dd>
                </template>
              </dl>
            </div>
          </el-tab-pane>

          <el-tab-pane label="源码" name="source">
            <CodeView v-if="source" :source="source" :filename="detail.file" />
            <p class="muted path">{{ directory }}/{{ detail.file }}</p>
          </el-tab-pane>

          <el-tab-pane :label="`回测记录 ${history.length || ''}`" name="history">
            <el-table :data="history" size="small">
              <el-table-column label="时间" width="160">
                <template #default="{ row }">{{ fmtTime(row.started_at) }}</template>
              </el-table-column>
              <el-table-column label="设置" min-width="240">
                <template #default="{ row }"><span class="pv">{{ settingsText(row.settings) }}</span></template>
              </el-table-column>
              <el-table-column label="总收益" width="100" align="right">
                <template #default="{ row }">
                  <span v-if="row.status === 'ok'" :class="sign(row.metrics?.total_return)">{{ pct(row.metrics?.total_return) }}</span>
                  <el-tag v-else :type="row.status === 'cancelled' ? 'info' : 'danger'" size="small">
                    {{ row.status === 'cancelled' ? '已取消' : '出错' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="最大回撤" width="100" align="right">
                <template #default="{ row }">{{ pct(row.metrics?.max_drawdown, 2, false) }}</template>
              </el-table-column>
              <el-table-column label="夏普" width="80" align="right">
                <template #default="{ row }">{{ num(row.metrics?.sharpe) }}</template>
              </el-table-column>
              <el-table-column width="100" align="right">
                <template #default="{ row }">
                  <RouterLink v-if="row.status === 'ok'" :to="{ name: 'backtest-report', params: { runId: row.run_id } }">查看报告</RouterLink>
                </template>
              </el-table-column>
              <template #empty><div class="empty">这个策略还没有回测过</div></template>
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </section>
      <section v-else class="sheet main">
        <div class="empty">
          <strong>{{ list.length ? '从左侧选择一个策略' : '还没有策略' }}</strong>
          <template v-if="!list.length">在 strategy/strategies/ 下新建一个 .py 文件，然后点“刷新”</template>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.layout {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.slist {
  position: sticky;
  top: 64px;
  display: flex;
  flex-direction: column;
  max-height: calc(100vh - 80px);
}

.search {
  padding: 10px 12px 6px;
}

.items {
  flex: 1;
  margin: 0;
  padding: 4px 0;
  overflow: auto;
  list-style: none;
}

.item {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 2px 8px;
  padding: 10px 14px;
  color: inherit;
  text-decoration: none;
  border-left: 3px solid transparent;
}

.item:hover {
  background: #f5f7fa;
}

.item.on {
  background: var(--accent-soft);
  border-left-color: var(--accent);
}

.iname {
  font-weight: 600;
  color: var(--ink);
}

.ifile {
  color: var(--muted);
  font-size: var(--fs-xs);
}

.ibrief {
  grid-column: 1 / -1;
  color: var(--ink-2);
  font-size: var(--fs-sm);
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.broken .ibrief {
  color: var(--up);
}

.itags {
  grid-column: 1 / -1;
  display: flex;
  gap: 4px;
  margin-top: 2px;
}

.tag {
  padding: 0 6px;
  border: 1px solid var(--rule);
  border-radius: 3px;
  color: var(--ink-2);
  font-size: var(--fs-xs);
  line-height: 18px;
}

.howto {
  margin: 0;
  padding: 10px 14px 14px;
  border-top: 1px solid var(--rule-soft);
  font-size: var(--fs-xs);
  line-height: 1.6;
}

.howto code {
  font-size: 11px;
}

.main {
  min-width: 0;
}

.dhead {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 18px 20px 0;
}

.dtitle {
  display: flex;
  align-items: baseline;
  gap: 12px;
}

.dtitle h1 {
  font-size: var(--fs-2xl);
  letter-spacing: -0.01em;
}

.dtags {
  display: flex;
  gap: 6px;
  margin-left: auto;
}

.broken-alert {
  margin: 12px 20px 0;
  width: auto;
}

.dtabs {
  padding: 0 20px 20px;
}

.about {
  max-width: 900px;
}

.about h3 {
  margin: 20px 0 8px;
  font-size: var(--fs-md);
}

.defaults {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 6px 24px;
  margin: 0;
  font-size: var(--fs-sm);
}

.defaults dt {
  color: var(--muted);
}

.defaults dd {
  margin: 0;
  color: var(--ink);
}

.path {
  margin: 8px 0 0;
  font-size: var(--fs-xs);
}

.pv {
  color: var(--ink-2);
  font-size: var(--fs-xs);
}

@media (max-width: 900px) {
  .layout {
    grid-template-columns: 1fr;
  }

  .slist {
    position: static;
    max-height: 360px;
  }

  .dhead {
    flex-wrap: wrap;
  }
}
</style>
