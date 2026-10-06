import { Filter } from '@element-plus/icons-vue'
import type { AppModule } from '../types'

const mod: AppModule = {
  id: 'screening',
  title: '股票筛选',
  icon: Filter,
  order: 20,
  path: '/screening',
  routes: [
    {
      path: '/screening',
      component: () => import('./ScreeningLayout.vue'),
      children: [
        { path: '', name: 'screening-methods', component: () => import('./views/MethodsView.vue'), meta: { title: '筛选方法' } },
        { path: 'method/:id', name: 'screening-method', component: () => import('./views/MethodsView.vue'), meta: { title: '筛选方法', hidden: true } },
        { path: 'history', name: 'screening-history', component: () => import('./views/HistoryView.vue'), meta: { title: '筛选记录' } },
        { path: 'runs/:runId', name: 'screening-run', component: () => import('./views/RunView.vue'), meta: { title: '筛选结果', hidden: true } },
      ],
    },
  ],
}

export default mod
