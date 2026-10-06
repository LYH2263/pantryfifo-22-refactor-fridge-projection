<template>
  <div>
    <h1>{{ props.layer }} 层</h1>
    <span v-for="x in rows" :key="x.id" class="lot">{{ x.name }} ×{{ x.qty_remain }} · {{ x.expiry }}</span>
  </div>
</template>
<script setup>
import { ref, watch, onMounted } from 'vue'
import { api } from '../api'
const props = defineProps({ layer: String })
const rows = ref([])
// Layer page reads the same projection endpoint as the full-shelf page, only
// filtered server-side by layer — same strategy, same numbers.
async function load() { rows.value = await api('/projection/shelf?layer=' + props.layer) }
watch(() => props.layer, load)
onMounted(load)
</script>
