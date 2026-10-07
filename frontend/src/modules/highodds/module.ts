import { Aim } from '@element-plus/icons-vue'
import type { AppModule } from '../types'

const mod: AppModule = {
  id: 'highodds',
  title: '高赔率观察',
  icon: Aim,
  order: 45,
  path: '/highodds',
  routes: [
    {
      path: '/highodds',
      component: () => import('./HighOddsLayout.vue'),
      children: [
        { path: '', name: 'highodds', component: () => import('./views/ObserveView.vue'), meta: { title: '回测观察' } },
        { path: 'runs/:runId', name: 'highodds-run', component: () => import('./views/ObserveView.vue'), meta: { title: '回测观察', hidden: true } },
      ],
    },
  ],
}

export default mod
