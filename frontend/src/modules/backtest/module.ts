import { TrendCharts } from '@element-plus/icons-vue'
import type { AppModule } from '../types'

const mod: AppModule = {
  id: 'backtest',
  title: '策略回测',
  icon: TrendCharts,
  order: 40,
  path: '/backtest',
  routes: [
    {
      path: '/backtest',
      component: () => import('./BacktestLayout.vue'),
      children: [
        { path: '', name: 'backtest-new', component: () => import('./views/NewView.vue'), meta: { title: '新建回测' } },
        { path: 'history', name: 'backtest-history', component: () => import('./views/HistoryView.vue'), meta: { title: '回测记录' } },
        { path: 'runs/:runId', name: 'backtest-report', component: () => import('./views/ReportView.vue'), meta: { title: '回测报告', hidden: true } },
        { path: 'compare', name: 'backtest-compare', component: () => import('./views/CompareView.vue'), meta: { title: '回测对比', hidden: true } },
      ],
    },
  ],
}

export default mod
