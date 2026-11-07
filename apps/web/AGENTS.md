# 前端开发约定

- 前端面向用户，保持美观、简洁，不加入开发者说明或不可用的功能入口。
- `app/ModelControls.tsx` 管理提问模型与思考强度：供应商声明优先，未声明提供低、中、高，默认选项不发送强度参数；切换模型清除旧强度，取消过时目录请求。选择随问题提交，不通过修改全局设置影响已提交任务。
- 使用 Next.js App Router 与 TypeScript；依赖由根目录 pnpm workspace 和 `pnpm-lock.yaml` 管理。
- 在项目根目录执行 `pnpm dev:web`，默认只监听 `127.0.0.1:3000`；服务端通过 `/api` 代理本地 API，`TUFA_API_URL` 可覆盖默认的 `http://127.0.0.1:8000`。
- `app/page.tsx` 只装配工作台并维护项目选择与顶层提示；`app/workspace/` 按项目导航、收藏夹、聊天、文献、PDF 导入与搜索分责，各域自行管理表单、请求和弹窗。项目域仅通过必要的语义回调协调，不将所有状态集中到单个组件、Hook 或全局上下文。
- `app/hooks/useRunPolling.ts` 管理任务轮询。切换任务、会话或项目及卸载时取消旧请求，避免旧结果污染新上下文。收到终态后停止；请求失败有限重试，成功后重置连续失败计数。局部异步操作同样须防止卸载后的顶层提示和回调更新。
- `app/layout.tsx` 与 `app/globals.css` 负责共用布局和样式；`next.config.ts` 管理 API 代理。组件职责和验证入口见 [前端说明](README.md)，修改设置页时继续读取 [设置页规则](app/settings/AGENTS.md)。
- 页面通过本地 API 操作业务数据，不直接连接 SQLite 或携带服务密钥调用云端模型。生产构建的代理地址在构建时读取，验收前先配置 `TUFA_API_URL`。
- 展示真实数据、加载、空态、失败和任务状态，不用演示数据掩盖接口失败；切换项目或查询条件时避免过时响应覆盖新状态。
- 收藏选择经用户确认后写入，取消不产生写入。删除空间须确认；交互状态以服务端结果为准，失败时保留可恢复的操作入口。
- 前端代码或配置修改后运行 `pnpm check:web` 与 `pnpm build:web`，通过实际 HTTP 入口检查首页和受影响页面；交互改动另验证操作结果。纯文档修改遵循根目录的文档检查规则。

<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
