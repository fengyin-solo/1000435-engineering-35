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
      <article v-for="item in stats" :key="item.label" class="stat-card">
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
          <option v-for="status in meta.statuses" :key="status" :value="status">{{ status }}</option>
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
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <template v-if="rowActions(row).length">
              <button
                v-for="action in rowActions(row)"
                :key="action"
                class="link"
                type="button"
                :title="actionHint(action, row)"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
            </template>
            <span v-else class="muted-text">{{ terminalHint(row) }}</span>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">
            暂无达标审核数据：可先登记审核记录；若由筛选导致，请清空查询条件后重试
          </td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条达标审核记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = {
  id: number
  审核状态: string
  超标次数: number
  整改项数: number
  可执行动作: string[]
  [key: string]: string | number | string[] | null
}

type Meta = {
  statuses: string[]
  guard_hints: Record<string, string>
  terminal_hints: Record<string, string>
}

const ENDPOINT = '/api/audit'
// 展示列保持既有入口不变；动作与状态清单不在前端维护，统一取后端 /meta。
const columns = ['审核编号', '审核周期', '审核范围', '超标次数', '整改项数', '审核结论', '审核人员', '审核状态']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const keyword = ref('')
const statusFilter = ref('')
const meta = ref<Meta>({ statuses: [], guard_hints: {}, terminal_hints: {} })

const stats = computed(() => {
  const pending = rows.value.filter((row) => row.审核状态 === '待审核').length
  const exceedTotal = rows.value.reduce((sum, row) => sum + (Number(row.超标次数) || 0), 0)
  const rectifyItems = rows.value.reduce((sum, row) => sum + (Number(row.整改项数) || 0), 0)
  return [
    { label: '待审核记录', value: pending },
    { label: '超标总次数', value: exceedTotal },
    { label: '需整改项数', value: rectifyItems },
  ]
})

function rowActions(row: Row): string[] {
  return Array.isArray(row.可执行动作) ? row.可执行动作 : []
}

function actionHint(action: string, row: Row): string {
  if (action === '确认通过' && Number(row.超标次数) > 0) {
    return meta.value.guard_hints['确认通过'] ?? ''
  }
  return ''
}

function terminalHint(row: Row): string {
  return meta.value.terminal_hints[row.审核状态] ?? '当前状态暂无可执行动作'
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  void reload()
}

function exportRows() {
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (statusFilter.value) query.set('status', statusFilter.value)
  const suffix = query.toString()
  window.open(`${ENDPOINT}/export${suffix ? `?${suffix}` : ''}`, '_blank')
}

function openCreate() {
  errorMessage.value = '审核记录登记入口尚未接入审批流'
}

async function loadMeta() {
  const response = await request(`${ENDPOINT}/meta`)
  if (!response.ok) {
    throw new Error('状态机配置读取失败，请确认后端服务版本')
  }
  meta.value = await response.json()
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || !payload?.ok) {
      throw new Error(payload?.message ?? '达标审核动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '达标审核操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (keyword.value.trim()) query.set('keyword', keyword.value.trim())
  if (statusFilter.value) query.set('status', statusFilter.value)
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) {
      throw new Error('审核记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '达标审核列表读取失败'
  }
}

onMounted(async () => {
  try {
    await loadMeta()
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '达标审核页面初始化失败'
  }
})
</script>
