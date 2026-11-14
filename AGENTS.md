# 仓库规则

本项目是本机单用户科研助手。此处约束全仓库；各模块的长期专属约束放在职责目录内的 `AGENTS.md`。

## 阅读与文档

- 开始改动前阅读本文件、相关架构章节、目标代码及沿路径适用的 `AGENTS.md`。跨模块改动读取各自规则。
- `README.md` 是安装、配置、启动、使用和稳定验证命令的入口。`docs/DEVELOPMENT.md` 记录当前任务、关键决策及理由、考虑过的替代方案和接受的技术债；`docs/ARCHITECTURE.md` 是设计、接口边界和预算的权威来源；`docs/STATUS.md` 记录已实现能力与限制。
- 详细测试结果属于本次交付或 PR 说明，不归档到规则、架构、进度或状态文档。只有长期约束变化时才修改 `AGENTS.md`。
- 长期设计、实际能力和当前任务必须分开表述。架构目标不代表已实现，也不授权扩大本次任务范围。

## 全局约束

- 保持单机单用户、SQLite 和单 worker；API 与前端默认只监听回环地址。没有明确需求时，不引入鉴权令牌、租约、分布式组件或多后端抽象。
- Python 依赖由根 `pyproject.toml`/`uv.lock` 管理，前端依赖由根 pnpm workspace/lockfile 管理；不创建重复环境。
- API 与 worker 共用 `TUFA_DATA_DIR`。保护用户数据库、原文、报告和本机配置；测试使用独立数据目录，不把用户材料、密钥或本机配置提交到 Git。
- 新任务从 `main` 创建 `codex/*` 分支，同一任务沿用原分支。完成后仅本地提交本任务改动，保留用户已有的其他改动。
- 环境变量优先于本机设置。接口、错误和日志不得泄露密钥、用户材料或不必要的上游内容。
- 提示词正文放在 `src/prompts/`；数据结构、数值检查、证据范围与定位由代码负责。测试断言可观察行为，不以固定摘要锁定提示词、整份报告或目录内容。
- 修改代码时按 README 和相关模块规则验证实际入口。纯文档改动检查路径、链接和规则一致性，不启动应用或全量构建。

## 目录导航

| 范围 | 职责 | 局部规则 |
|---|---|---|
| `apps/api/` | HTTP 接口、校验与文件交付 | [API](apps/api/AGENTS.md) |
| `apps/worker/` | 单 worker 任务生命周期 | [Worker](apps/worker/AGENTS.md) |
| `apps/web/` | Next.js 界面与 API 代理 | [前端](apps/web/AGENTS.md) |
| `src/` | Python 模块职责与依赖方向 | [共享代码](src/AGENTS.md) |
| `src/agents/` | 科研工作流 | [工作流](src/agents/AGENTS.md) |
| `src/literature/` | 检索、排名与证据提取 | [文献业务](src/literature/AGENTS.md) |
| `src/tools/` | 可独立调用的科研工具 | [工具](src/tools/AGENTS.md) |
| `src/integrations/` | 外部搜索与模型协议 | [集成](src/integrations/AGENTS.md) |
| `src/persistence/` | SQLite、仓储与事务 | [持久化](src/persistence/AGENTS.md) |
| `src/infrastructure/` | 本机设置、环境与日志 | [基础设施](src/infrastructure/AGENTS.md) |
| `src/prompts/` | Markdown 提示词 | [提示词](src/prompts/AGENTS.md) |
| `tests/` | 回归测试与模拟服务 | [测试](tests/AGENTS.md) |
| `scripts/`、`start.ps1` | 启动、进程管理与入口验收 | [脚本](scripts/AGENTS.md) |
| `docs/` | 项目文档 | [文档](docs/AGENTS.md) |

进入目标目录时仍须检查更近的规则；`data/` 和 `.cache/` 是运行数据与临时产物位置，不存放源码或开发规则。
