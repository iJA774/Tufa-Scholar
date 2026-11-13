# 开发进度与上下文

本文件记录当前任务和协作状态。长期约束见 [根规则](../AGENTS.md)，能力边界见 [STATUS.md](STATUS.md)，工作流设计与接口见 [ARCHITECTURE.md](ARCHITECTURE.md)，实际验收结果见 [README](../README.md#验证)。

## 当前任务

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
