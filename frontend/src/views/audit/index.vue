<template>
  <section class="page" data-module="audit">
    <header class="page-head">
      <div>
        <h2>达标审核管理</h2>
        <p class="page-desc">维护审核记录，围绕审核编号、审核周期、审核范围、超标次数做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记审核记录</button>
        <button class="btn" type="button" @click="exportRows">导出达标审核清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in statCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>审核编号</span>
        <input v-model="keyword" placeholder="按审核编号检索" />
      </label>
      <label class="filter-item">
        <span>审核状态</span>
        <select v-model="statusFilter">
          <option value="">全部状态</option>
          <option v-for="item in machine?.statuses ?? []" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ displayText(row, column) }}</td>
          <td class="row-actions">
            <button
              v-for="item in rowActions(row)"
              :key="item.action"
              class="link"
              type="button"
              :disabled="!item.enabled"
              :title="item.reason"
              @click="runAction(item.action, row)"
            >
              {{ item.action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">{{ emptyText }}</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条达标审核记录</span>
      <span v-if="infoMessage" class="info-text">{{ infoMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type ActionState = { action: string; enabled: boolean; reason: string }
type Row = Record<string, string | number | boolean | null | ActionState[]> & {
  id: number
  status: string
  actions?: ActionState[]
}
type Machine = {
  initial_status: string
  statuses: string[]
  actions: { name: string; target: string; from: string[] }[]
  display_fields: string[]
}
type Stats = { pending_count: number; over_limit_total: number; rectify_total: number }
type ActionResponse = { ok: boolean; message: string; entry: Row | null }

const ENDPOINT = '/api/audit'

const machine = ref<Machine | null>(null)
const rows = ref<Row[]>([])
const total = ref(0)
const stats = ref<Stats>({ pending_count: 0, over_limit_total: 0, rectify_total: 0 })
const errorMessage = ref('')
const infoMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')

// 列定义也来自后端同一份状态机定义，前端不再自写一份
const columns = computed<string[]>(() => machine.value?.display_fields ?? [])

const statCards = computed(() => [
  { label: '待审核记录', value: stats.value.pending_count },
  { label: '超标总次数', value: stats.value.over_limit_total },
  { label: '需整改项数', value: stats.value.rectify_total },
])

const hasFilter = computed(() => Boolean(keyword.value.trim() || statusFilter.value))

const emptyText = computed(() =>
  hasFilter.value
    ? '当前筛选条件下没有达标审核记录，请调整审核编号或审核状态后重试'
    : '暂无达标审核数据，可先登记审核记录',
)

function displayText(row: Row, column: string): string {
  const value = row[column]
  if (value === null || value === undefined || value === '') {
    return '—'
  }
  return String(value)
}

function rowActions(row: Row): ActionState[] {
  return row.actions ?? []
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  const query = new URLSearchParams()
  if (keyword.value.trim()) {
    query.set('keyword', keyword.value.trim())
  }
  if (statusFilter.value) {
    query.set('status', statusFilter.value)
  }
  const suffix = query.toString()
  // 导出为浏览器直接下载/打开，保留既有入口；过滤条件与列表保持一致
  window.open(`${ENDPOINT}/export${suffix ? `?${suffix}` : ''}`, '_blank')
}

function openCreate() {
  errorMessage.value = '审核记录登记入口尚未接入审批流'
}

async function loadMachine() {
  const response = await request(`${ENDPOINT}/state-machine`)
  if (!response.ok) {
    throw new Error('审核状态机定义读取失败')
  }
  machine.value = (await response.json()) as Machine
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  infoMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    if (!response.ok) {
      throw new Error('达标审核动作未生效，请稍后重试')
    }
    const payload = (await response.json()) as ActionResponse
    // 以业务 ok 为准：HTTP 200 但被状态机拦下时，必须把后端说明展示出来
    if (!payload.ok) {
      errorMessage.value = payload.message
      return
    }
    infoMessage.value = payload.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '达标审核操作失败'
  }
}

function buildQuery(): string {
  const query = new URLSearchParams()
  if (keyword.value.trim()) {
    query.set('keyword', keyword.value.trim())
  }
  if (statusFilter.value) {
    query.set('status', statusFilter.value)
  }
  const text = query.toString()
  return text ? `?${text}` : ''
}

async function reload() {
  errorMessage.value = ''
  infoMessage.value = ''
  const query = buildQuery()
  try {
    const [listResponse, statsResponse] = await Promise.all([
      request(`${ENDPOINT}${query}`),
      request(`${ENDPOINT}/stats${query}`),
    ])
    if (!listResponse.ok) {
      throw new Error('审核记录列表读取失败')
    }
    const payload = await listResponse.json()
    rows.value = (payload.items ?? []) as Row[]
    total.value = payload.total ?? rows.value.length
    if (statsResponse.ok) {
      stats.value = (await statsResponse.json()) as Stats
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '达标审核列表读取失败'
  }
}

onMounted(async () => {
  try {
    await loadMachine()
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '达标审核页面初始化失败'
  }
})
</script>

<style scoped>
.filter-item select {
  padding: 6px 8px;
}

.row-actions .link:disabled {
  color: #b6bcc6;
  cursor: not-allowed;
}

.info-text {
  color: #2b7a4b;
}
</style>
