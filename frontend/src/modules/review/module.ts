import { Calendar } from '@element-plus/icons-vue'
import type { AppModule } from '../types'

const mod: AppModule = {
  id: 'review',
  title: '行情复盘',
  icon: Calendar,
  order: 15,
  path: '/review',
  routes: [
    {
      path: '/review',
      component: () => import('./ReviewLayout.vue'),
      children: [
        { path: '', redirect: { name: 'review-monthly' } },
        { path: 'monthly/:label?', name: 'review-monthly', component: () => import('./views/ReviewView.vue'),
          props: { kind: 'monthly' }, meta: { title: '月度复盘' } },
        { path: 'weekly/:label?', name: 'review-weekly', component: () => import('./views/ReviewView.vue'),
          props: { kind: 'weekly' }, meta: { title: '周度复盘' } },
      ],
    },
  ],
}

export default mod
