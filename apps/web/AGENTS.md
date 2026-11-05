# 前端开发约定

- 前端面向用户，保持美观、简洁，不加入开发者说明或不可用的功能入口。
- 使用 Next.js App Router 与 TypeScript；依赖由根目录 pnpm workspace 和 `pnpm-lock.yaml` 管理。
- 在项目根目录执行 `pnpm dev:web`，默认只监听 `127.0.0.1:3000`；服务端通过 `/api` 代理本地 API，`TUFA_API_URL` 可覆盖默认的 `http://127.0.0.1:8000`。
- 当前提供项目、会话、科研问答、文献与历史搜索、文献导入和项目收藏夹，以及独立设置页；展示真实数据、空态和任务状态。删除空间须经确认；密钥不回显，仅展示配置状态和来源。
- 修改后运行 `pnpm check:web` 与 `pnpm build:web`，并通过实际 HTTP 入口检查首页。

<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
