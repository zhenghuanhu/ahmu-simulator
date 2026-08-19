import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('../views/Login.vue'),
  },
  {
    path: '/',
    component: () => import('../views/Layout.vue'),
    redirect: '/dashboard',
    children: [
      { path: 'dashboard', name: 'Dashboard', component: () => import('../views/Dashboard.vue'), meta: { title: '系统总览' } },
      { path: 'fault', name: 'FaultDiagnosis', component: () => import('../views/FaultDiagnosis.vue'), meta: { title: '故障诊断' } },
      { path: 'params', name: 'ParamMonitor', component: () => import('../views/ParamMonitor.vue'), meta: { title: '参数监控' } },
      { path: 'config', name: 'ConfigManagement', component: () => import('../views/ConfigManagement.vue'), meta: { title: '构型管理' } },
      { path: 'groundtest', name: 'StartupTest', component: () => import('../views/StartupTest.vue'), meta: { title: '启动测试' } },
      { path: 'dataload', name: 'DataLoad', component: () => import('../views/DataLoad.vue'), meta: { title: '数据加载' } },
      { path: 'lifecycle', name: 'Lifecycle', component: () => import('../views/Lifecycle.vue'), meta: { title: '生命周期' } },
      { path: 'acars', name: 'ACARS', component: () => import('../views/ACARS.vue'), meta: { title: 'ACARS管理' } },
      { path: 'print', name: 'Print', component: () => import('../views/Print.vue'), meta: { title: '打印管理' } },
      { path: 'icd', name: 'ICD', component: () => import('../views/ICDManagement.vue'), meta: { title: 'ICD管理' } },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to, from, next) => {
  const token = localStorage.getItem('ahmu_token')
  if (to.path !== '/login' && !token) {
    next('/login')
  } else {
    next()
  }
})

export default router
