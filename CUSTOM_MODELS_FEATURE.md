# 自定义模型功能

## 功能概述

新增了**手动添加自定义模型**的功能，管理员可以在设置页面添加新发布的模型（如 gpt-5.5、dall-e-4 等），系统会自动识别并支持这些模型。

## 主要特性

1. **支持两种模型类型**
   - **对话模型**：用于文本生成和对话（如 gpt-5.5）
   - **生图模型**：用于图像生成（如 dall-e-4）

2. **弹窗式添加界面**
   - 输入模型名称
   - 选择模型类型（对话/生图）
   - 一键添加

3. **模型管理**
   - 查看已添加的自定义模型（分对话和生图两类显示）
   - 支持删除不需要的自定义模型
   - 自定义模型会自动出现在模型列表中

## 使用方法

### 添加自定义模型

1. 进入 **设置 → 模型管理** 页面
2. 点击右上角的 **「添加模型」** 按钮
3. 在弹窗中：
   - 输入模型名称（如 `gpt-5.5`）
   - 选择模型类型（对话/生图）
   - 点击 **「确认添加」**

### 删除自定义模型

1. 在 **自定义模型** 区域找到要删除的模型
2. 点击模型标签右侧的 **×** 按钮
3. 模型将从列表中移除

### 保存设置

添加或删除自定义模型后，记得点击页面底部的 **「保存模型设置」** 按钮保存更改。

## 技术实现

### 后端修改

1. **services/config.py**
   - 添加 `custom_chat_models` 和 `custom_image_models` 字段
   - 更新 `DEFAULT_MODEL_CATALOG`
   - 更新 `_normalize_model_catalog_settings()` 函数

2. **services/model_catalog_service.py**
   - 在 `get_model_catalog()` 中合并自定义模型
   - 对话模型合并到 `chat_models`
   - 生图模型合并到 `image_models`

### 前端修改

1. **web-vue/src/views/Settings.vue**
   - 添加 `custom_chat_models` 和 `custom_image_models` 字段
   - 新增弹窗组件 `customModelModal`
   - 实现 `openCustomModelModal()`, `submitCustomModel()`, `removeCustomModel()` 函数
   - UI 上分别显示对话和生图自定义模型

2. **web-vue/src/api/settings.ts**
   - 更新 `modelCatalogApi` 的类型定义
   - 添加 `custom_chat_models` 和 `custom_image_models` 字段

## 数据结构

```json
{
  "model_catalog": {
    "enabled_models": [],
    "disabled_models": [],
    "default_user_models": [],
    "custom_chat_models": ["gpt-5.5"],
    "custom_image_models": ["dall-e-4"]
  }
}
```

## 注意事项

1. 自定义模型仅添加到模型列表中，实际调用能力取决于后端账号是否支持
2. 模型名称需要与 API 返回的模型名称完全一致
3. 添加后的模型会立即出现在启用/禁用/默认模型的选择列表中
4. 删除自定义模型不会影响已存在的配置，只是从可选列表中移除

## 示例场景

### 场景 1: 添加新发布的 GPT 模型

OpenAI 发布了 `gpt-5.5` 模型：

1. 进入设置 → 模型管理
2. 点击「添加模型」
3. 输入 `gpt-5.5`，选择「对话模型」
4. 点击确认添加
5. 保存模型设置
6. 用户现在可以在对话中选择 `gpt-5.5` 模型

### 场景 2: 添加新的图像生成模型

OpenAI 发布了 `dall-e-4` 模型：

1. 进入设置 → 模型管理
2. 点击「添加模型」
3. 输入 `dall-e-4`，选择「生图模型」
4. 点击确认添加
5. 保存模型设置
6. 用户现在可以使用 `dall-e-4` 生成图片

## 相关错误修复

你遇到的错误 `third_party[grok] chat_completion_stream failed: status=400` 可能是因为：

1. 模型不在系统支持的模型列表中
2. 账号不支持该模型
3. 模型名称拼写错误

使用此功能可以快速添加新模型到系统支持列表中，解决模型不存在的问题。
