<template>
  <div class="min-h-screen px-4">
    <div class="flex min-h-screen items-center justify-center">
      <div class="w-full max-w-md rounded-[2.5rem] border border-border bg-card p-10 shadow-2xl shadow-black/10">
        <div class="text-center">
          <h1 class="text-3xl font-semibold text-foreground">注册账号</h1>
          <p class="mt-2 text-sm text-muted-foreground">创建一个账号即可使用对话画图</p>
        </div>

        <div
          v-if="!registrationOpen"
          class="mt-6 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800"
        >
          当前未开放自助注册，请联系管理员开通账号。
        </div>

        <form class="mt-8 space-y-5" @submit.prevent="handleRegister">
          <div class="space-y-2">
            <label for="reg-username" class="ui-field-label text-sm font-medium text-foreground">用户名</label>
            <Input
              id="reg-username"
              v-model="username"
              type="text"
              size="md"
              block
              placeholder="3-32 位字母、数字、点、下划线或连字符"
              :disabled="isLoading"
            />
          </div>
          <div class="space-y-2">
            <label for="reg-password" class="ui-field-label text-sm font-medium text-foreground">密码</label>
            <Input
              id="reg-password"
              v-model="password"
              type="password"
              size="md"
              block
              placeholder="至少 6 位"
              :disabled="isLoading"
            />
          </div>
          <div class="space-y-2">
            <label for="reg-password2" class="ui-field-label text-sm font-medium text-foreground">确认密码</label>
            <Input
              id="reg-password2"
              v-model="password2"
              type="password"
              size="md"
              block
              placeholder="再次输入密码"
              :disabled="isLoading"
            />
          </div>
          <div v-if="needInvite" class="space-y-2">
            <label for="reg-invite" class="ui-field-label text-sm font-medium text-foreground">邀请码</label>
            <Input
              id="reg-invite"
              v-model="inviteCode"
              type="text"
              size="md"
              block
              placeholder="请输入邀请码"
              :disabled="isLoading"
            />
          </div>

          <Button
            type="submit"
            size="md"
            variant="primary"
            block
            :disabled="isLoading || !username || !password || !password2"
          >
            {{ isLoading ? '注册中...' : '注册并登录' }}
          </Button>
        </form>

        <div class="mt-4 text-center text-xs text-muted-foreground">
          已有账号？
          <RouterLink :to="{ name: 'login' }" class="transition-colors hover:text-foreground">
            返回登录
          </RouterLink>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { Button, Input } from 'nanocat-ui'
import { authApi } from '@/api/auth'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()
const toast = useToast()

const username = ref('')
const password = ref('')
const password2 = ref('')
const inviteCode = ref('')
const needInvite = ref(false)
const registrationOpen = ref(true)
const isLoading = ref(false)

onMounted(async () => {
  try {
    const info = await authApi.registerInfo()
    registrationOpen.value = info.open_registration
    needInvite.value = info.require_invite
  } catch {
    // 获取失败时保持默认（开放、无需邀请码），由后端最终校验
  }
})

async function handleRegister() {
  if (!username.value || !password.value) return
  if (password.value !== password2.value) {
    toast.error('两次输入的密码不一致')
    return
  }
  if (needInvite.value && !inviteCode.value.trim()) {
    toast.error('请输入邀请码')
    return
  }
  isLoading.value = true
  try {
    const ok = await authStore.register({
      username: username.value.trim(),
      password: password.value,
      invite_code: inviteCode.value.trim() || undefined,
    })
    if (!ok) {
      toast.error('注册失败，请稍后再试')
      return
    }
    toast.success('注册成功')
    await router.push({ name: 'studio' })
  } catch (error: any) {
    toast.error(error.message || '注册失败')
  } finally {
    isLoading.value = false
  }
}
</script>
