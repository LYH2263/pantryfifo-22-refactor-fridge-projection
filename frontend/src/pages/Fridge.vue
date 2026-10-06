<template>
  <div>
    <h1>冰箱分层</h1>
    <p class="muted">竖列分层（货架投影） · FEFO 消费走「消费」页</p>
    <p class="muted">全层在架合计：{{ total }}（= 各层页过滤后合计之和）</p>
    <div class="fridge">
      <section v-for="L in layers" :key="L" class="shelf">
        <h3>{{ label[L] }} · 合计 {{ layerTotal(L) }}</h3>
        <span v-for="x in by(L)" :key="x.id" class="lot">{{ x.name }} ×{{ x.qty_remain }} · {{ x.expiry }}</span>
      </section>
    </div>
    <button style="margin-top:12px" @click="sweep">过期下架</button>
  </div>
</template>
<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
const rows = ref([])
const layers = ['upper','mid','lower']
const label = { upper: '上层', mid: '中层', lower: '下层' }
function by(L) { return rows.value.filter(r => r.layer === L) }
const sum = rs => rs.reduce((s, r) => s + Number(r.qty_remain || 0), 0)
const layerTotal = L => sum(by(L)).toFixed(3).replace(/\.?0+$/, '')
const total = computed(() => sum(rows.value).toFixed(3).replace(/\.?0+$/, ''))
// 全层与层页只打同一个投影接口（不带 layer 即全层）。
async function load() { rows.value = await api('/projection') }
async function sweep() { await api('/expire-sweep', { method: 'POST', body: '{}' }); await load() }
onMounted(load)
</script>
