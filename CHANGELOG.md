# Changelog

## 1.1.0 - 2026-07-06

### 新增功能

+ [新增] GPT 账号自动注册功能：当图片生成服务检测到无可用账号时自动触发注册流程
  - 支持 5 种智能触发条件（账号池为空、额度耗尽、状态异常、限流中、并发满）
  - 可配置注册数量、冷却期、连续失败阈值
  - 实时状态监控和统计分析
  - 手动触发测试和失败计数重置
+ [新增] 3 个邮箱服务提供商：LuckMail（付费）、Hotmail007（付费）、Microsoft Account Manager（自建）
+ [新增] 前端自动注册配置 UI，支持实时状态显示和操作

### 重构优化

+ [重构] 邮箱提供商架构：移除 8 个旧提供商（CloudMailGen、DDG Mail、DuckMail、GPTMail、MoeEmail、Inbucket、YYDS Mail、Outlook Token），保留 2 个核心提供商（Cloudflare Temp Email、TempMail.lol）
+ [重构] 正确实现 LuckMail 和 Hotmail007 的真实 API 调用（购买邮箱 + 读取邮件）
+ [优化] 前端注册页面代码减少 38.3%（从 53.69 kB 减至 35.27 kB），构建产物 gzip 后减少 30.7%
+ [优化] 简化邮箱来源配置界面，统一使用通用配置字段

### 安全特性

+ [安全] 付费邮箱服务（LuckMail、Hotmail007）标记为付费，**不参与自动注册**，避免意外扣费
+ [安全] 自动注册时自动排除付费提供商，只使用免费邮箱服务
+ [安全] 前端显示付费服务警告：”⚠️ 付费服务：每次注册会购买邮箱并消耗余额，不参与自动注册”

### API 变更

+ [新增] `GET /api/register/auto-register/status` - 获取自动注册状态
+ [新增] `POST /api/register/auto-register/config` - 更新自动注册配置
+ [新增] `POST /api/register/auto-register/trigger` - 手动触发自动注册
+ [新增] `POST /api/register/auto-register/reset-failures` - 重置失败计数

### 文档

+ [新增] `docs/paid-mail-providers.md` - 付费邮箱服务详细说明
+ [新增] `docs/auto-register-implementation-summary.md` - 完整实施总结
+ [新增] `docs/auto-register-quickstart.md` - 快速开始指南

### 技术细节

+ [技术] LuckMail 集成真实 API（https://mails.luckyous.com/api/v1/openapi）
+ [技术] Hotmail007 集成真实 API（https://gapi.hotmail007.com）
+ [技术] 提供商类支持 `is_paid` 属性标记付费服务
+ [技术] 自动注册逻辑检测并临时禁用付费提供商

## 1.0.1 - 2026-07-03

+ [修复] 图片管理页面删除图片后，对话画图页面（Studio）任务列表未同步：现在会自动移除已删除的任务并提示”此图片已删除”。

## 1.0.0 - 2026-07-03

+ [新增] 首个版本发布。
