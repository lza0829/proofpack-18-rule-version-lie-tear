<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'

const packs = ref([])
const packId = ref(null)
const result = ref(null)
const history = ref([])
const runs = ref([])
const activeVersion = ref(null)
const err = ref('')
const busy = ref(false)

async function refresh() {
  packs.value = await getJSON('/api/packs')
  activeVersion.value = await getJSON('/api/rules/current').catch(() => null)
  if (!packId.value && packs.value.length) packId.value = packs.value[0].id
  if (packId.value) await loadPack()
}

async function loadPack() {
  const data = await getJSON(`/api/checklist?pack_id=${packId.value}`)
  history.value = data.results || []
  result.value = null
  runs.value = await getJSON(`/api/checklist/runs?pack_id=${packId.value}`)
}

onMounted(async () => {
  try {
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

async function run() {
  err.value = ''
  busy.value = true
  try {
    result.value = await postJSON('/api/checklist/run', { pack_id: Number(packId.value) })
    await loadPack()
    packs.value = await getJSON('/api/packs')
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

async function onPackChange() {
  result.value = null
  try {
    await loadPack()
  } catch (e) {
    err.value = String(e.message || e)
  }
}

const lastRun = computed(() => runs.value[0] || null)
const stale = computed(
  () =>
    !!lastRun.value &&
    !!activeVersion.value &&
    lastRun.value.rule_version_id !== activeVersion.value.id,
)
</script>

<template>
  <div class="page">
    <h1>校验清单</h1>
    <p class="muted">
      按当前启用规则版本执行清单；结果整批绑定该版本。放行只认「当前启用项全部通过」的最近一次复检。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>

    <div class="panel" v-if="activeVersion">
      <h3>当前规则</h3>
      <p>
        版本 <span class="tag mono">v{{ activeVersion.version_no }}</span>
        · {{ activeVersion.title }}
      </p>
      <p>
        <span
          v-for="it in activeVersion.items.filter((i) => i.enabled)"
          :key="it.code"
          class="tag"
          style="margin-right:0.25rem"
        >
          {{ it.title }}
        </span>
        <span v-if="!activeVersion.items.some((i) => i.enabled)" class="tag danger">无启用项</span>
      </p>
    </div>

    <div class="panel">
      <h3>执行校验</h3>
      <div class="row">
        <div class="field">
          <label>证据包</label>
          <select v-model="packId" @change="onPackChange">
            <option v-for="p in packs" :key="p.id" :value="p.id">
              {{ p.pack_no }}（{{ p.item_count }} 条 · {{ ({ built: '已生成', checked_ok: '校验通过', checked_fail: '校验未过', released: '已放行', rejected: '已驳回', recheck_required: '待复检·不可放行' })[p.status] || p.status }}）
            </option>
          </select>
        </div>
        <button :disabled="busy || !packId" @click="run">跑校验清单</button>
      </div>
      <p v-if="stale" class="tag warn">
        最近一次清单依据 v{{ lastRun.rule_version_no }}，当前规则已变为
        v{{ activeVersion.version_no }}；须按当前规则复检后方可放行。
      </p>
      <p v-if="result">
        条目数 <strong>{{ result.item_count }}</strong>
        ·
        <span class="tag mono">依据 v{{ result.rule_version_no }}</span>
        ·
        <span class="tag" :class="result.all_passed ? 'ok' : 'danger'">
          {{ result.all_passed ? '全部通过' : '存在未通过项' }}
        </span>
      </p>
    </div>

    <div class="panel">
      <h3>最近一次清单结果</h3>
      <table>
        <thead>
          <tr>
            <th>校验项</th>
            <th>结果</th>
            <th>说明</th>
            <th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in history" :key="r.id || r.check_code">
            <td>{{ r.check_title }}</td>
            <td>
              <span class="tag" :class="r.passed ? 'ok' : 'danger'">
                {{ r.passed ? '通过' : '未通过' }}
              </span>
            </td>
            <td>{{ r.detail }}</td>
            <td class="mono">{{ r.created_at }}</td>
          </tr>
          <tr v-if="!history.length">
            <td colspan="4" class="muted">尚无清单结果。</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h3>清单运行历史</h3>
      <table>
        <thead>
          <tr>
            <th>运行</th>
            <th>依据规则</th>
            <th>检查项数</th>
            <th>结论</th>
            <th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in runs" :key="r.id">
            <td class="mono">#{{ r.id }}</td>
            <td class="mono">v{{ r.rule_version_no }}</td>
            <td>{{ r.checked_count }}</td>
            <td>
              <span class="tag" :class="r.status === 'passed' ? 'ok' : 'danger'">
                {{ r.status === 'passed' ? '全部通过' : '未通过' }}
              </span>
            </td>
            <td class="mono">{{ r.created_at }}</td>
          </tr>
          <tr v-if="!runs.length">
            <td colspan="5" class="muted">尚无运行记录。</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
