<template>
  <div class="space-y-6">
    <PagePanel class="space-y-5">
      <PanelHeader title="第三方提供商" align="start">
        <template #copy>
          <p class="mt-1 text-xs text-muted-foreground">
            接入 OpenAI 兼容的第三方 API（如 sub2api）。按模型名路由：命中已配置模型走第三方，未命中走原有 ChatGPT 上游。生图模型走 chat completions 并解析图片 URL。
          </p>
        </template>
        <template #actions>
          <Button size="sm" variant="outline" :disabled="loading" @click="loadData">
            {{ loading ? '刷新中...' : '刷新' }}
          </Button>
          <Button size="sm" variant="primary" :disabled="loading" @click="openCreate">
            新增提供商
          </Button>
        </template>
      </PanelHeader>

      <PageLoadingState v-if="loading && !providers.length" />

      <EmptyState
        v-else-if="!providers.length"
        title="暂无第三方提供商"
        description="点击右上角“新增提供商”接入第一个 OpenAI 兼容 API。"
      />

      <div v-else class="grid gap-4 md:grid-cols-2">
        <FormSection
          v-for="provider in providers"
          :key="provider.name"
          density="roomy"
          class="space-y-3"
        >
          <div class="flex items-start justify-between gap-3">
            <div class="min-w-0">
              <div class="flex items-center gap-2">
                <p class="truncate text-sm font-semibold text-foreground">{{ provider.name }}</p>
                <StateBadge :tone="provider.enabled ? 'success' : 'muted'">
                  {{ provider.enabled ? '已启用' : '已停用' }}
                </StateBadge>
              </div>
              <p class="mt-1 truncate font-mono text-xs text-muted-foreground" :title="provider.base_url">
                {{ provider.base_url }}
              </p>
            </div>
            <div class="flex shrink-0 gap-2">
              <Button size="xs" variant="outline" @click="openEdit(provider)">编辑</Button>
              <Button size="xs" variant="outline" root-class="text-rose-600" :disabled="deletingName === provider.name" @click="removeProvider(provider)">
                {{ deletingName === provider.name ? '删除中...' : '删除' }}
              </Button>
            </div>
          </div>

          <div class="grid grid-cols-2 gap-2 text-xs">
            <div class="rounded-lg border border-dashed border-border bg-muted/20 px-3 py-2">
              <p class="text-muted-foreground">对话模型</p>
              <p class="mt-1 font-medium text-foreground">{{ provider.models.length }} 个</p>
            </div>
            <div class="rounded-lg border border-dashed border-border bg-muted/20 px-3 py-2">
              <p class="text-muted-foreground">生图模型</p>
              <p class="mt-1 font-medium text-foreground">{{ provider.image_models.length }} 个</p>
            </div>
          </div>

          <div v-if="provider.models.length || provider.image_models.length" class="flex flex-wrap gap-1.5">
            <span
              v-for="model in provider.models"
              :key="`chat-${model}`"
              class="rounded-md bg-secondary/60 px-2 py-0.5 text-[11px] text-foreground"
            >{{ model }}</span>
            <span
              v-for="model in provider.image_models"
              :key="`img-${model}`"
              class="rounded-md bg-[hsl(var(--primary)_/_0.12)] px-2 py-0.5 text-[11px] text-foreground"
            >🖼 {{ model }}</span>
          </div>

          <p class="text-xs text-muted-foreground">
            超时 {{ provider.timeout_secs }}s
            <template v-if="provider.proxy"> · 专用代理 {{ provider.proxy }}</template>
            <template v-if="provider.api_key"> · 已配置 API Key</template>
          </p>
        </FormSection>
      </div>
    </PagePanel>

    <ModalShell :open="modalOpen" max-width="40rem" @close="closeModal">
      <ModalHeader :title="editing ? '编辑提供商' : '新增提供商'" @close="closeModal" />
      <ModalBody density="roomy">
        <div class="space-y-4">
          <div class="grid gap-3 sm:grid-cols-2">
            <label class="block text-xs">
              <span class="ui-field-label">名称（唯一）</span>
              <Input v-model="form.name" block placeholder="自定义名称，如 provider-1" :disabled="editing" />
            </label>
            <label class="block text-xs">
              <span class="ui-field-label">超时（秒）</span>
              <Input v-model="timeoutInput" block root-class="font-mono" placeholder="120" />
            </label>
          </div>

          <label class="block text-xs">
            <span class="ui-field-label">Base URL（末尾不带 /）</span>
            <Input v-model="form.base_url" block root-class="font-mono" placeholder="https://api.example.com" />
          </label>

          <label class="block text-xs">
            <span class="ui-field-label">API Key（Bearer Token）</span>
            <Input v-model="form.api_key" block root-class="font-mono" type="password" placeholder="sk-..." />
          </label>

          <div class="rounded-lg border border-border bg-muted/10 p-3 space-y-3">
            <div class="flex items-center justify-between gap-2">
              <span class="ui-field-label">从 /v1/models 获取模型列表</span>
              <Button size="xs" variant="outline" :disabled="fetchingModels || !form.base_url" @click="fetchModels">
                {{ fetchingModels ? '获取中...' : '拉取模型' }}
              </Button>
            </div>
            <p v-if="fetchError" class="text-xs text-rose-600">{{ fetchError }}</p>
            <template v-if="fetchedModels.length">
              <p class="text-xs text-muted-foreground">
                共 {{ fetchedModels.length }} 个模型。勾选加入对话模型，🖼 勾选加入生图模型。
              </p>
              <div class="max-h-52 overflow-y-auto rounded-md border border-border bg-background divide-y divide-border">
                <div
                  v-for="model in fetchedModels"
                  :key="model"
                  class="flex items-center justify-between gap-2 px-2.5 py-1.5 text-xs"
                >
                  <span class="truncate font-mono text-foreground" :title="model">{{ model }}</span>
                  <div class="flex shrink-0 items-center gap-3">
                    <label class="flex items-center gap-1 text-muted-foreground">
                      <Checkbox :model-value="selectedChat.has(model)" @update:model-value="toggleChat(model, $event)" />
                      <span>对话</span>
                    </label>
                    <label class="flex items-center gap-1 text-muted-foreground">
                      <Checkbox :model-value="selectedImage.has(model)" @update:model-value="toggleImage(model, $event)" />
                      <span>🖼 生图</span>
                    </label>
                  </div>
                </div>
              </div>
            </template>
          </div>

          <label class="block text-xs">
            <span class="ui-field-label">对话模型（每行一个，或用逗号分隔）</span>
            <textarea
              v-model="modelsInput"
              rows="3"
              class="ui-textarea mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 font-mono text-xs"
              placeholder="每行一个模型名"
            />
          </label>

          <label class="block text-xs">
            <span class="ui-field-label">生图模型（走 chat completions，每行一个）</span>
            <textarea
              v-model="imageModelsInput"
              rows="2"
              class="ui-textarea mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 font-mono text-xs"
              placeholder="每行一个生图模型名"
            />
          </label>

          <label class="block text-xs">
            <span class="ui-field-label">专用代理（可选）</span>
            <Input v-model="form.proxy" block root-class="font-mono" placeholder="http://127.0.0.1:7890（留空使用全局出口）" />
          </label>

          <label class="flex items-center gap-2 text-xs">
            <Checkbox v-model="form.enabled" />
            <span class="text-foreground">启用该提供商</span>
          </label>

          <div class="flex items-center gap-2 border-t border-border pt-3">
            <Button size="xs" variant="outline" :disabled="testing || !form.base_url" @click="testConnection">
              {{ testing ? '测试中...' : '测试连接' }}
            </Button>
            <p v-if="testResult" class="text-xs" :class="testResult.ok ? 'text-emerald-600' : 'text-rose-600'">
              {{ testResultText }}
            </p>
          </div>
        </div>
      </ModalBody>
      <ModalFooter>
        <Button size="sm" variant="outline" :disabled="saving" @click="closeModal">取消</Button>
        <Button size="sm" variant="primary" :disabled="saving" @click="saveProvider">
          {{ saving ? '保存中...' : '保存' }}
        </Button>
      </ModalFooter>
    </ModalShell>
  </div>
</template>

<script setup lang="ts">
import { computed, onActivated, onMounted, reactive, ref } from 'vue'
import { Button, Checkbox, EmptyState, Input } from 'nanocat-ui'
import { providersApi, type Provider, type ProviderTestResult } from '@/api/providers'
import { useModelCatalog } from '@/composables/useModelCatalog'
import { useSettingsStore } from '@/stores/settings'
import FormSection from '@/components/ai/FormSection.vue'
import ModalBody from '@/components/ai/ModalBody.vue'
import ModalFooter from '@/components/ai/ModalFooter.vue'
import ModalHeader from '@/components/ai/ModalHeader.vue'
import ModalShell from '@/components/ai/ModalShell.vue'
import PageLoadingState from '@/components/ai/PageLoadingState.vue'
import PagePanel from '@/components/ai/PagePanel.vue'
import PanelHeader from '@/components/ai/PanelHeader.vue'
import StateBadge from '@/components/ai/StateBadge.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { useToast } from '@/composables/useToast'

const toast = useToast()
const confirm = useConfirmDialog()
const settingsStore = useSettingsStore()
const { loadModelCatalog } = useModelCatalog(() => settingsStore.settings)

const providers = ref<Provider[]>([])
const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const deletingName = ref('')
const modalOpen = ref(false)
const editing = ref(false)
const testResult = ref<ProviderTestResult | null>(null)
const fetchingModels = ref(false)
const fetchError = ref('')
const fetchedModels = ref<string[]>([])
const selectedChat = reactive(new Set<string>())
const selectedImage = reactive(new Set<string>())

const form = reactive<Provider>({
  name: '',
  base_url: '',
  api_key: '',
  models: [],
  image_models: [],
  enabled: true,
  timeout_secs: 120,
  proxy: '',
})
const originalName = ref('')
const timeoutInput = ref('120')
const modelsInput = ref('')
const imageModelsInput = ref('')

const testResultText = computed(() => {
  const result = testResult.value
  if (!result) return ''
  if (result.ok) {
    const count = result.model_count ?? 0
    return `连接成功（HTTP ${result.status}，${count} 个模型，${result.latency_ms}ms）`
  }
  return `连接失败：${result.error || `HTTP ${result.status}`}`
})

function parseModelList(value: string): string[] {
  const seen = new Set<string>()
  const result: string[] = []
  for (const raw of value.split(/[\n,]/)) {
    const model = raw.trim()
    if (model && !seen.has(model)) {
      seen.add(model)
      result.push(model)
    }
  }
  return result
}

function syncSelection() {
  selectedChat.clear()
  selectedImage.clear()
  for (const model of parseModelList(modelsInput.value)) selectedChat.add(model)
  for (const model of parseModelList(imageModelsInput.value)) selectedImage.add(model)
}

function toggleChat(model: string, checked: boolean) {
  const chat = parseModelList(modelsInput.value)
  const image = parseModelList(imageModelsInput.value)
  const chatIdx = chat.indexOf(model)
  if (checked) {
    if (chatIdx < 0) chat.push(model)
    const imgIdx = image.indexOf(model)
    if (imgIdx >= 0) image.splice(imgIdx, 1)
  } else if (chatIdx >= 0) {
    chat.splice(chatIdx, 1)
  }
  modelsInput.value = chat.join('\n')
  imageModelsInput.value = image.join('\n')
  syncSelection()
}

function toggleImage(model: string, checked: boolean) {
  const chat = parseModelList(modelsInput.value)
  const image = parseModelList(imageModelsInput.value)
  const imgIdx = image.indexOf(model)
  if (checked) {
    if (imgIdx < 0) image.push(model)
    const chatIdx = chat.indexOf(model)
    if (chatIdx >= 0) chat.splice(chatIdx, 1)
  } else if (imgIdx >= 0) {
    image.splice(imgIdx, 1)
  }
  modelsInput.value = chat.join('\n')
  imageModelsInput.value = image.join('\n')
  syncSelection()
}

async function fetchModels() {
  const base_url = form.base_url.trim().replace(/\/+$/, '')
  if (!base_url) {
    toast.error('请先填写 Base URL')
    return
  }
  fetchingModels.value = true
  fetchError.value = ''
  try {
    const response = await providersApi.fetchModels({
      base_url,
      api_key: form.api_key.trim(),
      proxy: form.proxy.trim(),
    })
    if (response.ok) {
      fetchedModels.value = response.models
      syncSelection()
      if (!response.models.length) fetchError.value = '上游返回了空的模型列表'
    } else {
      fetchError.value = response.error || `获取失败（HTTP ${response.status}）`
    }
  } catch (error) {
    fetchError.value = (error as Error).message || '获取模型失败'
  } finally {
    fetchingModels.value = false
  }
}

async function loadData() {
  loading.value = true
  try {
    const response = await providersApi.list()
    providers.value = response.providers || []
  } catch (error) {
    toast.error((error as Error).message || '加载提供商失败')
  } finally {
    loading.value = false
  }
}

function resetForm() {
  form.name = ''
  form.base_url = ''
  form.api_key = ''
  form.models = []
  form.image_models = []
  form.enabled = true
  form.timeout_secs = 120
  form.proxy = ''
  originalName.value = ''
  timeoutInput.value = '120'
  modelsInput.value = ''
  imageModelsInput.value = ''
  testResult.value = null
  fetchedModels.value = []
  fetchError.value = ''
  selectedChat.clear()
  selectedImage.clear()
}

function openCreate() {
  resetForm()
  editing.value = false
  modalOpen.value = true
}

function openEdit(provider: Provider) {
  resetForm()
  editing.value = true
  form.name = provider.name
  form.base_url = provider.base_url
  form.api_key = provider.api_key
  form.models = [...provider.models]
  form.image_models = [...provider.image_models]
  form.enabled = provider.enabled
  form.timeout_secs = provider.timeout_secs
  form.proxy = provider.proxy
  originalName.value = provider.name
  timeoutInput.value = String(provider.timeout_secs || 120)
  modelsInput.value = provider.models.join('\n')
  imageModelsInput.value = provider.image_models.join('\n')
  syncSelection()
  modalOpen.value = true
}

function closeModal() {
  modalOpen.value = false
}

function buildPayload() {
  const timeout = Number.parseInt(timeoutInput.value, 10)
  return {
    name: form.name.trim(),
    base_url: form.base_url.trim().replace(/\/+$/, ''),
    api_key: form.api_key.trim(),
    models: parseModelList(modelsInput.value),
    image_models: parseModelList(imageModelsInput.value),
    enabled: form.enabled,
    timeout_secs: Number.isFinite(timeout) && timeout > 0 ? timeout : 120,
    proxy: form.proxy.trim(),
    original_name: originalName.value,
  }
}

async function saveProvider() {
  const payload = buildPayload()
  if (!payload.name) {
    toast.error('请填写提供商名称')
    return
  }
  if (!payload.base_url) {
    toast.error('请填写 Base URL')
    return
  }
  if (!payload.models.length && !payload.image_models.length) {
    toast.error('请至少填写一个对话模型或生图模型')
    return
  }
  saving.value = true
  try {
    const response = await providersApi.save(payload)
    providers.value = response.providers || []
    toast.success('提供商已保存')
    modalOpen.value = false
    // 刷新模型目录,使 Studio 页面立即看到新模型
    void loadModelCatalog(true)
  } catch (error) {
    toast.error((error as Error).message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function removeProvider(provider: Provider) {
  const ok = await confirm.ask({
    title: '删除提供商',
    message: `确定删除提供商「${provider.name}」吗？该操作会移除其所有模型路由。`,
    confirmText: '删除',
  })
  if (!ok) return
  deletingName.value = provider.name
  try {
    const response = await providersApi.delete(provider.name)
    providers.value = response.providers || []
    toast.success('提供商已删除')
    // 刷新模型目录,移除已删除的模型
    void loadModelCatalog(true)
  } catch (error) {
    toast.error((error as Error).message || '删除失败')
  } finally {
    deletingName.value = ''
  }
}

async function testConnection() {
  const base_url = form.base_url.trim().replace(/\/+$/, '')
  if (!base_url) {
    toast.error('请先填写 Base URL')
    return
  }
  testing.value = true
  testResult.value = null
  try {
    const response = await providersApi.test({
      base_url,
      api_key: form.api_key.trim(),
      proxy: form.proxy.trim(),
    })
    testResult.value = response.result
  } catch (error) {
    testResult.value = { ok: false, status: 0, latency_ms: 0, error: (error as Error).message }
  } finally {
    testing.value = false
  }
}

onMounted(loadData)
onActivated(loadData)
</script>
