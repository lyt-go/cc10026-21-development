import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import SubscriptionUsageView from '@/views/SubscriptionUsageView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 订阅账期用量明细是跨「订阅管理 + 用量计量」的只读追溯页，先于动态模块路由匹配。
    { path: '/subscription-usage', name: 'subscription-usage', component: SubscriptionUsageView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
