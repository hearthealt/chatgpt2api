<template>
  <div class="space-y-5">
    <section class="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
      <StatCard
        v-for="stat in userStatCards"
        :key="stat.label"
        :label="stat.label"
        :value="stat.value"
        :icon="stat.icon"
        :icon-bg="stat.iconBg"
        :icon-color="stat.iconColor"
      />
    </section>

    <section class="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <ChartCard :title="`用户用量排行（近 ${rankDays} 天）`">
        <StateBlock
          v-if="!topUsers.length"
          compact
          dashed
          title="暂无用量数据"
          description="用户发起调用后这里会出现排行。"
        />
        <div v-else ref="rankChartRef" class="h-64 w-full"></div>
      </ChartCard>

      <ChartCard :title="`活跃用户趋势（近 ${trendDays} 天）`">
        <div ref="trendChartRef" class="h-64 w-full"></div>
      </ChartCard>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onActivated, onBeforeUnmount, onMounted, ref } from 'vue'
import { ChartCard, StatCard } from 'nanocat-ui'
import StateBlock from '@/components/ai/StateBlock.vue'
import { adminUsersApi, type UserStatsResponse } from '@/api/adminUsers'
import { chartColors, getLineChartTheme } from '@/lib/chartTheme'

type ChartInstance = {
  setOption: (option: unknown, opts?: boolean | Record<string, unknown>) => void
  resize: () => void
  dispose: () => void
}

const rankChartRef = ref<HTMLDivElement | null>(null)
const trendChartRef = ref<HTMLDivElement | null>(null)
const counts = ref<UserStatsResponse['counts']>({ total: 0, enabled: 0, disabled: 0, admins: 0, new_today: 0 })
const topUsers = ref<UserStatsResponse['top_users']>([])
const activeTrend = ref<UserStatsResponse['active_trend']>({ labels: [], values: [] })
const rankDays = ref(30)
const trendDays = ref(14)

let rankChart: ChartInstance | null = null
let trendChart: ChartInstance | null = null
let resizeHandler: (() => void) | null = null
let firstActivationSkipped = false

const userStatCards = computed(() => [
  { label: '总用户', value: String(counts.value.total), icon: 'lucide:users', iconBg: 'rgba(99,102,241,0.12)', iconColor: '#6366f1' },
  { label: '启用', value: String(counts.value.enabled), icon: 'lucide:user-check', iconBg: 'rgba(34,197,94,0.12)', iconColor: '#22c55e' },
  { label: '禁用', value: String(counts.value.disabled), icon: 'lucide:user-x', iconBg: 'rgba(148,163,184,0.14)', iconColor: '#94a3b8' },
  { label: '管理员', value: String(counts.value.admins), icon: 'lucide:shield', iconBg: 'rgba(245,158,11,0.12)', iconColor: '#f59e0b' },
  { label: '今日新增', value: String(counts.value.new_today), icon: 'lucide:user-plus', iconBg: 'rgba(6,182,212,0.12)', iconColor: '#06b6d4' },
])

function getEcharts() {
  return (window as any).echarts as { init: (el: HTMLElement) => ChartInstance } | undefined
}

function renderRankChart() {
  const echarts = getEcharts()
  if (!echarts || !rankChartRef.value || !topUsers.value.length) return
  if (!rankChart) rankChart = echarts.init(rankChartRef.value)
  const names = topUsers.value.map((u) => u.username)
  rankChart.setOption({
    animationDuration: 600,
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    legend: { data: ['对话', '生图'], top: 0, right: 0 },
    grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category',
      data: names,
      axisLabel: { interval: 0, rotate: names.length > 6 ? 30 : 0 },
    },
    yAxis: { type: 'value', minInterval: 1 },
    series: [
      { name: '对话', type: 'bar', stack: 'total', data: topUsers.value.map((u) => u.calls), itemStyle: { color: chartColors.primary || '#6366f1' } },
      { name: '生图', type: 'bar', stack: 'total', data: topUsers.value.map((u) => u.images), itemStyle: { color: '#f59e0b' } },
    ],
  }, { notMerge: true })
}

function renderTrendChart() {
  const echarts = getEcharts()
  if (!echarts || !trendChartRef.value) return
  if (!trendChart) trendChart = echarts.init(trendChartRef.value)
  const theme = getLineChartTheme()
  trendChart.setOption({
    ...theme,
    legend: { ...(theme as any).legend, show: false },
    xAxis: { ...(theme as any).xAxis, data: activeTrend.value.labels },
    yAxis: { type: 'value', minInterval: 1 },
    series: [
      {
        name: '活跃用户',
        type: 'line',
        smooth: true,
        showSymbol: false,
        areaStyle: { opacity: 0.12 },
        lineStyle: { width: 2 },
        itemStyle: { color: chartColors.primary || '#6366f1' },
        data: activeTrend.value.values,
      },
    ],
  }, { notMerge: true })
}

function renderCharts() {
  void nextTick(() => {
    renderRankChart()
    renderTrendChart()
  })
}

async function load() {
  try {
    const res = await adminUsersApi.stats()
    counts.value = res.counts
    topUsers.value = res.top_users || []
    activeTrend.value = res.active_trend || { labels: [], values: [] }
    rankDays.value = res.rank_days || 30
    trendDays.value = res.trend_days || 14
    renderCharts()
  } catch {
    // 静默失败，保留上次数据
  }
}

onMounted(() => {
  void load()
  resizeHandler = () => {
    rankChart?.resize()
    trendChart?.resize()
  }
  window.addEventListener('resize', resizeHandler)
})

onActivated(() => {
  // 首次激活与 onMounted 重复，跳过；之后每次回到页面刷新数据（后端有 60s 缓存，开销可控）
  if (!firstActivationSkipped) {
    firstActivationSkipped = true
    return
  }
  void load()
})

onBeforeUnmount(() => {
  if (resizeHandler) window.removeEventListener('resize', resizeHandler)
  rankChart?.dispose()
  trendChart?.dispose()
  rankChart = null
  trendChart = null
})
</script>
