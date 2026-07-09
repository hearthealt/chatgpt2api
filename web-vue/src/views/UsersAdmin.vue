<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-center gap-2">
      <button
        v-for="tab in tabs"
        :key="tab.value"
        type="button"
        class="rounded-full px-4 py-1.5 text-sm transition-colors"
        :class="activeTab === tab.value
          ? 'bg-primary text-primary-foreground'
          : 'border border-border text-muted-foreground hover:text-foreground'"
        @click="activeTab = tab.value"
      >
        {{ tab.label }}
      </button>
    </div>

    <!-- 用户账户 -->
    <div v-if="activeTab === 'users'" class="space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <p class="text-sm text-muted-foreground">共 {{ users.length }} 个账户</p>
        <div class="flex items-center gap-2">
          <Button size="sm" variant="outline" :disabled="usersLoading" @click="loadUsers">
            {{ usersLoading ? '刷新中...' : '刷新' }}
          </Button>
          <Button size="sm" variant="primary" :disabled="userBusy === 'create'" @click="openCreateUser">
            新建用户
          </Button>
        </div>
      </div>

      <div
        v-if="newUserApiKey"
        class="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900"
      >
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <p class="font-medium">已创建用户，其调用密钥只展示一次，请复制保存。</p>
            <p class="mt-2 break-all font-mono text-xs">{{ newUserApiKey }}</p>
          </div>
          <Button size="xs" variant="outline" root-class="shrink-0 border-emerald-200 bg-white text-emerald-700" @click="copyText(newUserApiKey)">
            复制
          </Button>
        </div>
      </div>

      <StateBlock v-if="usersLoading && !users.length" compact dashed title="正在加载" description="读取用户账户列表。" />
      <StateBlock v-else-if="!users.length" compact dashed title="暂无用户账户" description="点击新建用户或让用户自助注册。" />
      <div v-else class="space-y-2">
        <div
          v-for="item in users"
          :key="item.id"
          class="flex flex-col gap-3 rounded-xl border border-border bg-card px-4 py-3 md:flex-row md:items-center md:justify-between"
        >
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <p class="truncate text-sm font-medium text-foreground">{{ item.username }}</p>
              <span class="rounded-md bg-secondary px-2 py-0.5 text-xs text-muted-foreground">
                {{ item.role === 'admin' ? '管理员' : '普通用户' }}
              </span>
              <span
                class="rounded-md px-2 py-0.5 text-xs"
                :class="item.enabled ? 'bg-emerald-50 text-emerald-700' : 'bg-secondary text-muted-foreground'"
              >
                {{ item.enabled ? '已启用' : '已禁用' }}
              </span>
            </div>
            <p class="mt-1 text-xs text-muted-foreground">
              对话 {{ item.usage?.calls ?? 0 }}/{{ formatQuotaLimit(item.effective_quota?.call_limit ?? item.quota.call_limit) }}
              · 生图 {{ item.usage?.images ?? 0 }}/{{ formatQuotaLimit(item.effective_quota?.image_limit ?? item.quota.image_limit) }}
              · 创建 {{ formatDate(item.created_at) }}
            </p>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <Button size="xs" variant="outline" :disabled="userBusy === item.id" @click="openEditQuota(item)">编辑配额</Button>
            <Button size="xs" variant="outline" :disabled="userBusy === item.id" @click="toggleUser(item)">
              {{ item.enabled ? '禁用' : '启用' }}
            </Button>
            <Button size="xs" variant="outline" :disabled="userBusy === item.id" @click="openResetPassword(item)">重置密码</Button>
            <Button size="xs" variant="outline" root-class="text-rose-600" :disabled="userBusy === item.id || item.role === 'admin'" @click="removeUser(item)">删除</Button>
          </div>
        </div>
      </div>
    </div>

    <!-- 邀请码 -->
    <div v-else class="space-y-4">
      <div class="flex flex-wrap items-end justify-between gap-3">
        <div class="flex flex-wrap items-end gap-3">
          <FormField label="生成数量">
            <Input v-model.number="genForm.count" type="number" size="md" root-class="w-28" />
          </FormField>
          <FormField label="每个可用次数（0=不限）">
            <Input v-model.number="genForm.max_uses" type="number" size="md" root-class="w-40" />
          </FormField>
          <Button size="sm" variant="primary" :disabled="inviteBusy" @click="generateInvites">批量生成</Button>
        </div>
        <Button size="sm" variant="outline" :disabled="invitesLoading" @click="loadInvites">
          {{ invitesLoading ? '刷新中...' : '刷新' }}
        </Button>
      </div>

      <StateBlock v-if="invitesLoading && !invites.length" compact dashed title="正在加载" description="读取邀请码列表。" />
      <StateBlock v-else-if="!invites.length" compact dashed title="暂无邀请码" description="点击批量生成创建一批邀请码。" />
      <div v-else class="overflow-hidden rounded-2xl border border-border">
        <table class="w-full text-sm">
          <thead>
            <tr class="bg-muted/40 text-xs text-muted-foreground">
              <th class="px-3 py-2 text-left font-medium">邀请码</th>
              <th class="px-3 py-2 text-left font-medium">使用情况</th>
              <th class="px-3 py-2 text-left font-medium">状态</th>
              <th class="px-3 py-2 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in invites" :key="item.code" class="border-t border-border">
              <td class="px-3 py-2">
                <span class="font-mono">{{ item.code }}</span>
                <button class="ml-2 text-xs text-muted-foreground hover:text-foreground" @click="copyText(item.code)">复制</button>
              </td>
              <td class="px-3 py-2 text-muted-foreground">
                {{ item.used_count }} / {{ item.unlimited ? '∞' : item.max_uses }}
              </td>
              <td class="px-3 py-2">
                <span v-if="item.exhausted" class="text-amber-500">已用尽</span>
                <span v-else-if="item.enabled" class="text-emerald-500">可用</span>
                <span v-else class="text-muted-foreground">已停用</span>
              </td>
              <td class="px-3 py-2 text-right">
                <button class="text-xs text-muted-foreground hover:text-foreground" @click="toggleInvite(item)">
                  {{ item.enabled ? '停用' : '启用' }}
                </button>
                <button class="ml-3 text-xs text-rose-600 hover:underline" @click="deleteInvite(item)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- 新建用户 -->
    <ModalShell :open="userModal === 'create'" max-width="34rem" :z-index="130" close-on-backdrop @close="closeUserModal">
      <ModalHeader
        title="新建用户"
        subtitle="创建后会生成一把只展示一次的调用密钥。"
        :close-disabled="userBusy === 'create'"
        :bordered="false"
        @close="closeUserModal"
      />
      <ModalBody class="space-y-3">
        <FormField label="用户名">
          <Input v-model.trim="createForm.username" block placeholder="3-32 位字母、数字、点、下划线或连字符" />
        </FormField>
        <FormField label="初始密码">
          <Input v-model="createForm.password" type="password" block placeholder="至少 6 位" />
        </FormField>
      </ModalBody>
      <ModalFooter :bordered="false">
        <Button size="sm" variant="outline" :disabled="userBusy === 'create'" @click="closeUserModal">取消</Button>
        <Button size="sm" variant="primary" :disabled="userBusy === 'create'" @click="submitCreateUser">
          {{ userBusy === 'create' ? '创建中...' : '创建' }}
        </Button>
      </ModalFooter>
    </ModalShell>

    <!-- 编辑配额 -->
    <ModalShell :open="userModal === 'quota'" max-width="34rem" :z-index="130" close-on-backdrop @close="closeUserModal">
      <ModalHeader
        :title="`编辑配额 · ${editingUser?.username || ''}`"
        subtitle="-1 表示无限，0 表示使用系统默认额度。"
        :bordered="false"
        @close="closeUserModal"
      />
      <ModalBody class="space-y-3">
        <FormField label="对话 / 接口次数上限">
          <Input v-model.number="quotaForm.call_limit" type="number" block />
        </FormField>
        <FormField label="生图次数上限">
          <Input v-model.number="quotaForm.image_limit" type="number" block />
        </FormField>
      </ModalBody>
      <ModalFooter :bordered="false">
        <Button size="sm" variant="outline" :disabled="Boolean(editingUser && userBusy === editingUser.id)" @click="closeUserModal">取消</Button>
        <Button size="sm" variant="primary" :disabled="Boolean(editingUser && userBusy === editingUser.id)" @click="submitEditQuota">
          {{ editingUser && userBusy === editingUser.id ? '保存中...' : '保存' }}
        </Button>
      </ModalFooter>
    </ModalShell>

    <!-- 重置密码 -->
    <ModalShell :open="userModal === 'password'" max-width="34rem" :z-index="130" close-on-backdrop @close="closeUserModal">
      <ModalHeader
        :title="`重置密码 · ${editingUser?.username || ''}`"
        subtitle="重置后该用户已登录的会话将失效，需要用新密码重新登录。"
        :bordered="false"
        @close="closeUserModal"
      />
      <ModalBody class="space-y-3">
        <FormField label="新密码">
          <Input v-model="passwordForm.password" type="password" block placeholder="至少 6 位" />
        </FormField>
      </ModalBody>
      <ModalFooter :bordered="false">
        <Button size="sm" variant="outline" :disabled="Boolean(editingUser && userBusy === editingUser.id)" @click="closeUserModal">取消</Button>
        <Button size="sm" variant="primary" :disabled="Boolean(editingUser && userBusy === editingUser.id)" @click="submitResetPassword">
          {{ editingUser && userBusy === editingUser.id ? '重置中...' : '确认重置' }}
        </Button>
      </ModalFooter>
    </ModalShell>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Button, FormField, Input } from 'nanocat-ui'
import StateBlock from '@/components/ai/StateBlock.vue'
import ModalShell from '@/components/ai/ModalShell.vue'
import ModalHeader from '@/components/ai/ModalHeader.vue'
import ModalBody from '@/components/ai/ModalBody.vue'
import ModalFooter from '@/components/ai/ModalFooter.vue'
import { adminUsersApi } from '@/api/adminUsers'
import { invitesApi, type InviteCode } from '@/api/invites'
import { useToast } from '@/composables/useToast'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import type { AdminUser } from '@/types/api'

const toast = useToast()
const confirmDialog = useConfirmDialog()

const tabs = [
  { value: 'users', label: '用户账户' },
  { value: 'invites', label: '邀请码' },
]
const activeTab = ref<'users' | 'invites'>('users')

const users = ref<AdminUser[]>([])
const usersLoading = ref(false)
const userBusy = ref('')
const newUserApiKey = ref('')

const userModal = ref<'create' | 'quota' | 'password' | ''>('')
const editingUser = ref<AdminUser | null>(null)
const createForm = ref({ username: '', password: '' })
const quotaForm = ref({ call_limit: 0, image_limit: 0 })
const passwordForm = ref({ password: '' })

const invites = ref<InviteCode[]>([])
const invitesLoading = ref(false)
const inviteBusy = ref(false)
const genForm = ref({ count: 5, max_uses: 1 })

function formatQuotaLimit(limit: number) {
  if (limit === -1) return '无限'
  return String(limit ?? 0)
}

function formatDate(value?: string) {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString()
}

async function copyText(value: string) {
  try {
    await navigator.clipboard.writeText(value)
    toast.success('已复制')
  } catch {
    toast.error('复制失败，请手动复制')
  }
}

async function loadUsers() {
  usersLoading.value = true
  try {
    users.value = (await adminUsersApi.list()).items || []
  } catch (error: any) {
    toast.error(error?.message || '加载用户失败')
  } finally {
    usersLoading.value = false
  }
}

function openCreateUser() {
  createForm.value = { username: '', password: '' }
  userModal.value = 'create'
}

async function submitCreateUser() {
  const username = createForm.value.username.trim()
  if (!username || !createForm.value.password) {
    toast.error('请填写用户名和初始密码')
    return
  }
  userBusy.value = 'create'
  newUserApiKey.value = ''
  try {
    const res = await adminUsersApi.create({ username, password: createForm.value.password })
    users.value = res.items || []
    if (res.api_key) newUserApiKey.value = res.api_key
    userModal.value = ''
    toast.success('用户已创建')
  } catch (error: any) {
    toast.error(error?.message || '创建用户失败')
  } finally {
    userBusy.value = ''
  }
}

function openEditQuota(item: AdminUser) {
  editingUser.value = item
  quotaForm.value = {
    call_limit: item.effective_quota?.call_limit ?? item.quota.call_limit ?? 0,
    image_limit: item.effective_quota?.image_limit ?? item.quota.image_limit ?? 0,
  }
  userModal.value = 'quota'
}

async function submitEditQuota() {
  const item = editingUser.value
  if (!item) return
  userBusy.value = item.id
  try {
    users.value = (await adminUsersApi.update(item.id, {
      quota: {
        call_limit: Number(quotaForm.value.call_limit),
        image_limit: Number(quotaForm.value.image_limit),
        period: item.quota.period || '',
      },
    })).items || []
    userModal.value = ''
    toast.success('配额已更新')
  } catch (error: any) {
    toast.error(error?.message || '更新配额失败')
  } finally {
    userBusy.value = ''
  }
}

async function toggleUser(item: AdminUser) {
  userBusy.value = item.id
  try {
    users.value = (await adminUsersApi.update(item.id, { enabled: !item.enabled })).items || []
  } catch (error: any) {
    toast.error(error?.message || '更新失败')
  } finally {
    userBusy.value = ''
  }
}

function openResetPassword(item: AdminUser) {
  editingUser.value = item
  passwordForm.value = { password: '' }
  userModal.value = 'password'
}

async function submitResetPassword() {
  const item = editingUser.value
  if (!item) return
  if (!passwordForm.value.password) {
    toast.error('请输入新密码')
    return
  }
  userBusy.value = item.id
  try {
    await adminUsersApi.resetPassword(item.id, passwordForm.value.password)
    userModal.value = ''
    toast.success('密码已重置')
  } catch (error: any) {
    toast.error(error?.message || '重置密码失败')
  } finally {
    userBusy.value = ''
  }
}

function closeUserModal() {
  if (userBusy.value) return
  userModal.value = ''
  editingUser.value = null
}

async function removeUser(item: AdminUser) {
  const ok = await confirmDialog.ask({
    title: '删除用户',
    message: `确定删除用户「${item.username}」吗？其名下的调用密钥也会一并删除。`,
    confirmText: '删除',
    cancelText: '取消',
  })
  if (!ok) return
  userBusy.value = item.id
  try {
    users.value = (await adminUsersApi.delete(item.id)).items || []
    toast.success('用户已删除')
  } catch (error: any) {
    toast.error(error?.message || '删除用户失败')
  } finally {
    userBusy.value = ''
  }
}

async function loadInvites() {
  invitesLoading.value = true
  try {
    invites.value = (await invitesApi.list()).items || []
  } catch (error: any) {
    toast.error(error?.message || '加载邀请码失败')
  } finally {
    invitesLoading.value = false
  }
}

async function generateInvites() {
  inviteBusy.value = true
  try {
    const res = await invitesApi.generate({
      count: Number(genForm.value.count) || 1,
      max_uses: Number(genForm.value.max_uses) || 0,
    })
    invites.value = res.items || []
    toast.success(`已生成 ${res.created.length} 个邀请码`)
  } catch (error: any) {
    toast.error(error?.message || '生成邀请码失败')
  } finally {
    inviteBusy.value = false
  }
}

async function toggleInvite(item: InviteCode) {
  try {
    invites.value = (await invitesApi.toggle(item.code, !item.enabled)).items || []
  } catch (error: any) {
    toast.error(error?.message || '操作失败')
  }
}

async function deleteInvite(item: InviteCode) {
  const ok = await confirmDialog.ask({
    title: '删除邀请码',
    message: `确定删除邀请码 ${item.code} 吗？`,
    confirmText: '删除',
    cancelText: '取消',
  })
  if (!ok) return
  try {
    invites.value = (await invitesApi.delete(item.code)).items || []
    toast.success('邀请码已删除')
  } catch (error: any) {
    toast.error(error?.message || '删除失败')
  }
}

onMounted(() => {
  void loadUsers()
  void loadInvites()
})
</script>
