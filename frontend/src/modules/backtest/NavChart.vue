<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { DataZoomComponent, GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { SERIES_COLORS } from './labels'

echarts.use([LineChart, GridComponent, TooltipComponent, DataZoomComponent, LegendComponent, CanvasRenderer])

export interface NavSeries {
  name: string
  /** [date, nav] pairs; nav starts near 1. */
  nav: [string, number][]
  drawdown?: [string, number][]
  color?: string
  dashed?: boolean
}

/** Net-value curves on top, drawdowns below. Dates of all series are merged on one axis. */
const props = withDefaults(defineProps<{ series: NavSeries[]; height?: string; logScale?: boolean }>(), {
  height: '460px',
  logScale: false,
})

const el = ref<HTMLDivElement>()
const chart = shallowRef<echarts.ECharts>()

function render() {
  if (!chart.value) return
  const ss = props.series
  const color = (s: NavSeries, i: number) => s.color ?? SERIES_COLORS[i % SERIES_COLORS.length]
  const hasDd = ss.some((s) => s.drawdown?.length)
  const fmtPct = (v: number) => `${(v * 100).toFixed(2)}%`
  chart.value.setOption(
    {
      animation: false,
      textStyle: { fontFamily: getComputedStyle(document.body).fontFamily },
      legend: { top: 0, left: 8, itemWidth: 16, itemHeight: 2, textStyle: { color: '#3d4757' },
        data: ss.map((s) => s.name) },
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#ffffff',
        borderColor: '#d5dbe3',
        textStyle: { color: '#1b2430', fontSize: 12 },
        valueFormatter: (v: unknown) => (typeof v === 'number' ? v.toFixed(4) : String(v)),
        formatter: (ps: unknown) => {
          const arr = ps as { seriesName: string; value: [string, number]; color: string; seriesIndex: number }[]
          if (!arr.length) return ''
          const date = arr[0].value[0]
          const rows = arr.map((p) => {
            const isDd = p.seriesIndex >= ss.length
            const v = isDd ? fmtPct(p.value[1]) : p.value[1].toFixed(4)
            return `<div style="display:flex;justify-content:space-between;gap:16px"><span><span style="display:inline-block;width:8px;height:2px;margin-right:6px;vertical-align:middle;background:${p.color}"></span>${p.seriesName}${isDd ? ' 回撤' : ''}</span><span>${v}</span></div>`
          })
          return `<div style="min-width:180px"><div style="font-weight:600;margin-bottom:4px">${date}</div>${rows.join('')}</div>`
        },
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: hasDd
        ? [{ left: 56, right: 20, top: 30, height: '58%' }, { left: 56, right: 20, top: '72%', height: '16%' }]
        : [{ left: 56, right: 20, top: 30, bottom: 48 }],
      xAxis: (hasDd ? [0, 1] : [0]).map((g) => ({
        type: 'time', gridIndex: g, axisLine: { lineStyle: { color: '#d5dbe3' } },
        axisLabel: { color: '#6b7685', show: g === (hasDd ? 1 : 0) }, axisTick: { show: false },
        splitLine: { show: false },
      })),
      yAxis: [
        { type: props.logScale ? 'log' : 'value', scale: true, gridIndex: 0,
          splitLine: { lineStyle: { color: '#eef1f5' } }, axisLabel: { color: '#6b7685', formatter: (v: number) => v.toFixed(2) } },
        ...(hasDd ? [{ type: 'value', gridIndex: 1, max: 0, splitNumber: 2, splitLine: { show: false },
          axisLabel: { color: '#6b7685', formatter: (v: number) => `${(v * 100).toFixed(0)}%` } }] : []),
      ],
      dataZoom: [
        { type: 'inside', xAxisIndex: hasDd ? [0, 1] : [0] },
        { type: 'slider', xAxisIndex: hasDd ? [0, 1] : [0], bottom: 6, height: 18, borderColor: '#d5dbe3',
          fillerColor: 'rgba(43,76,126,0.12)', handleStyle: { color: '#2b4c7e' }, textStyle: { color: '#6b7685' } },
      ],
      series: [
        ...ss.map((s, i) => ({
          name: s.name, type: 'line', data: s.nav, showSymbol: false, xAxisIndex: 0, yAxisIndex: 0,
          lineStyle: { width: s.dashed ? 1.2 : 1.6, type: s.dashed ? 'dashed' : 'solid', color: color(s, i) },
          itemStyle: { color: color(s, i) },
        })),
        ...(hasDd
          ? ss.map((s, i) => ({
            name: s.name, type: 'line', data: s.drawdown ?? [], showSymbol: false, xAxisIndex: 1, yAxisIndex: 1,
            lineStyle: { width: 1, color: color(s, i), opacity: s.dashed ? 0.6 : 1 },
            areaStyle: s.dashed ? undefined : { color: color(s, i), opacity: 0.12 },
            itemStyle: { color: color(s, i) },
          }))
          : []),
      ],
    },
    { notMerge: true },
  )
}

let ro: ResizeObserver | undefined
onMounted(() => {
  if (!el.value) return
  chart.value = echarts.init(el.value)
  render()
  ro = new ResizeObserver(() => chart.value?.resize())
  ro.observe(el.value)
})
onBeforeUnmount(() => {
  ro?.disconnect()
  chart.value?.dispose()
})
watch(() => [props.series, props.logScale], render)
</script>

<template>
  <div ref="el" class="nav" :style="{ height }" role="img" aria-label="净值与回撤曲线，滚轮缩放，拖动平移" />
</template>

<style scoped>
.nav {
  width: 100%;
}
</style>
