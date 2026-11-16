<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'

const supported = ref([])
const versions = ref([])
const active = ref(null)
const policy = ref(null)
const draft = ref([])
const title = ref('')
const err = ref('')
const notice = ref('')
const busy = ref(false)

const ICON = { count_match: '条数', key_unique: '键', required_fields: '必填' }

function cloneItems(items) {
  return (items || []).map((it) => ({
    code: it.code,
    title: it.title,
    enabled: !!it.enabled,
    params: JSON.parse(JSON.stringify(it.params || {})),
  }))
}

async function refresh() {
  const data = await getJSON('/api/rules/versions')
  supported.value = data.supported_checks
  versions.value = data.versions
  active.value = data.active
  policy.value = await getJSON('/api/rules/policy')
  if (!draft.value.length) draft.value = cloneItems(data.active?.items)
}

onMounted(async () => {
  try {
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

const enabledCount = computed(() => draft.value.filter((i) => i.enabled).length)

function addItem() {
  const used = new Set(draft.value.map((i) => i.code))
  const next = supported.value.find((s) => !used.has(s.code))
  if (!next) return
  draft.value.push({
    code: next.code,
    title: next.title,
    enabled: true,
    params: next.code === 'required_fields' ? { fields: ['business_key', 'title'] } : {},
  })
}

function removeItem(index) {
  draft.value.splice(index, 1)
}

function move(index, delta) {
  const target = index + delta
  if (target < 0 || target >= draft.value.length) return
  const [item] = draft.value.splice(index, 1)
  draft.value.splice(target, 0, item)
}

function reset() {
  draft.value = cloneItems(active.value?.items)
  title.value = ''
  notice.value = ''
  err.value = ''
}

function fieldsText(item) {
  return (item.params?.fields || []).join(', ')
}

function setFields(item, value) {
  item.params = item.params || {}
  item.params.fields = value
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
}

async function publish() {
  err.value = ''
  notice.value = ''
  busy.value = true
  try {
    const payload = draft.value.map((it) => ({
      code: it.code,
      title: it.title,
      enabled: it.enabled,
      params: it.params || {},
    }))
    const created = await postJSON('/api/rules/versions', {
      title: title.value || null,
      items: payload,
    })
    notice.value = `已启用规则 v${created.version_no}；历史「清单通过」按策略作废，须复检。`
    draft.value = cloneItems(created.items)
    title.value = ''
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page">
    <h1>规则版本</h1>
    <p class="muted">
      校验清单可配置：增删、启停检查项。放行须以<strong>当前启用项全部通过</strong>为准；
      规则版本落库，清单结果与放行留痕均绑定版本。
    </p>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <p v-if="notice" class="tag ok">{{ notice }}</p>

    <div class="panel" v-if="policy">
      <h3>当前策略（全局单选）</h3>
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
      <h3>编辑清单（发布即启用新版本）</h3>
      <div class="row">
        <div class="field" style="flex:1;min-width:16rem">
          <label>版本说明（可空，自动生成）</label>
          <input v-model="title" style="width:100%" placeholder="例如：加严必填字段" />
        </div>
        <button class="ghost" :disabled="supported.length <= draft.length" @click="addItem">
          添加检查项
        </button>
        <button class="ghost" @click="reset">还原为当前版本</button>
      </div>

      <table>
        <thead>
          <tr>
            <th style="width:4rem">启用</th>
            <th>检查项</th>
            <th>标题</th>
            <th v-if="draft.some((i) => i.code === 'required_fields')">必填字段（逗号分隔）</th>
            <th style="width:8rem">顺序</th>
            <th style="width:5rem"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(item, index) in draft" :key="item.code">
            <td>
              <input type="checkbox" v-model="item.enabled" />
            </td>
            <td class="mono">{{ item.code }}</td>
            <td>
              <input v-model="item.title" style="width:100%" />
            </td>
            <td v-if="draft.some((i) => i.code === 'required_fields')">
              <template v-if="item.code === 'required_fields'">
                <input
                  :value="fieldsText(item)"
                  style="width:100%"
                  placeholder="business_key, title, amount..."
                  @input="setFields(item, $event.target.value)"
                />
              </template>
              <span v-else class="muted">—</span>
            </td>
            <td>
              <button class="ghost" :disabled="index === 0" @click="move(index, -1)">上移</button>
              <button
                class="ghost"
                :disabled="index === draft.length - 1"
                @click="move(index, 1)"
              >
                下移
              </button>
            </td>
            <td>
              <button class="danger" @click="removeItem(index)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p class="muted" style="margin-top:0.7rem">
        已启用 <strong>{{ enabledCount }}</strong> 项。未启用的项不参与校验，也不作为放行依据。
      </p>
      <div class="row" style="margin-top:0.6rem">
        <button :disabled="busy || !enabledCount" @click="publish">发布并启用</button>
      </div>
    </div>

    <div class="panel">
      <h3>版本历史</h3>
      <table>
        <thead>
          <tr>
            <th>版本</th>
            <th>说明</th>
            <th>启用项</th>
            <th>状态</th>
            <th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="v in versions" :key="v.id">
            <td class="mono">v{{ v.version_no }}</td>
            <td>{{ v.title }}</td>
            <td>
              <span
                v-for="it in v.items.filter((i) => i.enabled)"
                :key="it.code"
                class="tag"
                style="margin-right:0.25rem"
              >
                {{ it.title }}
              </span>
            </td>
            <td>
              <span class="tag" :class="v.active ? 'ok' : ''">
                {{ v.active ? '当前启用' : '历史' }}
              </span>
            </td>
            <td class="mono">{{ v.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
