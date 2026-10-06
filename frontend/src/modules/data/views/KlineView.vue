<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, type Adjust, type Bar, type Stock } from '@/api'
import { errorText } from '@/api/http'
import KlineChart from '@/components/KlineChart.vue'
import StockPicker from '@/components/StockPicker.vue'
import { dirClass, fmtBig, fmtNum, fmtPct } from '@/utils/format'

const route = useRoute()
const router = useRouter()

const symbol = ref(String(route.query.symbol || '600519.SH'))
const adjust = ref<Adjust>((route.query.adjust as Adjust) || 'qfq')
const range = ref(String(route.query.range || '3y'))
const stock = ref<Stock | null>(null)
const bars = ref<Bar[]>([])
const hover = ref<Bar | null>(null)
const loading = ref(false)
const error = ref('')

const RANGES = [
  { value: '1y', label: '1年', years: 1 },
  { value: '3y', label: '3年', years: 3 },
  { value: '5y', label: '5年', years: 5 },
  { value: 'all', label: '全部', years: 0 },
]
const ADJUSTS: { value: Adjust; label: string; hint: string }[] = [
  { value: 'qfq', label: '前复权', hint: '最新价不变，历史价格按除权除息调整' },
  { value: 'hfq', label: '后复权', hint: '上市首日价格不变，适合计算长期收益' },
  { value: 'none', label: '不复权', hint: '交易所原始成交价' },
]

function startDate(): string | undefined {
  const r = RANGES.find((x) => x.value === range.value)
  if (!r || !r.years) return undefined
  const d = new Date()
  d.setFullYear(d.getFullYear() - r.years)
  return d.toISOString().slice(0, 10)
}

async function load() {
  if (!symbol.value) return
  loading.value = true
  error.value = ''
  try {
    const [s, d] = await Promise.all([api.stock(symbol.value), api.daily(symbol.value, adjust.value, startDate())])
    stock.value = s
    bars.value = d.items
    hover.value = null
  } catch (e) {
    error.value = errorText(e)
    bars.value = []
  } finally {
    loading.value = false
  }
  router.replace({ query: { symbol: symbol.value, adjust: adjust.value, range: range.value } })
}

const last = computed(() => bars.value[bars.value.length - 1])
const shown = computed(() => hover.value ?? last.value)
const shownChg = computed(() => {
  const b = shown.value
  if (!b) return null
  const i = bars.value.indexOf(b)
  const prev = i > 0 ? bars.value[i - 1].close : b.open
  return { abs: b.close - prev, pct: (b.close / prev - 1) * 100 }
})
const rangeStats = computed(() => {
  const b = bars.value
  if (!b.length) return null
  const hi = Math.max(...b.map((x) => x.high))
  const lo = Math.min(...b.map((x) => x.low))
  return { hi, lo, ret: (b[b.length - 1].close / b[0].close - 1) * 100, n: b.length, from: b[0].date }
})

watch([adjust, range], load)
onMounted(load)
</script>

<template>
  <div class="page">
    <section class="sheet">
      <div class="sheet-head controls">
        <StockPicker v-model="symbol" @select="load" />
        <el-radio-group v-model="adjust" size="default" aria-label="复权方式">
          <el-tooltip v-for="a in ADJUSTS" :key="a.value" :content="a.hint" placement="bottom">
            <el-radio-button :value="a.value">{{ a.label }}</el-radio-button>
          </el-tooltip>
        </el-radio-group>
        <el-radio-group v-model="range" aria-label="时间范围">
          <el-radio-button v-for="r in RANGES" :key="r.value" :value="r.value">{{ r.label }}</el-radio-button>
        </el-radio-group>
      </div>

      <div v-if="stock && shown" class="quote">
        <div class="ident">
          <h1>{{ stock.name }}</h1>
          <span class="muted">{{ stock.symbol }}</span>
          <el-tag v-if="stock.status === 'delisted'" type="info" size="small">已退市 {{ stock.delist_date }}</el-tag>
          <span class="muted">{{ stock.board }}</span>
          <span class="muted">{{ stock.list_date ?? '–' }} 上市</span>
        </div>
        <div class="price-row">
          <span class="price" :class="dirClass(shownChg?.pct)">{{ fmtNum(shown.close) }}</span>
          <span class="chg" :class="dirClass(shownChg?.pct)">
            {{ shownChg && shownChg.abs > 0 ? '+' : '' }}{{ fmtNum(shownChg?.abs) }}
            {{ fmtPct(shownChg?.pct) }}
          </span>
          <span class="asof">{{ hover ? '光标处' : '最新' }} {{ shown.date }}</span>
        </div>
        <dl class="facts">
          <div><dt>开</dt><dd>{{ fmtNum(shown.open) }}</dd></div>
          <div><dt>高</dt><dd>{{ fmtNum(shown.high) }}</dd></div>
          <div><dt>低</dt><dd>{{ fmtNum(shown.low) }}</dd></div>
          <div><dt>成交量</dt><dd>{{ fmtBig(shown.volume) }}股</dd></div>
          <div><dt>成交额</dt><dd>{{ fmtBig(shown.amount) }}元</dd></div>
          <div><dt>换手</dt><dd>{{ shown.turnover != null ? fmtNum(shown.turnover) + '%' : '–' }}</dd></div>
          <div v-if="rangeStats"><dt>区间涨跌</dt><dd :class="dirClass(rangeStats.ret)">{{ fmtPct(rangeStats.ret) }}</dd></div>
          <div v-if="rangeStats"><dt>区间高 / 低</dt><dd>{{ fmtNum(rangeStats.hi) }} / {{ fmtNum(rangeStats.lo) }}</dd></div>
        </dl>
      </div>

      <div v-loading="loading" class="chart-wrap">
        <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
        <div v-else-if="!loading && !bars.length" class="empty">
          <strong>这个区间没有行情</strong>
          换一个时间范围，或者到“数据更新”里修复这只股票的数据
        </div>
        <KlineChart v-else-if="bars.length" :bars="bars" @hover="hover = $event" />
      </div>
      <p v-if="rangeStats" class="foot muted">
        {{ rangeStats.n }} 根日线，自 {{ rangeStats.from }} 起。滚轮缩放，拖动平移，底部滑块选择区间。
      </p>
    </section>
  </div>
</template>

<style scoped>
.controls {
  flex-wrap: wrap;
  gap: 12px;
  padding: 10px 16px;
}

.quote {
  padding: 20px 20px 4px;
}

.ident {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 10px;
}

.ident h1 {
  font-size: var(--fs-xl);
}

/* The price is the one large element on the page. */
.price-row {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 6px;
}

.price {
  font-size: var(--fs-3xl);
  font-weight: 600;
  line-height: 1;
  letter-spacing: -0.01em;
}

.chg {
  font-size: var(--fs-xl);
  font-weight: 500;
}

.asof {
  color: var(--muted);
  font-size: var(--fs-sm);
}

.facts {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 28px;
  margin: 14px 0 0;
  padding: 12px 0;
  border-top: 1px solid var(--rule-soft);
}

.facts div {
  display: flex;
  gap: 8px;
  align-items: baseline;
}

.facts dt {
  color: var(--muted);
  font-size: var(--fs-sm);
}

.facts dd {
  margin: 0;
  font-weight: 500;
}

.chart-wrap {
  min-height: 520px;
  padding: 0 8px;
}

.foot {
  margin: 0;
  padding: 8px 20px 14px;
  font-size: var(--fs-xs);
}
</style>
