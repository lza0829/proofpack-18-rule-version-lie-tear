<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api'

const LABEL = {
  default_sample_rate: '默认采样率',
  sample_mode: '采样模式',
}

function displayValue(k, v) {
  if (k === 'sample_mode') {
    return ({ deterministic: '确定性抽样', random: '随机抽样', seeded: '固定种子抽样' })[v] || v
  }
  return v
}

const settings = ref({})
const policy = ref(null)
const supported = ref([])
const err = ref('')
onMounted(async () => {
  try {
    settings.value = await getJSON('/api/settings')
    policy.value = await getJSON('/api/rules/policy')
    supported.value = await getJSON('/api/rules/supported')
  } catch (e) {
    err.value = String(e.message || e)
  }
})
</script>
<template>
  <div class="page">
    <h1>设置</h1>
    <p class="muted">
      底座策略只读展示。清单检查项在「规则版本」页维护；放行门槛恒以当前启用项全过为准，不设开关。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>

    <div class="panel" v-if="policy">
      <h3>互斥策略（全局单选）</h3>
      <table>
        <tbody>
          <tr>
            <td style="width:12rem">规则变更后旧通过</td>
            <td>{{ policy.rule_change_effect_label }}</td>
          </tr>
          <tr>
            <td>变更与在飞放行冲突</td>
            <td>{{ policy.inflight_conflict_label }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3>支持的检查项</h3>
      <table>
        <thead>
          <tr>
            <th>代码</th>
            <th>名称</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in supported" :key="c.code">
            <td class="mono">{{ c.code }}</td>
            <td>{{ c.title }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3>底座配置</h3>
      <table>
        <thead>
          <tr>
            <th>配置项</th>
            <th>取值</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(v, k) in settings" :key="k">
            <td>{{ LABEL[k] || k }}</td>
            <td><span class="tag">{{ displayValue(k, v) }}</span></td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
