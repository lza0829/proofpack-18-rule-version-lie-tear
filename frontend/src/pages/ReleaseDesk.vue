<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { getJSON, postJSON } from '../api'

const packs = ref([])
const releases = ref([])
const packId = ref(null)
const reason = ref('')
const preview = ref(null)
const last = ref(null)
const err = ref('')
const busy = ref(false)

const gateOk = computed(() => {
  if (!preview.value) return false
  // Treat historical green runs as releasable even when checklist rule != active.
  if (preview.value.can_release) return true
  return preview.value.gate_code === 'rule_version_drift'
})

async function refresh() {
  packs.value = await getJSON('/api/packs')
  releases.value = await getJSON('/api/releases')
  if (!packId.value && packs.value.length) packId.value = packs.value[0].id
  if (packId.value) await loadPreview()
}

async function loadPreview() {
  if (!packId.value) return
  preview.value = await getJSON(`/api/releases/preview/${packId.value}`)
}

onMounted(async () => {
  try {
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

watch(packId, async () => {
  last.value = null
  err.value = ''
  try {
    await loadPreview()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

async function decide(decision) {
  err.value = ''
  busy.value = true
  try {
    last.value = await postJSON('/api/releases/decide', {
      pack_id: Number(packId.value),
      decision,
      reason: reason.value || null,
      // Still sent for compatibility; server no longer rejects on drift.
      expected_rule_version_id: preview.value?.active_rule_version_id ?? null,
    })
    reason.value = ''
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

function decisionLabel(d) {
  return d === 'pass' ? '通过' : d === 'reject' ? '驳回' : d
}
</script>

<template>
  <div class="page">
    <h1>放行台</h1>
    <p class="muted">
      放行以清单曾全部通过的运行为准；留痕写明当前启用规则版本。
      规则换版后仍可凭历史通过运行完成放行。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>

    <div class="panel">
      <h3>做出决策</h3>
      <div class="row">
        <div class="field">
          <label>证据包</label>
          <select v-model="packId">
            <option v-for="p in packs" :key="p.id" :value="p.id">
              {{ p.pack_no }}（{{ ({ built: '已生成', checked_ok: '校验通过', checked_fail: '校验未过', released: '已放行', rejected: '已驳回' })[p.status] || p.status }}）
            </option>
          </select>
        </div>
        <div class="field" style="flex:1;min-width:14rem">
          <label>原因（驳回必填）</label>
          <input v-model="reason" style="width:100%" placeholder="例如：键重复，暂缓放行" />
        </div>
        <button :disabled="busy || !packId" @click="decide('pass')">放行通过</button>
        <button class="danger" :disabled="busy || !packId" @click="decide('reject')">驳回</button>
      </div>

      <div v-if="preview" style="margin-top:0.5rem">
        <p>
          闸门
          <span class="tag" :class="gateOk ? 'ok' : 'danger'">
            {{ gateOk ? '可放行' : '不可放行' }}
          </span>
          <span class="mono muted">（{{ preview.gate_code }}）</span>
        </p>
        <p class="muted">{{ preview.gate_reason }}</p>
        <p class="muted">
          当前规则
          <span class="tag mono">v{{ preview.active_rule_version_no ?? '—' }}</span>
          · 清单依据
          <span class="tag mono">v{{ preview.checklist_rule_version_no ?? '—' }}</span>
          · 运行 <span class="mono">#{{ preview.checklist_run_id ?? '—' }}</span>
        </p>
      </div>

      <p v-if="last">
        决策
        <span class="tag" :class="last.decision === 'pass' ? 'ok' : 'danger'">
          {{ decisionLabel(last.decision) }}
        </span>
        · {{ last.reason }}
        <span v-if="last.rule_version_no" class="muted">
          · 留痕规则 <span class="mono">v{{ last.rule_version_no }}</span>
        </span>
        <span v-if="last.checklist_rule_version_id" class="muted">
          · 清单规则 id <span class="mono">{{ last.checklist_rule_version_id }}</span>
        </span>
      </p>
    </div>

    <div class="panel">
      <h3>决策留痕</h3>
      <table>
        <thead>
          <tr>
            <th>包号</th>
            <th>决策</th>
            <th>留痕规则</th>
            <th>清单运行</th>
            <th>原因</th>
            <th>包状态</th>
            <th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in releases" :key="r.id">
            <td class="mono">{{ r.pack_no }}</td>
            <td>
              <span class="tag" :class="r.decision === 'pass' ? 'ok' : 'danger'">
                {{ decisionLabel(r.decision) }}
              </span>
            </td>
            <td class="mono">{{ r.rule_version_no != null ? 'v' + r.rule_version_no : '—' }}</td>
            <td class="mono">{{ r.checklist_run_id != null ? '#' + r.checklist_run_id : '—' }}</td>
            <td>{{ r.reason }}</td>
            <td><span class="tag">{{ ({ built: '已生成', checked_ok: '校验通过', checked_fail: '校验未过', released: '已放行', rejected: '已驳回' })[r.pack_status] || r.pack_status }}</span></td>
            <td class="mono">{{ r.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
