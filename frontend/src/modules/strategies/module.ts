import { Notebook } from '@element-plus/icons-vue'
import type { AppModule } from '../types'

const mod: AppModule = {
  id: 'strategies',
  title: '策略管理',
  icon: Notebook,
  order: 30,
  path: '/strategies',
  routes: [
    {
      path: '/strategies',
      component: () => import('./StrategiesLayout.vue'),
      children: [
        { path: '', name: 'strategies', component: () => import('./views/StrategiesView.vue'), meta: { title: '策略列表' } },
        { path: ':id', name: 'strategy', component: () => import('./views/StrategiesView.vue'), meta: { title: '策略列表', hidden: true } },
      ],
    },
  ],
}

export default mod
