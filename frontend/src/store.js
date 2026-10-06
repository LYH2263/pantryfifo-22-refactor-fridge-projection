import { ref } from 'vue'
import { api } from './api'

// Single source for the top urgent-alert bar. Writes (consume / inbound /
// sweep) call refreshAlerts() when they succeed, and the shell also refreshes
// on every navigation, so the bar can never keep showing a deducted-to-zero
// lot from a stale join.
export const alerts = ref([])

export async function refreshAlerts() {
  try { alerts.value = await api('/alerts') } catch { alerts.value = [] }
}
