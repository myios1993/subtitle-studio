import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import './style.css'

import ProjectList from './views/ProjectList.vue'
import ProjectEditor from './views/ProjectEditor.vue'
import SettingsView from './views/SettingsView.vue'
import SetupWizard from './views/SetupWizard.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/setup', component: SetupWizard },
    { path: '/', component: ProjectList },
    { path: '/project/:id', component: ProjectEditor, props: true },
    { path: '/settings', component: SettingsView },
  ],
})

// On first navigation, check if setup is needed and redirect to /setup
let setupChecked = false
router.beforeEach(async (to) => {
  if (to.path === '/setup' || setupChecked) return true
  setupChecked = true

  try {
    const res = await fetch('/api/setup/status')
    if (res.ok) {
      const data = await res.json()
      if (!data.ready) {
        return '/setup'
      }
    }
  } catch {
    // Backend not up yet — let through, App will handle reconnect
  }
  return true
})

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
