<template>
  <div class="gallery-page">
    <PagePanel class="gallery-hero">
      <PanelHeader title="我的图库">
        <template #actions>
          <Button size="sm" variant="outline" :disabled="isLoading" @click="resetAndLoad">
            {{ isLoading ? '刷新中...' : '刷新' }}
          </Button>
        </template>
      </PanelHeader>
      <FilterToolbar class="gallery-filter-grid" gap="tight" mobile-mode="stack">
        <Input
          :model-value="searchQuery"
          type="text"
          placeholder="搜索文件名、路径"
          block
          root-class="gallery-filter-search"
          @update:model-value="searchQuery = $event"
        />
        <DateRangeInputs
          v-model:start="startDate"
          v-model:end="endDate"
          class="gallery-date-range"
          input-root-class="gallery-date-input"
        />
      </FilterToolbar>
    </PagePanel>

    <PagePanel flush>
      <div class="gallery-content-toolbar">
        <div class="min-w-0">
          <p class="ui-section-kicker">当前视图</p>
          <p class="mt-1 text-xs text-muted-foreground">{{ paginationSummary }}</p>
        </div>
      </div>

      <PageLoadingState
        v-if="!hasLoadedOnce && files.length === 0"
        class="gallery-state-block"
        title="正在加载图片"
        description="读取你生成的图片记录。"
      />

      <StateBlock
        v-else-if="files.length === 0"
        class="gallery-state-block"
        :title="loadError ? '加载失败' : '暂无图片'"
        :description="loadError || '去对话画图页生成第一张图片吧。'"
      >
        <template #media>
          <Icon icon="lucide:image-off" class="h-12 w-12 text-muted-foreground/40" />
        </template>
      </StateBlock>

      <div v-else class="space-y-4 p-4 lg:p-5">
        <div class="image-grid">
          <GalleryImageCard
            v-for="file in files"
            :key="file.path"
            :file="file"
            :selected="false"
            :previewable="canPreviewFile(file)"
            :copied="copiedFileKey === file.path"
            :image-url="getFileUrl(file.thumbnail_url || file.url)"
            :storage-label="''"
            :size-label="formatSize(file.size)"
            :dimensions="formatDimensions(file)"
            :time-remaining="file.expires_in_seconds !== null ? formatTimeRemaining(file.expires_in_seconds) : ''"
            hide-select
            hide-tags
            hide-delete
            @preview="openPreview"
            @image-error="(event, item) => handleImageError(event, item.path)"
            @copy="copyFileLink"
            @download="downloadFile"
          />
        </div>

        <ListPagination
          v-model:page="currentPage"
          v-model:page-size="pageSize"
          :total-count="totalItems"
          :page-size-options="galleryPageSizeOptions"
          unit="张图片"
          :disabled="isLoading"
        />
      </div>
    </PagePanel>

    <GalleryLightbox
      :file="previewFile"
      :image-url="previewFile ? getFileUrl(previewFile.url) : ''"
      :size-label="previewFile ? formatSize(previewFile.size) : ''"
      :copied="Boolean(previewFile && copiedFileKey === previewFile.path)"
      :show-tag-action="false"
      @close="closePreview"
      @download="downloadFile"
      @copy="copyFileLink"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Icon } from '@iconify/vue'
import { galleryApi, resolveGalleryFileUrl, type GalleryFile } from '@/api/gallery'
import { Button, Input } from 'nanocat-ui'
import DateRangeInputs from '@/components/ai/DateRangeInputs.vue'
import FilterToolbar from '@/components/ai/FilterToolbar.vue'
import GalleryImageCard from '@/components/ai/GalleryImageCard.vue'
import ListPagination from '@/components/ai/ListPagination.vue'
import PageLoadingState from '@/components/ai/PageLoadingState.vue'
import PagePanel from '@/components/ai/PagePanel.vue'
import PanelHeader from '@/components/ai/PanelHeader.vue'
import StateBlock from '@/components/ai/StateBlock.vue'
import { useToast } from '@/composables/useToast'
import { downloadUrlAsFile } from '@/lib/downloads'
import { getNumberPreference, preferenceKeys, setNumberPreference } from '@/lib/preferences'

const GalleryLightbox = defineAsyncComponent(() => import('@/components/ai/GalleryLightbox.vue'))

const toast = useToast()

const files = ref<GalleryFile[]>([])
const totalItems = ref(0)
const isLoading = ref(true)
const hasLoadedOnce = ref(false)
const loadError = ref('')
const previewFile = ref<GalleryFile | null>(null)
const copiedFileKey = ref('')
const searchQuery = ref('')
const startDate = ref('')
const endDate = ref('')
const galleryPageSizeOptions = [24, 48, 96] as const
const pageSize = ref(getNumberPreference(preferenceKeys.galleryPageSize, 24, { allowed: galleryPageSizeOptions }))
const currentPage = ref(1)
const pageCount = ref(1)
const brokenImagePaths = ref<Set<string>>(new Set())
let latestLoadToken = 0
let copyResetTimer: number | null = null
let searchTimer: number | null = null

const paginationSummary = computed(() => `第 ${currentPage.value} / ${pageCount.value} 页，共 ${totalItems.value} 张`)

function getFileUrl(url: string) {
  return resolveGalleryFileUrl(url)
}

async function loadGallery() {
  const loadToken = ++latestLoadToken
  isLoading.value = true
  loadError.value = ''
  try {
    const response = await galleryApi.getMine({
      page: currentPage.value,
      page_size: pageSize.value,
      search: searchQuery.value.trim(),
      start_date: startDate.value,
      end_date: endDate.value,
    })
    if (loadToken !== latestLoadToken) return
    files.value = response.files
    totalItems.value = response.total
    pageCount.value = response.page_count
    if (currentPage.value > response.page_count && response.page_count >= 1) {
      currentPage.value = response.page_count
    }
  } catch (error: any) {
    if (loadToken !== latestLoadToken) return
    loadError.value = error?.message || '加载图库失败'
    files.value = []
    totalItems.value = 0
  } finally {
    if (loadToken === latestLoadToken) {
      isLoading.value = false
      hasLoadedOnce.value = true
    }
  }
}

function resetAndLoad() {
  currentPage.value = 1
  void loadGallery()
}

async function downloadFile(file: GalleryFile) {
  try {
    await downloadUrlAsFile(getFileUrl(file.url), file.filename)
  } catch {
    toast.error('下载失败，请重试')
  }
}

async function copyFileLink(file: GalleryFile | null) {
  if (!file) return
  try {
    await navigator.clipboard.writeText(getFileUrl(file.url))
    copiedFileKey.value = file.path
    if (copyResetTimer !== null) window.clearTimeout(copyResetTimer)
    copyResetTimer = window.setTimeout(() => { copiedFileKey.value = '' }, 1500)
    toast.success('链接已复制')
  } catch {
    toast.error('复制失败，请手动复制')
  }
}

function openPreview(file: GalleryFile) {
  previewFile.value = file
}

function closePreview() {
  previewFile.value = null
}

function formatSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

function formatTimeRemaining(seconds: number): string {
  if (seconds <= 0) return '已过期'
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d > 0) return `${d}天 ${h}小时`
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

function formatDimensions(file: GalleryFile): string {
  return file.width && file.height ? `${file.width}x${file.height}` : ''
}

function canPreviewFile(file: GalleryFile): boolean {
  return file.size > 128 && !brokenImagePaths.value.has(file.path)
}

function handleImageError(event: Event, path: string) {
  const img = event.target as HTMLImageElement
  img.style.opacity = '0'
  brokenImagePaths.value = new Set([...brokenImagePaths.value, path])
}

watch([startDate, endDate, pageSize], resetAndLoad)
watch(pageSize, (value) => setNumberPreference(preferenceKeys.galleryPageSize, value))
watch(searchQuery, () => {
  if (searchTimer !== null) window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => { searchTimer = null; resetAndLoad() }, 250)
})
watch(currentPage, () => { void loadGallery() })

onMounted(loadGallery)
onBeforeUnmount(() => {
  if (copyResetTimer !== null) window.clearTimeout(copyResetTimer)
  if (searchTimer !== null) window.clearTimeout(searchTimer)
})
</script>

<style scoped>
.gallery-page {
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.gallery-hero {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 18px 20px;
}

:deep(.gallery-filter-search) {
  flex: 0 1 22rem;
  min-width: min(100%, 14rem);
}

.gallery-date-range {
  --date-range-flex: 0 1 17rem;
  --date-range-min-width: min(100%, 16rem);
  --date-range-input-min-width: 7.25rem;
}

@media (max-width: 640px) {
  .gallery-hero {
    padding: 14px;
  }

  :deep(.gallery-filter-search),
  .gallery-date-range {
    flex: 1 1 auto;
    min-width: 0;
    width: 100%;
  }
}

.image-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(168px, 1fr));
  gap: 12px;
}

@media (min-width: 1280px) {
  .image-grid {
    grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
  }
}

@media (max-width: 420px) {
  .image-grid {
    grid-template-columns: repeat(auto-fill, minmax(136px, 1fr));
  }
}

.gallery-content-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid hsl(var(--border));
}

.gallery-state-block {
  margin: 24px;
}
</style>
