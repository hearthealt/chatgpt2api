# Changelog

## 1.1.2 - 2026-07-08

+ [修复] 自动注册触发后不会实际启动注册 worker 的问题；现在会重置统计、执行注册任务，并在结束后恢复原注册配置和邮箱来源状态。
+ [修复] Turnstile VM 回调参数错误，避免 Sentinel SO Token 生成时把寄存器编号误传给回调。
+ [修复] Sentinel 无 PoW 场景复用同一个 requirements token，避免 `p` / `t` 不一致导致校验失败。
+ [修复] Cloudflare clearance 刷新后，创建账号资料重试会使用最新浏览器 fingerprint。
+ [修复] 图片管理删除图片后，同步标记对话画图任务并过滤日志管理里的已删除图片预览。
+ [优化] 注册流程支持多套 Chrome 指纹，并将 headers、OAuth token 请求和 Sentinel 请求保持一致。
+ [清理] 注册邮箱来源保持移除 GPTMail 和 Outlook Token，不再恢复旧 provider 分发。
+ [重构] 邮箱 provider 实现合并到 `services/register/mail_provider.py`，当前仅保留 Cloudflare Temp Email、TempMail.lol、LuckMail、Hotmail007 和 Microsoft Account Manager。
+ [调整] `gpt-image-2` 底层图片模型映射调整为 `gpt-5-5-thinking`。

## 1.1.1 - 2026-07-06

+ [优化] 注册账号页面布局，调整任务配置、邮箱来源、自动注册和执行控制区域的排版。
+ [优化] 邮箱来源配置改为更清晰的连接信息、鉴权信息、购买参数和域名池分组。
+ [调整] Hotmail007 配置仅保留 `product_id`，启动前先通过库存接口检查，有库存才购买，且每次固定购买 1 个。

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
