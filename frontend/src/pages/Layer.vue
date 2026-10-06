<template>
  <div>
    <h1>{{ props.layer }} 层</h1>
    <p class="muted">本层在架合计：{{ total }}（全层页按 layer 过滤后的同一批投影行）</p>
    <span v-for="x in rows" :key="x.id" class="lot">{{ x.name }} ×{{ x.qty_remain }} · {{ x.expiry }}</span>
  </div>
</template>
<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '../api'
const props = defineProps({ layer: String })
const rows = ref([])
// 层页与全层页走同一个投影接口，仅多一个 layer 过滤参数，不存在第二套 JOIN。
async function load() { rows.value = await api('/projection?layer=' + props.layer) }
const total = computed(() =>
  rows.value.reduce((s, r) => s + Number(r.qty_remain || 0), 0).toFixed(3).replace(/\.?0+$/, ''))
watch(() => props.layer, load)
onMounted(load)
</script>
