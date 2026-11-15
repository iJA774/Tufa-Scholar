# Tufa Scholar

本地单用户科研助手，提供项目与对话、文献检索和导入、证据定位、科研问答与报告导出。架构与研究预算见 [架构文档](docs/ARCHITECTURE.md)，当前能力见 [项目状态](docs/STATUS.md)，设计取舍与技术债见 [开发文档](docs/DEVELOPMENT.md)。

## 安装

- Python 3.13 与 uv：根目录 `pyproject.toml`、`uv.lock` 管理 API、worker 和 `src/` 共用环境。
- Node.js 22.14 或兼容版本与 pnpm 11.25.0：pnpm workspace 和 `pnpm-lock.yaml` 管理前端。
- SQLite 由 Python 提供，无需单独部署数据库、Docker 或 Conda。

在仓库根目录执行：

```powershell
uv sync --locked
pnpm install --frozen-lockfile
```

## 配置与启动

API 和 worker 必须使用同一个 `TUFA_DATA_DIR`。未设置时数据写到仓库根目录 `data/`，该目录由 Git 忽略。验收脚本使用隔离临时目录，不要把临时数据库放入已有用户数据目录。

模型可在设置页配置 OpenAI 兼容 Base URL、密钥和模型名。也可在运行 API/worker 的环境设置 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`，这些环境变量优先。本机设置保存在 `TUFA_DATA_DIR/research.db`，密钥以明文保存在该本机数据库，接口只返回配置状态和来源。提问时可选模型及思考强度，任务和重试保留所选项。深度研究需要服务支持结构化输出和原生 Function Calling。

网页搜索默认 DuckDuckGo，可在设置页切换 Tavily、InfoQuest、Brave、Serper、arXiv、Searx 或 Wikipedia；各引擎的配置和证据层级见 [docs/WEB_SEARCH.md](docs/WEB_SEARCH.md)。搜索引擎密钥可通过本机设置或相应环境变量提供。

推荐在仓库根目录启动：

```powershell
.\start.ps1
```

默认先构建前端，再启动生产模式 API、单 worker 和前端；构建失败时不启动服务。开发模式使用 `.\start.ps1 -Dev`。启动器只管理本次启动的进程树，按 `Ctrl+C` 停止。3000、8000 端口必须空闲。

也可在三个 PowerShell 终端分别运行：

```powershell
# API
uv run --locked uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
```

```powershell
# 单 worker；API 与 worker 使用相同 TUFA_DATA_DIR
uv run --locked python -m apps.worker
```

```powershell
# 前端
pnpm dev:web
```

打开 [前端首页](http://127.0.0.1:3000)、[API 健康检查](http://127.0.0.1:8000/health) 或 [API 文档](http://127.0.0.1:8000/docs)。前端默认代理到 `http://127.0.0.1:8000`。worker 顺序处理任务；重启时遗留运行中任务会标记为中断，可在界面或 API 重试。不要同时运行多个 worker。`uv run --locked python -m apps.worker --once` 最多处理一个任务后退出，`--check` 只检查存储并退出。

## 使用

打开首页后点击“开始使用”进入研究工作区；GitHub 入口会在新标签页打开 [Tufa Scholar 仓库](https://github.com/iJA774/Tufa-Scholar)。首页不依赖 API。研究工作区使用本机 `/api` 代理。将光标移到左上角标志可展开研究历史，移出标志和抽屉会收起。悬浮在会话上时，可用右侧“…”置顶、解除置顶或删除。置顶状态保存在本机；删除需确认，且有待处理或正在运行任务的会话暂不能删除。打开历史时工作区切换至其所属项目。

在提问框的模式菜单中选择“自动”“深度研究”或“快速探索”。默认使用自动档，由模型选择直接回答或进入研究流程。深度研究无条件进入研究流程，快速探索直接回答并跳过检索工具。设置中的“通用”可选择研究模式、本次模型、思考强度及发送快捷键。通用页底部的“重置所有设置”会恢复默认模式、发送快捷键、研究偏好和外观。API 密钥、服务地址、搜索配置、本次模型和思考强度沿用当前值。

在“研究偏好”中可选择轻量、标准或深入的研究档位，以及是否优先使用原始证据和相关性过滤强度。研究档位决定预算、自动思考强度和提前结束条件。手动选择思考强度时优先使用该值。偏好随新提问提交，重试沿用原任务的模式和偏好。报告格式目前只保存在前端，终稿仍按现有工作流生成。

深度研究先搜索和筛选背景资料，再规划并收集证据，最后生成报告。右侧“活动”可查看计划、审查、搜索结果与工具操作。支持流式响应的模型会边生成边显示 Markdown 草稿。任务完成后显示核验并保存的报告和引用。终稿失败时会标出降级状态，并展示最近一份通过引用检查的研究草稿。快速探索的回答预览显示在聊天区。

点击“设置”会在当前工作区上方打开设置卡片。在“服务与模型”和“网页搜索”中保存配置。“本地文件”可打开浏览器的目录选择器，预览选中目录及子目录中的文件名。“关于”在新标签页打开项目的 GitHub 仓库。

已有文献和通过本机 API 导入的 PDF 按项目保存，新研究沿用当前项目。PDF 按可提取文本取证并保留版本与页码，图片型或加密 PDF 可能无法取证，重复文件按内容摘要去重。

网页搜索工具也可独立调用，结果不自动导入论文库。网页正文读取限制公网地址和重定向；摘要和网页片段不能视作完整原文。Markdown 报告与 CSV 证据表可从完成任务下载。工具注册、参数和来源约束见 [src/tools/README.md](src/tools/README.md)；六个模型提示词 ID 与数据契约见 [docs/PROMPTS.md](docs/PROMPTS.md)。

## API 入口

| 方法与路径 | 功能 |
|---|---|
| `GET /health` | 健康与 SQLite/FTS5 检查 |
| `GET/POST /projects` | 列出、创建项目 |
| `GET/PUT /settings` | 读取配置状态或保存本机配置，不返回密钥原文 |
| `POST /settings/models`、`GET /models` | 发现或列出模型 |
| `GET/POST /projects/{project_id}/conversations` | 列出、创建对话 |
| `GET /conversations` | 跨项目平铺列出历史会话，置顶优先，其余按更新时间倒序，返回 `project_id` 与 `pinned` |
| `PATCH /conversations/{conversation_id}` | 设置 `pinned` 布尔值，返回更新后的会话 |
| `DELETE /conversations/{conversation_id}` | 删除该会话、消息、报告及其任务；若有待处理或正在运行的任务则返回 409 |
| `GET/POST /projects/{project_id}/papers` | 列出、保存论文元数据 |
| `POST /projects/{project_id}/papers/import` | 上传 PDF |
| `GET /projects/{project_id}/web-search?q=...` | 独立网页搜索 |
| `POST /projects/{project_id}/questions` | 提交科研问题 |
| `GET /runs/{run_id}`、`POST /runs/{run_id}/retry` | 查询或重试任务 |
| `GET /runs/{run_id}/events?after=0` | 订阅研究事件，按事件 ID 续读 |
| `GET /conversations/{conversation_id}/messages` | 查询对话消息和证据 |
| `GET /reports/{report_id}/markdown`、`GET /reports/{report_id}/evidence.csv` | 下载报告或证据表 |

提问示例，`source` 可为 `auto`、`local`、`openalex`、`all`、`web` 或 `collection`；`research_mode` 可为 `auto`、`deep` 或 `quick`：

```powershell
$body = @{ question = 'Attention Is All You Need'; source = 'local'; research_mode = 'deep' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/projects/1/questions' -ContentType 'application/json' -Body $body
```

可选任务参数：`research_depth` 为 `light|standard|deep`，默认 `standard`；`prefer_primary_evidence` 为 JSON 布尔值，默认 `true`；`relevance_filter` 为 `relaxed|standard|strict`，默认 `standard`。各档位预算见[架构文档](docs/ARCHITECTURE.md#工作流预算)。

## 验证

在仓库根目录按改动范围运行。

```powershell
uv run --locked python -m unittest discover -s tests -q
uv run --locked python -m scripts.verify_chat
uv run --locked python -m scripts.verify_web_search
pnpm check:web
pnpm build:web
```

两条 `verify_*` 命令使用临时 SQLite、实际本地 API/worker 和模拟上游。浏览器交互和真实供应商需要分别验收。浏览器验收入口见 [前端说明](apps/web/README.md)。

排查研究请求时，可在 `TUFA_DATA_DIR/logs/worker.log` 按 `run_id` 查询模型调用、工具失败和报告校验。供应商用量缺失时日志显示 `na`。结构化响应诊断位于 `TUFA_DATA_DIR/diagnostics/`，可能包含用户材料。可在仓库根目录用 `uv run --locked python -m scripts.replay_diagnostic <诊断文件路径>` 离线校验当前提示词契约。
