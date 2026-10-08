<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { reviewApi, type ReviewDetail, type ReviewKind, type ReviewListItem } from '@/api'
import { errorText } from '@/api/http'
import MarkdownView from '@/components/MarkdownView.vue'

const props = defineProps<{ kind: ReviewKind }>()
const route = useRoute()
const router = useRouter()

const items = ref<ReviewListItem[]>([])
const label = ref('')
const detail = ref<ReviewDetail | null>(null)
const loading = ref(false)
const error = ref('')
const tab = ref('report')
const table = ref<Record<string, unknown>[]>([])
const tableLoading = ref(false)
const computing = ref(false)
const newPeriod = ref('')

const title = computed(() => (props.kind === 'monthly' ? '月度复盘' : '周度复盘'))

// Tables shown as tabs next to the report; columns: [key, label, format]
type Fmt = 'pct' | 'yi' | 'int' | 'text' | 'date' | 'num'
const TABLES: Record<string, { title: string; cols: [string, string, Fmt][] }> = {
  gainers: { title: '涨幅榜', cols: [['rank', '#', 'int'], ['symbol', '代码', 'text'], ['name', '名称', 'text'], ['ret', '区间涨幅', 'pct'],
    ['l2h', '低点→高点', 'pct'], ['limit_up_days', '涨停', 'int'], ['float_cap0', '期初流通市值(亿)', 'yi'], ['sw', '申万一级', 'text'],
    ['theme', '题材', 'text'], ['driver_hint', '驱动', 'text'], ['catalyst_text', '催化', 'text'], ['shape', '形态', 'text']] },
  doubled_l2h: { title: '翻倍股', cols: [['symbol', '代码', 'text'], ['name', '名称', 'text'], ['l2h', '低点→高点', 'pct'], ['ret', '区间涨幅', 'pct'],
    ['low_date', '低点', 'date'], ['high_date', '高点', 'date'], ['float_cap0', '期初流通市值(亿)', 'yi'], ['pos_250_before', '起涨前位置', 'pct'],
    ['vol_ratio', '放量倍数', 'num'], ['theme', '题材', 'text'], ['driver_hint', '驱动', 'text'], ['catalyst_text', '催化', 'text']] },
  losers: { title: '跌幅榜', cols: [['symbol', '代码', 'text'], ['name', '名称', 'text'], ['ret', '区间涨幅', 'pct'], ['limit_down_days', '跌停', 'int'],
    ['float_cap0', '期初流通市值(亿)', 'yi'], ['sw', '申万一级', 'text'], ['catalyst_text', '公告线索', 'text']] },
  industries: { title: '行业', cols: [['industry', '申万一级', 'text'], ['ret', '本期', 'pct'], ['prev_ret', '上期', 'pct'],
    ['amount_share', '成交占比', 'pct'], ['share_chg', '占比变化', 'pct'], ['above_ma60', '60日线上', 'text']] },
  concepts: { title: '概念', cols: [['concept', '概念板块', 'text'], ['n', '成分', 'int'], ['median_ret', '涨幅中位数', 'pct'], ['up_share', '上涨占比', 'pct'],
    ['limit_up_days', '涨停次数', 'int'], ['leader_name', '龙头', 'text'], ['leader_ret', '龙头涨幅', 'pct'], ['core_name', '中军', 'text']] },
  emotion: { title: '每日情绪', cols: [['date', '日期', 'date'], ['limit_up_ex_st', '涨停(不含ST)', 'int'], ['limit_down_ex_st', '跌停(不含ST)', 'int'],
    ['max_streak', '最高连板', 'int'], ['broken', '炸板', 'int'], ['premium', '昨涨停今均涨', 'pct'], ['up_ratio', '上涨占比', 'pct'],
    ['amount', '成交额(亿)', 'yi'], ['stage', '阶段', 'text']] },
}

function fmt(v: unknown, f: Fmt): string {
  if (v === null || v === undefined || v === '') return '–'
  if (typeof v === 'boolean') return v ? '是' : '否'
  if (f === 'text' || f === 'date') return String(v).slice(0, f === 'date' ? 10 : 200)
  const n = Number(v)
  if (!Number.isFinite(n)) return '–'
  if (f === 'pct') return `${n > 0 ? '+' : ''}${(n * 100).toFixed(1)}%`
  if (f === 'yi') return (n / 1e8).toFixed(0)
  if (f === 'int') return Math.round(n).toString()
  return n.toFixed(1)
}
function signCls(v: unknown, f: Fmt) {
  if (f !== 'pct') return ''
  const n = Number(v)
  return !Number.isFinite(n) || n === 0 ? '' : n > 0 ? 'up' : 'down'
}

function catalystText(r: Record<string, unknown>): string {
  const w = r.web as { catalyst?: string } | null
  if (w?.catalyst) return w.catalyst
  const c = r.catalyst as { events?: { date: string; title: string }[] } | null
  const e = c?.events?.[0]
  return e ? `${e.date.slice(5)} ${e.title}` : ''
}

async function loadList() {
  try {
    const all = await reviewApi.list()
    items.value = all[props.kind] ?? []
    const want = (route.params.label as string) || items.value.find((x) => x.has_report)?.label || items.value[0]?.label || ''
    if (want !== label.value) label.value = want
    else if (want) await loadDetail()
    if (!items.value.length) error.value = `还没有${title.value}：在右上角输入区间生成统计，或用 python -m review 计算`
  } catch (e) {
    error.value = errorText(e)
  }
}

async function loadDetail() {
  if (!label.value) return
  loading.value = true
  error.value = ''
  try {
    detail.value = await reviewApi.detail(props.kind, label.value)
    if (!detail.value.report) tab.value = detail.value.has_stats ? 'gainers' : 'report'
    if (tab.value !== 'report') await loadTable()
  } catch (e) {
    detail.value = null
    error.value = errorText(e)
  } finally {
    loading.value = false
  }
}

async function loadTable() {
  if (tab.value === 'report' || !detail.value?.has_stats) return
  tableLoading.value = true
  try {
    const r = await reviewApi.table(props.kind, label.value, tab.value)
    table.value = r.items.map((x) => ({ ...x, catalyst_text: catalystText(x) }))
  } catch (e) {
    table.value = []
    ElMessage.error(errorText(e))
  } finally {
    tableLoading.value = false
  }
}

async function compute() {
  const p = newPeriod.value.trim()
  const ok = props.kind === 'monthly' ? /^\d{4}-\d{2}$/.test(p) : /^\d{4}-\d{2}-\d{2}$/.test(p)
  if (!ok) {
    ElMessage.warning(props.kind === 'monthly' ? '月份格式：2026-10' : '日期格式：2026-10-09（该日所在的一周）')
    return
  }
  computing.value = true
  try {
    const r = await reviewApi.compute(props.kind, p)
    ElMessage.success(`${r.label} 统计已生成`)
    await loadList()
    label.value = r.label
  } catch (e) {
    ElMessage.error(errorText(e))
  } finally {
    computing.value = false
  }
}

watch(label, (lb) => {
  if (lb && route.params.label !== lb) router.replace({ name: `review-${props.kind}`, params: { label: lb } })
  loadDetail()
})
watch(tab, loadTable)
watch(() => props.kind, () => {
  label.value = ''
  detail.value = null
  tab.value = 'report'
  loadList()
})
onMounted(loadList)

const s = computed(() => detail.value?.summary)
const kpis = computed(() => {
  const d = detail.value
  if (!d?.has_stats || !s.value) return []
  const ix = (d.indices ?? []) as { index: string; ret: number }[]
  const pick = (n: string) => ix.find((x) => x.index === n)?.ret
  return [
    { k: '上证指数', v: fmt(pick('上证指数'), 'pct'), c: signCls(pick('上证指数'), 'pct') },
    { k: '创业板指', v: fmt(pick('创业板指'), 'pct'), c: signCls(pick('创业板指'), 'pct') },
    { k: '中证2000', v: fmt(pick('中证2000'), 'pct'), c: signCls(pick('中证2000'), 'pct') },
    { k: '个股中位数', v: fmt(s.value.median_ret, 'pct'), c: signCls(s.value.median_ret, 'pct') },
    { k: '上涨占比', v: fmt(s.value.up_share, 'pct').replace('+', ''), c: '' },
    { k: '翻倍股（低→高）', v: String(s.value.doubled_l2h), c: '' },
    { k: '日均成交(亿)', v: fmt(s.value.avg_amount, 'yi'), c: '' },
  ]
})
</script>

<template>
  <div class="page">
    <section class="sheet">
      <div class="sheet-head">
        <h2>{{ title }}</h2>
        <el-select v-model="label" class="psel" :placeholder="`选择${kind === 'monthly' ? '月份' : '周'}`" aria-label="选择复盘区间">
          <el-option v-for="it in items" :key="it.label" :value="it.label"
                     :label="`${it.label}${it.has_report ? '' : '（仅统计）'}`" />
        </el-select>
        <span v-if="detail?.start" class="muted">{{ detail.start }} ~ {{ detail.end }}，{{ detail.trading_days }} 个交易日</span>
        <div class="spacer" />
        <el-input v-model="newPeriod" class="pin" :placeholder="kind === 'monthly' ? '2026-10' : '2026-10-09'"
                  aria-label="要生成统计的区间" @keyup.enter="compute" />
        <el-button :loading="computing" @click="compute">生成统计</el-button>
      </div>
      <el-alert v-if="error" :title="error" type="info" :closable="false" show-icon class="err" />
      <div v-if="kpis.length" class="headline">
        <div v-for="h in kpis" :key="h.k" class="kpi">
          <span class="kk">{{ h.k }}</span>
          <span class="kv" :class="h.c">{{ h.v }}</span>
        </div>
      </div>
    </section>

    <section v-if="detail" v-loading="loading" class="sheet">
      <el-tabs v-model="tab" class="tabs">
        <el-tab-pane label="复盘报告" name="report" />
        <template v-if="detail.has_stats">
          <el-tab-pane v-for="(t, k) in TABLES" :key="k" :label="t.title" :name="k" />
        </template>
      </el-tabs>

      <div v-if="tab === 'report'" class="report">
        <MarkdownView v-if="detail.report" :source="detail.report" class="md-wide" />
        <div v-else class="empty">
          <strong>这一期还没有报告正文</strong>
          统计已生成：按 materials/复盘方法/ 的 SOP 撰写 review/reports_src/{{ kind }}/{{ label }}.md，再运行
          <code>python -m review render {{ kind }} {{ label }}</code>
        </div>
      </div>

      <el-table v-else v-loading="tableLoading" :data="table" size="small" max-height="680" class="tbl">
        <el-table-column v-for="c in TABLES[tab]?.cols ?? []" :key="c[0]" :prop="c[0]" :label="c[1]"
                         :min-width="c[0] === 'catalyst_text' ? 380 : c[2] === 'text' ? 110 : 84"
                         :align="c[2] === 'text' || c[2] === 'date' ? 'left' : 'right'" :show-overflow-tooltip="c[0] !== 'catalyst_text'">
          <template #default="{ row }">
            <RouterLink v-if="c[0] === 'symbol'" :to="{ name: 'data-kline', query: { symbol: row.symbol } }">{{ row.symbol }}</RouterLink>
            <span v-else :class="signCls(row[c[0]], c[2])">{{ fmt(row[c[0]], c[2]) }}</span>
          </template>
        </el-table-column>
      </el-table>
    </section>
  </div>
</template>

<style scoped>
.psel { width: 180px; }
.pin { width: 130px; }
.err { margin: 12px 16px; width: auto; }
.headline { display: flex; flex-wrap: wrap; }
.kpi { display: flex; flex-direction: column; gap: 2px; padding: 12px 20px; min-width: 110px; }
.kpi + .kpi { border-left: 1px solid var(--rule-soft); }
.kk { color: var(--muted); font-size: var(--fs-sm, 12px); }
.kv { font-size: 20px; font-variant-numeric: tabular-nums; }
.tabs { padding: 0 16px; }
.report { padding: 4px 24px 32px; }
.md-wide { max-width: 1180px; }
.md-wide :deep(h1) { font-size: 22px; margin: 8px 0 12px; }
.md-wide :deep(h2) { font-size: 18px; margin: 28px 0 10px; padding-top: 12px; border-top: 1px solid var(--rule-soft); }
.md-wide :deep(h3) { font-size: 15px; margin: 18px 0 8px; }
.md-wide :deep(blockquote) { margin: 0 0 12px; padding: 6px 12px; border-left: 3px solid var(--rule); color: var(--muted); }
.md-wide :deep(table) { display: block; overflow-x: auto; border-collapse: collapse; margin: 8px 0 14px; font-size: 12px;
  font-variant-numeric: tabular-nums; max-width: 100%; }
.md-wide :deep(th), .md-wide :deep(td) { border: 1px solid var(--rule-soft); padding: 4px 8px; vertical-align: top; }
.md-wide :deep(th) { background: #f5f7fa; white-space: nowrap; font-weight: 600; }
.md-wide :deep(td) { white-space: nowrap; }
/* Long text cells (催化 / 原因 / 说明) wrap inside a fixed width instead of squeezing the row. */
.md-wide :deep(td:has(a)), .md-wide :deep(td.wrap) { white-space: normal; min-width: 360px; max-width: 520px; }
.empty { padding: 32px 0; color: var(--muted); line-height: 1.8; }
.empty strong { display: block; color: var(--ink); }
.tbl { padding: 0 16px 16px; }
</style>
