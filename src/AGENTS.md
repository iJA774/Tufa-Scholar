# Python 模块约定

## 职责与依赖

| 模块 | 职责 | 就近规则 |
|---|---|---|
| `agents/` | LangGraph 工作流、任务阶段、综合与核验 | [工作流](agents/AGENTS.md) |
| `tools/` | 工具发现、参数契约、范围约束与业务适配 | [工具](tools/AGENTS.md) |
| `literature/` | 检索排名、收藏夹候选与证据提取 | [文献业务](literature/AGENTS.md) |
| `integrations/` | 外部搜索与模型协议 | [集成](integrations/AGENTS.md) |
| `persistence/` | SQLite 初始化、仓储及事务 | [持久化](persistence/AGENTS.md) |
| `infrastructure/` | 设置解析、环境变量与日志 | [基础设施](infrastructure/AGENTS.md) |
| `prompts/` | Markdown 提示词正文 | [提示词](prompts/AGENTS.md) |

- 工作流经工具调用科研能力，工具适配文献业务与服务集成；文献业务不得依赖工具注册器或 Agent 图状态。
- 仓储不负责联网或模型推理。应用与科研代码复用持久化和基础设施模块，不各自实现存储、配置或脱敏规则。
- `src/` 不依赖 `apps/` 路由或界面，不引入 HTTP 响应类型。根目录仅保留包入口，业务 Python 文件放入职责对应的子目录。
- 模块移动必须同步真实导入、测试 mock、脚本入口与文档路径；基于文件位置推导的提示词和数据路径仍须保持既有语义。
