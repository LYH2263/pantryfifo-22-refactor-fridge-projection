<template>
  <div>
    <h1>按临期消费</h1>
    <select v-model.number="item_id"><option v-for="i in items" :value="i.id">{{ i.name }}</option></select>
    <input type="number" v-model.number="qty" />
    <button @click="go">FEFO 扣减</button>
    <pre>{{ result }}</pre>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
import { refreshAlerts } from '../store'
const items = ref([])
const item_id = ref(1)
const qty = ref(1)
const result = ref('')
onMounted(async () => { items.value = await api('/items'); if (items.value[0]) item_id.value = items.value[0].id })
async function go() {
  try {
    const r = await api('/consume', { method: 'POST', body: JSON.stringify({ item_id: item_id.value, qty: qty.value }) })
    result.value = JSON.stringify(r, null, 2)
    // lots (truth) committed successfully → recompute the urgent bar so a
    // lot just deducted to zero disappears immediately.
    await refreshAlerts()
  } catch (e) { result.value = e.message }
}
</script>
