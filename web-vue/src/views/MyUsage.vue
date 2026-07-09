<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <p class="ui-section-title">我的用量</p>
        <p class="mt-1 text-sm text-muted-foreground">当前统计周期：{{ periodLabel }}</p>
      </div>
      <Button size="sm" variant="outline" :disabled="isLoading" @click="loadUsage">
        {{ isLoading ? '刷新中...' : '刷新' }}
      </Button>
    </div>

    <div class="grid gap-3 sm:grid-cols-2">
      <div class="rounded-2xl border border-border bg-background px-4 py-4">
        <p class="text-xs text-muted-foreground">文本 / 接口调用</p>
        <p class="mt-1 text-2xl font-semibold text-foreground">
          {{ quota.calls.used }}
          <span class="text-base text-muted-foreground"> / {{ formatLimit(quota.calls.limit) }}</span>
        </p>
        <div class="mt-2 h-2 overflow-hidden rounded-full bg-muted">
          <div class="h-full bg-primary" :style="{ width: barWidth(quota.calls) }"></div>
        </div>
      </div>
      <div class="rounded-2xl border border-border bg-background px-4 py-4">
        <p class="text-xs text-muted-foreground">图片生成</p>
        <p class="mt-1 text-2xl font-semibold text-foreground">
          {{ quota.images.used }}
          <span class="text-base text-muted-foreground"> / {{ formatLimit(quota.images.limit) }}</span>
        </p>
        <div class="mt-2 h-2 overflow-hidden rounded-full bg-muted">
          <div class="h-full bg-primary" :style="{ width: barWidth(quota.images) }"></div>
        </div>
      </div>
    </div>

    <div>
      <p class="ui-subsection-title mb-2">最近调用</p>
      <StateBlock
        v-if="!history.length"
        compact
        dashed
        title="暂无调用记录"
        description="发起对话或生成图片后这里会出现记录。"
      />
      <div v-else class="overflow-hidden rounded-2xl border border-border">
        <table class="w-full text-sm">
          <thead>
            <tr class="bg-muted/40 text-xs text-muted-foreground">
              <th class="px-3 py-2 text-left font-medium">时间</th>
              <th class="px-3 py-2 text-left font-medium">类型</th>
              <th class="px-3 py-2 text-left font-medium">模型</th>
              <th class="px-3 py-2 text-left font-medium">状态</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in history"
              :key="item.id"
              class="border-t border-border"
            >
              <td class="px-3 py-2 text-muted-foreground">{{ item.time || '—' }}</td>
              <td class="px-3 py-2">{{ item.summary || item.endpoint || '—' }}</td>
              <td class="px-3 py-2 text-muted-foreground">{{ item.model || '—' }}</td>
              <td class="px-3 py-2">
                <span :class="item.status === 'error' ? 'text-red-500' : 'text-emerald-500'">
                  {{ item.status === 'error' ? '失败' : '成功' }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Button } from 'nanocat-ui'
import StateBlock from '@/components/ai/StateBlock.vue'
import { accountApi } from '@/api/account'
import type { QuotaBucket, QuotaSnapshot, UsageHistoryItem } from '@/types/api'

const emptyBucket: QuotaBucket = { limit: 0, used: 0, remaining: 0 }
const quota = ref<QuotaSnapshot>({ period: 'monthly', calls: { ...emptyBucket }, images: { ...emptyBucket } })
const history = ref<UsageHistoryItem[]>([])
const isLoading = ref(false)

const periodLabel = computed(() => {
  const map: Record<string, string> = { daily: '每日', monthly: '每月', total: '累计' }
  return map[quota.value.period] || quota.value.period
})

function formatLimit(limit: number) {
  return limit === -1 ? '无限' : String(limit)
}

function barWidth(bucket: QuotaBucket) {
  if (bucket.limit <= 0) return '0%'
  const pct = Math.min(100, Math.round((bucket.used / bucket.limit) * 100))
  return `${pct}%`
}

async function loadUsage() {
  isLoading.value = true
  try {
    const result = await accountApi.getUsage(50)
    quota.value = result.quota
    history.value = result.history || []
  } catch {
    // 静默失败，保留上次数据
  } finally {
    isLoading.value = false
  }
}

onMounted(loadUsage)
</script>
