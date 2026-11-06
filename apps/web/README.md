# 研究工作台前端

`app/page.tsx` 保存项目列表、当前项目和顶层提示，按项目 key 装配工作区。局部表单、请求和弹窗属于对应功能组件，不提升为首页状态。

| 模块 | 状态与职责 |
|---|---|
| `workspace/ProjectWorkspace.tsx` | 工作区布局、视图切换、项目文献刷新及历史跳转协调；项目切换时重置，屏蔽旧项目回调 |
| `workspace/ProjectSidebar.tsx` | 项目创建、菜单、删除确认和失败提示，组合收藏夹导航 |
| `workspace/Collections.tsx` | 仅收藏域共享列表、当前范围与成员；管理创建、移除、删除、收藏选择及弹窗 |
| `workspace/ResearchChat.tsx` | 会话、消息、提问草稿、模型选择、任务恢复与重试；通过文献库和导入插槽保持双列布局 |
| `workspace/SearchDialog.tsx` | 文献与历史检索的标签、输入、结果和取消；组合现有网页搜索组件 |
| `workspace/PdfImport.tsx` | 文件与元数据表单、上传状态和取消，成功后通知项目文献刷新 |
| `workspace/PaperLibrary.tsx`、`PaperRow.tsx` | 文献列表与来源、PDF 链接展示，收藏动作由收藏域提供 |
| `workspace/api.ts`、`types.ts` | 同一工作台使用的请求辅助与数据契约 |
| `hooks/useRunPolling.ts`、`runPolling.ts` | React 生命周期适配与可独立验证的轮询调度 |

收藏域上下文不承接聊天、模型、搜索或上传状态。切换收藏夹视图时保留聊天组件与草稿，项目切换则重挂项目域；输入变化、搜索关闭和卸载会取消过期请求。

任务在 `queued`、`pending`、`running` 时每次请求结束后间隔 1200 ms 再轮询，不并发重叠；切换任务、会话、项目或卸载时清除定时器并中止请求。终态或请求失败停止调度，错误展示给用户；新的任务可重新开启。完成后按任务 ID、状态和当前会话刷新消息。

从仓库根目录验证：

```powershell
pnpm check:web
pnpm build:web
pnpm --dir apps/web exec tsc app/hooks/runPolling.ts --target es2020 --module commonjs --outDir ../../.cache/polling-test --skipLibCheck
node apps/web/app/hooks/runPolling.test.cjs (Resolve-Path .cache/polling-test/runPolling.js)
```

交互验收使用 `python -m scripts.fixture_api --data-dir <独立空目录> --port 18180` 的真实 API/SQLite 与模拟搜索、模型和任务。前端在构建前设置 `TUFA_API_URL=http://127.0.0.1:18180`。该 fixture 无真实云端调用且不启动 worker，任务通过 `POST /__fixture/advance` 两次显式推进运行、完成；包含 `fail` 的问题首次失败，重试后可完成。实测范围、结果与限制见 [根 README](../../README.md#验证)。
