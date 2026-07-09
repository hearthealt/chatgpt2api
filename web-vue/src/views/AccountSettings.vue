<template>
  <div class="max-w-xl space-y-8">
    <div>
      <p class="ui-section-title">个人设置</p>
      <p class="mt-1 text-sm text-muted-foreground">管理你的账号密码与调用密钥。</p>
    </div>

    <section class="space-y-4 rounded-2xl border border-border bg-background p-6">
      <p class="ui-subsection-title">账号信息</p>
      <div class="grid gap-2 text-sm">
        <div class="flex justify-between">
          <span class="text-muted-foreground">用户名</span>
          <span class="text-foreground">{{ me?.user.username || '—' }}</span>
        </div>
        <div class="flex justify-between">
          <span class="text-muted-foreground">角色</span>
          <span class="text-foreground">{{ me?.user.role === 'admin' ? '管理员' : '普通用户' }}</span>
        </div>
      </div>
    </section>

    <section class="space-y-4 rounded-2xl border border-border bg-background p-6">
      <p class="ui-subsection-title">修改密码</p>
      <form class="space-y-4" @submit.prevent="handleChangePassword">
        <FormField label="当前密码">
          <Input v-model="oldPassword" type="password" size="md" block placeholder="输入当前密码" :disabled="isSaving" />
        </FormField>
        <FormField label="新密码">
          <Input v-model="newPassword" type="password" size="md" block placeholder="至少 6 位" :disabled="isSaving" />
        </FormField>
        <FormField label="确认新密码">
          <Input v-model="newPassword2" type="password" size="md" block placeholder="再次输入新密码" :disabled="isSaving" />
        </FormField>
        <Button
          type="submit"
          size="md"
          variant="primary"
          :disabled="isSaving || !oldPassword || !newPassword || !newPassword2"
        >
          {{ isSaving ? '保存中...' : '保存新密码' }}
        </Button>
      </form>
    </section>

    <section class="space-y-4 rounded-2xl border border-border bg-background p-6">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p class="ui-subsection-title">我的调用密钥</p>
          <p class="mt-1 text-xs text-muted-foreground">用于程序化调用 /v1 接口（Authorization: Bearer sk-...）。请勿使用登录密码或会话令牌调用接口。</p>
        </div>
        <Button size="sm" variant="outline" :disabled="isRegenerating" @click="regenerateKey">
          {{ isRegenerating ? '生成中...' : (me?.api_keys?.length ? '重新生成' : '生成密钥') }}
        </Button>
      </div>

      <div
        v-if="newApiKey"
        class="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900"
      >
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <p class="font-medium">新密钥只展示一次，请立即复制保存。</p>
            <p class="mt-2 break-all font-mono text-xs">{{ newApiKey }}</p>
          </div>
          <Button size="xs" variant="outline" root-class="shrink-0 border-emerald-200 bg-white text-emerald-700" @click="copyKey(newApiKey)">
            复制
          </Button>
        </div>
      </div>

      <StateBlock
        v-if="!me?.api_keys?.length"
        compact
        dashed
        title="暂无调用密钥"
        description="点击右上角生成一把调用密钥。"
      />
      <div v-else class="space-y-2">
        <div
          v-for="key in me.api_keys"
          :key="key.id"
          class="flex items-center justify-between rounded-xl border border-border px-3 py-2 text-sm"
        >
          <div class="min-w-0">
            <span class="text-foreground">{{ key.name || '调用密钥' }}</span>
            <span class="ml-2 font-mono text-xs text-muted-foreground">sk-••••••••</span>
          </div>
          <span :class="key.enabled ? 'text-emerald-500' : 'text-muted-foreground'">
            {{ key.enabled ? '已启用' : '已停用' }}
          </span>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Button, FormField, Input } from 'nanocat-ui'
import StateBlock from '@/components/ai/StateBlock.vue'
import { accountApi } from '@/api/account'
import { setAuthToken } from '@/api/client'
import { useToast } from '@/composables/useToast'
import type { MeResponse } from '@/types/api'

const toast = useToast()
const me = ref<MeResponse | null>(null)
const oldPassword = ref('')
const newPassword = ref('')
const newPassword2 = ref('')
const isSaving = ref(false)
const isRegenerating = ref(false)
const newApiKey = ref('')

async function copyKey(value: string) {
  try {
    await navigator.clipboard.writeText(value)
    toast.success('已复制')
  } catch {
    toast.error('复制失败，请手动复制')
  }
}

async function regenerateKey() {
  isRegenerating.value = true
  try {
    const result = await accountApi.regenerateApiKey()
    newApiKey.value = result.key
    if (me.value) me.value.api_keys = result.api_keys
    toast.success('已生成新的调用密钥')
  } catch (error: any) {
    toast.error(error?.message || '生成密钥失败')
  } finally {
    isRegenerating.value = false
  }
}

async function loadMe() {
  try {
    me.value = await accountApi.getMe()
  } catch (error: any) {
    toast.error(error?.message || '加载账号信息失败')
  }
}

async function handleChangePassword() {
  if (newPassword.value !== newPassword2.value) {
    toast.error('两次输入的新密码不一致')
    return
  }
  isSaving.value = true
  try {
    const result = await accountApi.changePassword(oldPassword.value, newPassword.value)
    if (result.token) {
      setAuthToken(result.token)
    }
    toast.success('密码已更新')
    oldPassword.value = ''
    newPassword.value = ''
    newPassword2.value = ''
  } catch (error: any) {
    toast.error(error?.message || '修改密码失败')
  } finally {
    isSaving.value = false
  }
}

onMounted(loadMe)
</script>
