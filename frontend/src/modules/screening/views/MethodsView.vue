<script setup lang="ts">
import { computed, onMounted, ref, toRaw, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api, type ScreenRecord, type ScreenResult, type ScreenerDetail, type ScreenerSummary } from '@/api'
import { errorText } from '@/api/http'
import CodeView from '@/components/CodeView.vue'
import MarkdownView from '@/components/MarkdownView.vue'
import ParamForm from '@/components/ParamForm.vue'
import ResultTable from '@/components/ResultTable.vue'
import { dataStatus } from '@/stores/dataStatus'
import { fmtDuration, fmtTime } from '@/utils/format'

const route = useRoute()
const router = useRouter()

const list = ref<ScreenerSummary[]>([])
const directory = ref('')
const listError = ref('')
const filter = ref('')
const detail = ref<ScreenerDetail | null>(null)
const source = ref('')
const tab = ref<'about' | 'run' | 'source' | 'history'>('run')
const values = ref<Record<string, unknown>>({})
const asOf = ref<string | null>(null)
const running = ref(false)
const result = ref<ScreenResult | null>(null)
const history = ref<ScreenRecord[]>([])

const currentId = computed(() => String(route.params.id ?? ''))
const shown = computed(() => {
  const k = filter.value.trim().toLowerCase()
  if (!k) return list.value
  return list.value.filter((s) => [s.name, s.id, s.brief, ...s.tags].join(' ').toLowerCase().includes(k))
})

function defaults(d: ScreenerDetail): Record<string, unknown> {
  // d comes from a reactive ref, so nested arrays/objects are Proxies; structuredClone can't handle Proxies, so unwrap first
  return Object.fromEntries(toRaw(d).params.map((p) => [p.key, structuredClone(toRaw(p.default))]))
}

async function loadList() {
  try {
    const r = await api.screeners()
    list.value = r.items
    directory.value = r.directory
    listError.value = ''
    if (!currentId.value && r.items.length) router.replace({ name: 'screening-method', params: { id: r.items[0].id } })
  } catch (e) {
    listError.value = errorText(e)
  }
}

async function loadDetail(id: string) {
  if (!id) return
  result.value = null
  source.value = ''
  try {
    detail.value = await api.screener(id)
    values.value = defaults(detail.value)
    tab.value = detail.value.ok ? (tab.value === 'source' || tab.value === 'about' ? tab.value : 'run') : 'source'
    loadHistory()
    if (tab.value === 'source') loadSource()
  } catch (e) {
    detail.value = null
    ElMessage.error(errorText(e))
  }
}

async function loadSource() {
  if (!detail.value || source.value) return
  source.value = (await api.screenerSource(detail.value.id)).source
}

async function loadHistory() {
  if (!detail.value) return
  history.value = (await api.screenerHistory(detail.value.id, 20)).items
}

async function run() {
  if (!detail.value) return
  running.value = true
  try {
    result.value = await api.runScreener(detail.value.id, values.value, asOf.value)
    if (result.value.status === 'ok') ElMessage.success(`选出 ${result.value.count} 只股票`)
    loadHistory()
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    running.value = false
  }
}

async function openRecord(r: ScreenRecord) {
  router.push({ name: 'screening-run', params: { runId: r.run_id } })
}

function applyParams(r: ScreenRecord) {
  values.value = { ...values.value, ...r.params }
  tab.value = 'run'
  ElMessage.info('已填入这次运行的参数')
}

function resetParams() {
  if (detail.value) values.value = defaults(detail.value)
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
      <aside class="sheet methods" aria-label="筛选方法列表">
        <div class="sheet-head">
          <h2>筛选方法</h2>
          <span class="muted">{{ list.length }}</span>
          <div class="spacer" />
          <el-tooltip content="重新扫描脚本目录" placement="top">
            <el-button text aria-label="刷新方法列表" @click="loadList">刷新</el-button>
          </el-tooltip>
        </div>
        <div class="search">
          <el-input v-model="filter" placeholder="按名称或标签过滤" clearable aria-label="过滤筛选方法" />
        </div>
        <el-alert v-if="listError" :title="listError" type="error" :closable="false" show-icon />
        <ul class="mlist">
          <li v-for="s in shown" :key="s.id">
            <RouterLink
              :to="{ name: 'screening-method', params: { id: s.id } }"
              class="mitem"
              :class="{ on: s.id === currentId, broken: !s.ok }"
            >
              <span class="mname">{{ s.name }}</span>
              <span class="mfile">{{ s.file }}</span>
              <span class="mbrief">{{ s.ok ? s.brief : `加载失败：${s.error}` }}</span>
              <span v-if="s.tags.length" class="mtags">
                <span v-for="t in s.tags" :key="t" class="tag">{{ t }}</span>
              </span>
            </RouterLink>
          </li>
        </ul>
        <p class="howto muted">
          一个方法对应 <code>screening/screeners/</code> 下的一个 .py 文件。新增或删除文件后点“刷新”。写法见同目录 README.md。
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
          <el-tab-pane label="运行" name="run" :disabled="!detail.ok">
            <div class="runpane">
              <div class="params">
                <ParamForm v-model="values" :params="detail.params" :disabled="running" />
                <el-form label-position="top" @submit.prevent>
                  <el-form-item label="筛选日期">
                    <el-date-picker
                      v-model="asOf"
                      type="date"
                      value-format="YYYY-MM-DD"
                      :placeholder="`最新交易日 ${dataStatus.value?.last_date ?? ''}`"
                      clearable
                    />
                  </el-form-item>
                </el-form>
                <div class="actions">
                  <el-button type="primary" :loading="running" @click="run">运行筛选</el-button>
                  <el-button :disabled="running" @click="resetParams">恢复默认参数</el-button>
                </div>
              </div>

              <div class="results">
                <template v-if="result">
                  <div class="rhead">
                    <template v-if="result.status === 'ok'">
                      <span class="count">{{ result.count }}</span>
                      <span>只股票满足条件</span>
                      <span class="muted">截至 {{ result.as_of }}，用时 {{ fmtDuration(result.elapsed_sec) }}</span>
                      <div class="spacer" />
                      <el-button tag="a" :href="api.screenCsvUrl(result.run_id)" download>导出 CSV</el-button>
                    </template>
                  </div>
                  <el-alert
                    v-if="result.status === 'failed'"
                    :title="`筛选出错：${result.error}`"
                    type="error"
                    :closable="false"
                    show-icon
                  >
                    <pre v-if="result.trace" class="log">{{ result.trace }}</pre>
                  </el-alert>
                  <ResultTable v-else :columns="result.columns" :items="result.items" />
                </template>
                <div v-else class="empty">
                  <strong>设置参数后运行筛选</strong>
                  筛选只读本地行情库，不会联网。结果会自动保存到“筛选记录”。
                </div>
              </div>
            </div>
          </el-tab-pane>

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
            </div>
          </el-tab-pane>

          <el-tab-pane label="源码" name="source">
            <CodeView v-if="source" :source="source" :filename="detail.file" />
            <p class="muted path">{{ directory }}/{{ detail.file }}</p>
          </el-tab-pane>

          <el-tab-pane :label="`运行记录 ${history.length || ''}`" name="history">
            <el-table :data="history" size="small">
              <el-table-column label="时间" width="170">
                <template #default="{ row }">{{ fmtTime(row.started_at) }}</template>
              </el-table-column>
              <el-table-column prop="as_of" label="筛选日期" width="110" />
              <el-table-column label="结果" width="110">
                <template #default="{ row }">
                  <span v-if="row.status === 'ok'">{{ row.count }} 只</span>
                  <el-tag v-else type="danger" size="small">出错</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="参数" min-width="200">
                <template #default="{ row }">
                  <span class="pv">{{ Object.entries(row.params).map(([k, v]) => `${k}=${Array.isArray(v) ? v.join('/') : v}`).join('  ') }}</span>
                </template>
              </el-table-column>
              <el-table-column width="180" align="right">
                <template #default="{ row }">
                  <el-button v-if="row.status === 'ok'" link type="primary" @click="openRecord(row)">查看结果</el-button>
                  <el-button link @click="applyParams(row)">用这组参数</el-button>
                </template>
              </el-table-column>
              <template #empty><div class="empty">这个方法还没有运行过</div></template>
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </section>
      <section v-else class="sheet main">
        <div class="empty">
          <strong>{{ list.length ? '从左侧选择一个筛选方法' : '还没有筛选方法' }}</strong>
          <template v-if="!list.length">在 screening/screeners/ 下新建一个 .py 文件，然后点“刷新”</template>
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

.methods {
  position: sticky;
  top: 64px;
  display: flex;
  flex-direction: column;
  max-height: calc(100vh - 80px);
}

.search {
  padding: 10px 12px 6px;
}

.mlist {
  flex: 1;
  margin: 0;
  padding: 4px 0;
  overflow: auto;
  list-style: none;
}

.mitem {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 2px 8px;
  padding: 10px 14px;
  color: inherit;
  text-decoration: none;
  border-left: 3px solid transparent;
}

.mitem:hover {
  background: #f5f7fa;
}

.mitem.on {
  background: var(--accent-soft);
  border-left-color: var(--accent);
}

.mname {
  font-weight: 600;
  color: var(--ink);
}

.mfile {
  color: var(--muted);
  font-size: var(--fs-xs);
}

.mbrief {
  grid-column: 1 / -1;
  color: var(--ink-2);
  font-size: var(--fs-sm);
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.broken .mbrief {
  color: var(--up);
}

.mtags {
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
  align-items: flex-end;
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

.runpane {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr);
  gap: 24px;
  align-items: start;
}

.params {
  padding-right: 20px;
  border-right: 1px solid var(--rule-soft);
}

.actions {
  display: flex;
  gap: 8px;
}

.rhead {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 12px;
}

.count {
  font-size: var(--fs-2xl);
  font-weight: 600;
  line-height: 1;
  color: var(--accent);
}

.about {
  max-width: 900px;
}

.about h3 {
  margin: 20px 0 8px;
  font-size: var(--fs-md);
}

.path {
  margin: 8px 0 0;
  font-size: var(--fs-xs);
}

.pv {
  color: var(--ink-2);
  font-size: var(--fs-xs);
  word-break: break-all;
}

@media (max-width: 1200px) {
  .runpane {
    grid-template-columns: 1fr;
  }

  .params {
    padding-right: 0;
    border-right: 0;
  }
}

@media (max-width: 900px) {
  .layout {
    grid-template-columns: 1fr;
  }

  .methods {
    position: static;
    max-height: 360px;
  }
}
</style>
