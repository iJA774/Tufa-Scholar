# 前端开发约定

- 前端面向用户，保持美观、简洁，不加入开发者说明或不可用的功能入口。
- `app/ModelControls.tsx` 管理本次提问的模型与思考强度：供应商声明优先，未声明提供低、中、高，默认选项不发送强度参数；切换模型清除旧强度，取消过时目录请求。选择保存在浏览器偏好中，并随每个新问题提交；它与服务地址、密钥等全局设置分开，不能修改或追溯影响已提交任务。
- 使用 Next.js App Router 与 TypeScript；依赖由根目录 pnpm workspace 和 `pnpm-lock.yaml` 管理。
- 在项目根目录执行 `pnpm dev:web`，默认只监听 `127.0.0.1:3000`；服务端通过 `/api` 代理本地 API，`TUFA_API_URL` 可覆盖默认的 `http://127.0.0.1:8000`。
- `app/page.tsx` 只装配独立开始页并导出首页 metadata；开始页不请求 API、不承载研究状态。内部导航使用 Next.js `Link`，外部站点链接在新标签页打开并设置安全的 `rel` 属性。开始页动画应封装在独立 Canvas 组件中，样式与工作台隔离；Canvas 绘制、尺寸监听及动画回调须在卸载时清理，不与工作台状态耦合。
- `/workspace` 由 `workspace/WorkspacePage.tsx` 装配。它读取项目列表与全局平铺会话列表，默认使用首个项目；仅在没有项目且用户开始研究或打开本地文件时懒创建默认项目，不预建空会话。打开历史会话必须先切换到该会话的 `project_id`，本地文件和研究请求始终限定在当前项目。新研究保留当前项目范围。历史加载、项目加载及懒创建失败要分别显示并可重试，不能把请求失败当成空数据。
- `workspace/ResearchChat.tsx` 负责当前对话的真实消息、问题草稿、模式、引用、报告导出、任务阶段与子任务、失败重试。新问题只发送 `research_mode: "deep" | "quick"` 和本次模型选项；新建页面默认深度模式。模式决策和研究分支由 API/worker 实施，前端展示任务返回的真实模式与阶段，不模拟回答或任务状态。新会话只在首次提问时创建。
- `app/hooks/useRunPolling.ts` 管理任务轮询。切换任务、会话或项目及卸载时取消旧请求，避免旧结果污染新上下文。收到终态后停止；请求失败有限重试，成功后重置连续失败计数。发送、重试和本地文件操作同样须防止过期响应、卸载后的顶层提示和回调更新。
- `app/settings/SettingsPanel.tsx` 同时用于工作台模态框和直接访问的 `/settings` 页面。服务地址、密钥、搜索引擎、PDF 导入与文件列表使用现有本地 API；模型选项随每个问题提交。默认研究模式、Enter 发送方式、外观和密度保存在浏览器偏好中；研究偏好占位控件须明确标记为暂不生效，不能提交到后端。`workspace/PdfImport.tsx`、`PaperLibrary.tsx` 与 `PaperRow.tsx` 仅展示和操作当前项目文件。
- `app/launch/LaunchPage.module.css`、`workspace/WorkspacePage.module.css`、`workspace/ResearchChat.module.css` 与设置面板 CSS Module 隔离各自页面样式；`app/globals.css` 只放确有跨路由用途的基础规则，不能让工作台深色主题污染开始页。
- `app/layout.tsx` 与 `app/globals.css` 负责共用布局和样式；`next.config.ts` 管理 API 代理。组件职责和验证入口见 [前端说明](README.md)，修改设置页时继续读取 [设置页规则](app/settings/AGENTS.md)。
- 页面通过本地 API 操作业务数据，不直接连接 SQLite 或携带服务密钥调用云端模型。生产构建的代理地址在构建时读取，验收前先配置 `TUFA_API_URL`。
- 展示真实数据、加载、空态、失败和任务状态，不用演示数据掩盖接口失败；切换项目或查询条件时避免过时响应覆盖新状态。
- 前端代码或配置修改后运行 `pnpm check:web` 与 `pnpm build:web`，通过实际 HTTP 入口检查首页和受影响页面；交互改动另验证操作结果。纯文档修改遵循根目录的文档检查规则。

<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
