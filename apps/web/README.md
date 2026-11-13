# Web 开始页与研究工作台

`app/page.tsx` 是开始页路由，只装配 `launch/LaunchPage.tsx` 并导出首页 metadata。开始页不调用 API、不装载研究工作台状态；页面只提供“开始使用”和 GitHub 两个入口。Canvas LED 动画封装在独立组件中，不和路由或工作台状态耦合。点击“开始使用”进入 `/workspace`；GitHub 地址 `https://github.com/iJA774/Tufa-Scholar` 在新标签页打开。

`app/workspace/page.tsx` 是原研究工作台的新入口，装配 `workspace/WorkspacePage.tsx` 中的项目与研究状态。工作台 API 调用仍通过 `/api` 代理，后端 API 路径、设置和业务行为不因首页迁移而改变。

| 模块 | 状态与职责 |
|---|---|
| `app/page.tsx`、`launch/LaunchPage.tsx` | 独立开始页路由与内容；开始页不请求本地 API。 |
| `launch/LaunchPage.module.css` | 开始页 LED 动画与页面样式。 |
| `app/workspace/page.tsx`、`workspace/WorkspacePage.tsx` | `/workspace` 工作台路由及项目选择、顶层提示和工作区状态装配。 |
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

任务在 `queued`、`pending`、`running` 时每次请求结束后间隔 1200 ms 再轮询，同一时刻只有一个请求。一次或两次请求失败后继续轮询，任意一次成功都清零失败计数。收到终态后停止，连续三次失败则停止并显示错误。切换任务、会话、项目或卸载时清除定时器并中止请求。完成后按任务 ID、状态和当前会话刷新消息。

从仓库根目录验证：

```powershell
pnpm check:web
pnpm build:web
pnpm --dir apps/web exec tsc app/hooks/runPolling.ts --target es2020 --module commonjs --outDir ../../.cache/polling-test --skipLibCheck
node apps/web/app/hooks/runPolling.test.cjs (Resolve-Path .cache/polling-test/runPolling.js)
```

交互验收使用 `python -m scripts.fixture_api --data-dir <独立空目录> --port 18180` 的真实 API/SQLite 与模拟搜索、模型和任务。前端在构建前设置 `TUFA_API_URL=http://127.0.0.1:18180`。该 fixture 无真实云端调用且不启动 worker，任务通过 `POST /__fixture/advance` 两次显式推进运行、完成；包含 `fail` 的问题首次失败，重试后可完成。实测范围、结果与限制见 [根 README](../../README.md#验证)。
