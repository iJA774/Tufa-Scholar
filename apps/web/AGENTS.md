# 前端规则

- 面向用户保持简洁。文案只服务于操作、可访问名称和真实状态；禁止装饰性解释、口号、英文眉题、重复标签及不可用入口。
- 使用根 workspace 管理的 Next.js App Router 与 TypeScript。前端通过本机 `/api` 代理访问业务数据，不直连 SQLite，不携带服务密钥调用云端模型；默认仅监听回环地址。
- 展示真实数据、加载、空态、失败和任务状态，不用演示数据掩盖接口失败。异步操作须避免旧响应覆盖新上下文，并在切换或卸载时清理相关工作。
- 项目范围由 API/worker 约束，前端不得把其他项目的资料混入当前研究。密钥仅通过本机设置接口处理。本地文档使用服务端固定原文目录，由用户显式启动预处理。界面只显示相对文件名，并说明分类使用配置的模型服务。
- 页面职责和稳定的验证入口见本目录 README；实际交互改动需验证相应页面操作。

<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
