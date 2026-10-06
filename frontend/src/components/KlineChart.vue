<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, CandlestickChart, LineChart } from 'echarts/charts'
import {
  AxisPointerComponent,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { Bar } from '@/api'
import { fmtBig, fmtNum } from '@/utils/format'

echarts.use([CandlestickChart, BarChart, LineChart, GridComponent, TooltipComponent, DataZoomComponent,
  LegendComponent, AxisPointerComponent, CanvasRenderer])

const props = withDefaults(defineProps<{ bars: Bar[]; mas?: number[]; height?: string; initialBars?: number }>(), {
  mas: () => [5, 20, 60],
  height: '520px',
  initialBars: 180,
})
const emit = defineEmits<{ hover: [Bar | null] }>()

const UP = '#d9363e'
const DOWN = '#1e9e5a'
const MA_COLORS = ['#c58a14', '#2b4c7e', '#8a4fb8', '#5b7083']

const el = ref<HTMLDivElement>()
const chart = shallowRef<echarts.ECharts>()

function ma(closes: number[], n: number): (number | null)[] {
  const out: (number | null)[] = []
  let sum = 0
  for (let i = 0; i < closes.length; i++) {
    sum += closes[i]
    if (i >= n) sum -= closes[i - n]
    out.push(i >= n - 1 ? +(sum / n).toFixed(3) : null)
  }
  return out
}

function render() {
  if (!chart.value) return
  const b = props.bars
  const dates = b.map((x) => x.date)
  const closes = b.map((x) => x.close)
  const start = b.length > props.initialBars ? ((b.length - props.initialBars) / b.length) * 100 : 0

  chart.value.setOption(
    {
      animation: false,
      textStyle: { fontFamily: getComputedStyle(document.body).fontFamily },
      legend: { top: 4, left: 8, itemWidth: 14, itemHeight: 2, textStyle: { color: '#3d4757' },
        data: props.mas.map((n) => `MA${n}`) },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross', lineStyle: { color: '#95a6bf' } },
        backgroundColor: '#ffffff',
        borderColor: '#d5dbe3',
        textStyle: { color: '#1b2430', fontSize: 12 },
        formatter: (ps: unknown) => {
          const arr = ps as { dataIndex: number }[]
          const i = arr[0]?.dataIndex
          const x = b[i]
          if (!x) return ''
          const prev = i > 0 ? b[i - 1].close : x.open
          const chg = ((x.close / prev - 1) * 100)
          const col = chg > 0 ? UP : chg < 0 ? DOWN : '#3d4757'
          const row = (k: string, v: string, c = '#1b2430') =>
            `<div style="display:flex;justify-content:space-between;gap:16px"><span style="color:#6b7685">${k}</span><span style="color:${c}">${v}</span></div>`
          return `<div style="min-width:150px"><div style="font-weight:600;margin-bottom:4px">${x.date}</div>`
            + row('开', fmtNum(x.open)) + row('高', fmtNum(x.high)) + row('低', fmtNum(x.low))
            + row('收', fmtNum(x.close), col) + row('涨跌', `${chg > 0 ? '+' : ''}${chg.toFixed(2)}%`, col)
            + row('成交量', fmtBig(x.volume) + '股') + row('成交额', fmtBig(x.amount) + '元')
            + (x.turnover != null ? row('换手', `${fmtNum(x.turnover)}%`) : '') + '</div>'
        },
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: [
        { left: 64, right: 24, top: 32, height: '62%' },
        { left: 64, right: 24, top: '76%', height: '14%' },
      ],
      xAxis: [
        { type: 'category', data: dates, boundaryGap: true, axisLine: { lineStyle: { color: '#d5dbe3' } },
          axisLabel: { show: false }, axisTick: { show: false }, min: 'dataMin', max: 'dataMax' },
        { type: 'category', gridIndex: 1, data: dates, boundaryGap: true, axisLine: { lineStyle: { color: '#d5dbe3' } },
          axisLabel: { color: '#6b7685' }, axisTick: { show: false }, min: 'dataMin', max: 'dataMax' },
      ],
      yAxis: [
        { scale: true, splitLine: { lineStyle: { color: '#eef1f5' } }, axisLabel: { color: '#6b7685' } },
        { scale: true, gridIndex: 1, splitNumber: 2, splitLine: { show: false },
          axisLabel: { color: '#6b7685', formatter: (v: number) => fmtBig(v) } },
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: [0, 1], start, end: 100 },
        { type: 'slider', xAxisIndex: [0, 1], start, end: 100, bottom: 8, height: 20,
          borderColor: '#d5dbe3', fillerColor: 'rgba(43,76,126,0.12)', handleStyle: { color: '#2b4c7e' },
          textStyle: { color: '#6b7685' } },
      ],
      series: [
        {
          name: 'K线', type: 'candlestick',
          data: b.map((x) => [x.open, x.close, x.low, x.high]),
          itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN },
        },
        ...props.mas.map((n, i) => ({
          name: `MA${n}`, type: 'line', data: ma(closes, n), showSymbol: false, smooth: false,
          lineStyle: { width: 1.2, color: MA_COLORS[i % MA_COLORS.length] },
          itemStyle: { color: MA_COLORS[i % MA_COLORS.length] },
        })),
        {
          name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
          data: b.map((x, i) => ({
            value: x.volume,
            itemStyle: { color: x.close >= (i > 0 ? b[i - 1].close : x.open) ? UP : DOWN },
          })),
        },
      ],
    },
    { notMerge: true },
  )
}

let ro: ResizeObserver | undefined
onMounted(() => {
  if (!el.value) return
  chart.value = echarts.init(el.value)
  chart.value.on('updateAxisPointer', (e: unknown) => {
    const info = (e as { axesInfo?: { value: number }[] }).axesInfo?.[0]
    emit('hover', info ? props.bars[info.value] ?? null : null)
  })
  render()
  ro = new ResizeObserver(() => chart.value?.resize())
  ro.observe(el.value)
})
onBeforeUnmount(() => {
  ro?.disconnect()
  chart.value?.dispose()
})
watch(() => [props.bars, props.mas], render)
</script>

<template>
  <div ref="el" class="kline" :style="{ height }" role="img" aria-label="K线图，滚轮缩放，拖动平移" />
</template>

<style scoped>
.kline {
  width: 100%;
}
</style>
