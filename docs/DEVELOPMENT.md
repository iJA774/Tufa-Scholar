# 开发进度与上下文

本文件记录当前任务和协作状态。长期约束见 [根规则](../AGENTS.md)，能力边界见 [STATUS.md](STATUS.md)，工作流设计与接口见 [ARCHITECTURE.md](ARCHITECTURE.md)，实际验收结果见 [README](../README.md#验证)。

## 当前任务

- 主题：恢复 `cd8da93` 的终稿结构要求，修复推理预算与完成上限冲突导致的模型请求失败。
- 工作分支：`codex/live-research-feedback`，延续研究流程的调整。

## 上一阶段：深度研究预算

- 主题：压缩研究检索结果与模型输入，限制工具决策与生成开销，达到上下文预算后直接转入成稿。
- 工作分支：`codex/live-research-feedback`，延续研究流程的调整。
- 协作：Luna 子代理分别负责搜索与工作流、模型协议与生成，主代理负责方案、请求体积对照与整体验收。

## 上一阶段：实时研究反馈

- 主题：为科研工作区增加节点进度、工具任务卡片和模型流式预览。
- 工作分支：`codex/live-research-feedback`，从 `main` 创建。
- 协作：主代理负责 API、worker、事件存储及整体验收，Luna 子代理分别负责模型流、工作流事件与前端。

## 上一阶段：网页读取与终稿收束

- 主题：修复网页读取连续失败耗尽工具预算，以及终稿引用全部失效时阶段性发现丢失的问题。
- 工作分支：`codex/research-history-controls`，延续研究流程修复。
- 协作：Luna 子代理实现工作流与回归测试，主代理验证实际 API/worker 和报告导出。

## 上一阶段：工作区整合研究模型输入优化

- 主题：将研究模型输入裁剪与 token 用量日志合入工作区分支。
- 工作分支：`codex/research-history-controls`，已整合 `codex/research-token-budget`。

## 上一阶段：研究模型输入优化

- 主题：减少深度研究反复发送的候选资料和证据，按阶段裁剪模型输入，记录供应商返回的 token 用量。
- 工作分支：`codex/research-token-budget`，从 `main` 创建。
- 协作：主代理负责对照测量和验收，Luna 子代理分别负责工作流载荷与模型用量日志。

## 上一阶段：研究面板移植

- 主题：根据界面验收反馈，移植本机 DeerFlow 的研究双栏布局、报告/活动卡片和展开动画，替换上一版自定义进度栏。
- 工作分支：`codex/research-history-controls`，延续工作区界面调整。
- 协作：主代理负责原版对照、规划和验收，`gpt-6-luna` / `xhigh` 子代理负责研究界面编程；保留已完成的设置弹窗和星形图标动画。

## 上一阶段：研究历史操作

- 主题：清理开始页与研究工作区的装饰性说明；研究历史改为悬浮抽屉，支持持久化置顶与确认删除。
- 工作分支：`codex/research-history-controls`，从 `main` 创建。
- 协作：主代理负责规划、整合和验收；`/root/ui` 实现前端，`/root/history_api` 实现本机 API、SQLite 和后端回归，两位子代理均为 `gpt-6-luna`、`xhigh`。

## 上一阶段：桌面研究工作区与研究模式

- 主题：依据 `tufa_desktop.html` 调整桌面研究工作区：以跨项目平铺历史导航，支持 API/搜索引擎设置、当前项目 PDF 与研究模式控制；“研究偏好”保留为不影响行为的占位。
- 工作分支：`codex/launch-page`，延续同一 UI 任务分支。
- 协作：主代理负责规划、最终浏览器验收和审查；`/root/workflow` 负责前端，`/root/prompts` 负责 API、worker、数据库、工作流、模板和后端测试，`/root/verification` 负责隔离 API/worker 验收脚本、UI fixture 与文档。沿用 `gpt-6-luna`、`xhigh` 子代理配置。

## 模式与验收范围

- `research_mode=auto` 保持旧 API 客户端行为，缺省值及数据库迁移后的既有任务值为 auto；重试复制原任务模式。
- `deep` 不调用 dispatcher 模型判断，直接进入背景研究；`quick` 调用 dispatcher 生成直接答复，但代码强制结束于直接回答，即使模型要求研究也不检索。
- 实际 API/worker 验收使用隔离临时数据库和本地 HTTP 模拟模型/搜索；UI fixture 使用真实 API/SQLite 但不启动 worker。两类结果分开记录，真实模型与服务商不在模拟验收范围内。

## 阶段

| 阶段 | 状态 | 说明 |
|---|---|---|
| 研究工作流改造与验收 | 已完成 | 前一任务的实现与隔离验收记录保留在根 README。 |
| 开始页与工作台入口 | 已完成 | `/` 独立显示开始页；`/workspace` 为工作台路由，入口浏览器验收记录保留在根 README。 |
| 桌面研究工作区与模式改造 | 已完成 | API/worker 回归与隔离闭环、1280×720 桌面交互、最终类型检查、默认代理生产构建及 5 项轮询回归均通过。 |
| 文档与 UI fixture 同步 | 已完成 | 文档记录全局平铺历史、设置与 PDF、quick/deep/auto 契约；fixture 提供跨项目历史、样例 PDF 和本地假设置。 |

本轮桌面浏览器、后端回归、脚本及最终前端检查结果，分别记录在根 README 的 2026-11-13 工作区验收小节。UI fixture 不启动 worker；worker、模式执行与重试由独立隔离 API/worker 脚本验证。真实模型及搜索供应商未调用。自启验收服务已停止，最终构建恢复默认本机 API 代理；用户原有改动保留。
