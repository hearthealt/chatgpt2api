# TODO

## 🔴 待修复

### 图片管理删除后对话页面未同步

**优先级**：中  
**状态**：待修复  
**影响范围**：图片管理(Gallery) ↔ 对话画图页面(Studio)

#### 问题描述

在**图片管理页面**删除图片后，**对话画图页面**(Studio)的图片任务列表没有同步更新，已删除的图片仍然显示。应该展示"此图片已删除"状态。

#### 根本原因

- Gallery.vue 删除成功后只刷新了自己的列表
- Studio.vue 维护独立的 `imageTasks` 状态，不知道 Gallery 删了图片
- 后端已在 `imageTasksApi.list()` 返回 `missing_ids`(已删除的任务 ID)
- `markMissingImageTasks()` 会标记 error，但**不会从 `imageTasks` 数组移除**

#### 修复步骤

**文件**：`web-vue/src/views/Studio.vue`  
**位置**：`markMissingImageTasks` 函数(约第 1245 行)

修改逻辑：

```typescript
function markMissingImageTasks(taskIds: string[]) {
  const missing = new Set(taskIds.filter(Boolean))
  if (!missing.size) return
  
  // 1. 标记会话消息为 error
  conversations.value.forEach((conversation) => {
    conversation.messages.forEach((message) => {
      if (!message.taskId || !missing.has(message.taskId)) return
      if (message.status === 'done' || message.status === 'error') return
      message.status = 'error'
      message.error = '此图片已删除'
      touchConversation(conversation)
      markConversationNotice(conversation.id, 'error')
    })
  })
  
  // 2. 从 imageTasks 移除已删除的任务 ← 新增
  imageTasks.value = imageTasks.value.filter(task => !missing.has(task.id))
}
```

#### 测试验证

1. 在 Studio 创建 1-2 个画图任务
2. 切换到 Gallery，删除其中一张图片
3. 回到 Studio，等待轮询刷新(或手动刷新)
4. **预期**：
   - 对话消息显示"此图片已删除"错误状态
   - 图片任务列表中已删除的任务消失

---

## 🟡 未来优化

(待补充)
