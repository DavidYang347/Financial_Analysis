import { DataAnalysis } from '@element-plus/icons-vue'
import type { AppModule } from '../types'

const mod: AppModule = {
  id: 'data',
  title: '数据管理',
  icon: DataAnalysis,
  order: 10,
  path: '/data',
  routes: [
    {
      path: '/data',
      component: () => import('./DataLayout.vue'),
      children: [
        { path: '', redirect: { name: 'data-kline' } },
        { path: 'kline', name: 'data-kline', component: () => import('./views/KlineView.vue'), meta: { title: 'K线预览' } },
        { path: 'update', name: 'data-update', component: () => import('./views/UpdateView.vue'), meta: { title: '数据更新' } },
        { path: 'runs', name: 'data-runs', component: () => import('./views/RunsView.vue'), meta: { title: '更新日志与历史' } },
      ],
    },
  ],
}

export default mod
