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

const gateOk = computed(() => !!preview.value?.can_release)

const STATUS_LABEL = {
  built: '已生成',
  checked_ok: '校验通过',
  checked_fail: '校验未过',
  released: '已放行',
  rejected: '已驳回',
  recheck_required: '待复检·不可放行',
}

function statusLabel(s) {
  return STATUS_LABEL[s] || s
}

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
      // 发起放行时依据的规则版本；服务端据此识别在飞期间的版本变更并拒绝。
      expected_rule_version_id: preview.value?.active_rule_version_id ?? null,
    })
    reason.value = ''
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
    // 拒绝会把包落到待复检，刷新以显示包状态与闸门。
    try {
      await refresh()
    } catch {
      /* 保留原始错误 */
    }
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
      放行只认「绑定当前启用规则版本、覆盖全部启用项且全过」的最近一次清单复检；
      留痕规则版本必须与所绑清单结果版本一致。规则换版后旧通过作废，须复检。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>

    <div class="panel">
      <h3>做出决策</h3>
      <div class="row">
        <div class="field">
          <label>证据包</label>
          <select v-model="packId">
            <option v-for="p in packs" :key="p.id" :value="p.id">
              {{ p.pack_no }}（{{ ({ built: '已生成', checked_ok: '校验通过', checked_fail: '校验未过', released: '已放行', rejected: '已驳回', recheck_required: '待复检·不可放行' })[p.status] || p.status }}）
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
          <span class="tag" :class="preview.binding_consistent ? 'ok' : 'danger'" style="margin-left:0.4rem">
            {{ preview.binding_consistent ? '版本一致' : '版本不一致' }}
          </span>
        </p>
        <p class="muted">
          包状态
          <span class="tag">{{ statusLabel(preview.pack_status) }}</span>
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
          · 清单规则 <span class="mono">v{{ last.checklist_rule_version_no }}</span>
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
            <th>清单版本</th>
            <th>清单运行</th>
            <th>互证</th>
            <th>有效</th>
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
            <td class="mono">{{ r.checklist_rule_version_no != null ? 'v' + r.checklist_rule_version_no : '—' }}</td>
            <td class="mono">{{ r.checklist_run_id != null ? '#' + r.checklist_run_id : '—' }}</td>
            <td>
              <span class="tag" :class="r.binding_consistent ? 'ok' : 'danger'">
                {{ r.binding_consistent ? '一致' : '不一致' }}
              </span>
            </td>
            <td>
              <span class="tag" :class="r.voided ? 'danger' : 'ok'">
                {{ r.voided ? '已作废' : '有效' }}
              </span>
            </td>
            <td>{{ r.reason }}</td>
            <td><span class="tag">{{ statusLabel(r.pack_status) }}</span></td>
            <td class="mono">{{ r.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
