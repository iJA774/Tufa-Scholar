# Tufa Scholar

本地单用户科研助手，提供项目与对话、文献检索和导入、证据定位、科研问答与报告导出。架构目标见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，已实现能力和验证边界见 [docs/STATUS.md](docs/STATUS.md)。开发前按 [AGENTS.md](AGENTS.md) 阅读适用规则；任务和分支上下文见 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)。

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

模型可在设置页配置 OpenAI 兼容 Base URL、密钥和模型名。也可在运行 API/worker 的环境设置 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`；环境变量优先于本机设置。本机设置保存在 `TUFA_DATA_DIR/research.db`，密钥以明文存于该本机数据库；接口只返回是否已配置和配置来源，不回显密钥。提问时可选模型及思考强度，任务和重试会保留所选项。模型列表不保证其支持应用所需的 JSON 输出。

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

打开首页后点击“开始使用”进入研究工作台；GitHub 入口会在新标签页打开 [Tufa Scholar 仓库](https://github.com/iJA774/Tufa-Scholar)。首页不依赖 API。工作台继续使用本机 `/api` 代理和原有 API 功能。

在工作台创建项目和对话后输入问题。提问范围包括自动、项目本地论文、外部论文、混合检索、网页和收藏夹。自动范围先判断是否需要研究；研究问题由背景调查实际搜索和筛选资料，再规划至多两轮，最后生成终稿。直接回答不调用检索工具。可展开任务进度查看各阶段研究小结，完成后查看回答、引用和证据。

项目文献可上传 PDF、从检索结果保存或加入收藏夹。PDF 按可提取文本取证并保留版本与页码；图片型或加密 PDF 可能无法取证。重复文件按内容摘要去重。收藏夹在任务提交时固定成员快照；后续修改收藏夹不会改变已提交任务或重试的范围。收藏夹问题不读取其他会话历史或搜索夹外来源；空集合返回证据不足。

网页搜索工具也可独立调用，结果不自动导入论文库。网页正文读取限制公网地址和重定向；摘要和网页片段不能视作完整原文。Markdown 报告与 CSV 证据表可从完成任务下载。工具注册、参数和来源约束见 [src/tools/README.md](src/tools/README.md)；六个模型提示词 ID 与数据契约见 [docs/PROMPTS.md](docs/PROMPTS.md)。

## API 入口

| 方法与路径 | 功能 |
|---|---|
| `GET /health` | 健康与 SQLite/FTS5 检查 |
| `GET/POST /projects` | 列出、创建项目 |
| `GET/PUT /settings` | 读取配置状态或保存本机配置，不返回密钥原文 |
| `POST /settings/models`、`GET /models` | 发现或列出模型 |
| `GET/POST /projects/{project_id}/conversations` | 列出、创建对话 |
| `GET/POST /projects/{project_id}/papers` | 列出、保存论文元数据 |
| `POST /projects/{project_id}/papers/import` | 上传 PDF |
| `GET /projects/{project_id}/web-search?q=...` | 独立网页搜索 |
| `POST /projects/{project_id}/questions` | 提交科研问题 |
| `GET /runs/{run_id}`、`POST /runs/{run_id}/retry` | 查询或重试任务 |
| `GET /conversations/{conversation_id}/messages` | 查询对话消息和证据 |
| `GET /reports/{report_id}/markdown`、`GET /reports/{report_id}/evidence.csv` | 下载报告或证据表 |

提问示例，`source` 可为 `auto`、`local`、`openalex`、`all`、`web` 或 `collection`：

```powershell
$body = @{ question = 'Attention Is All You Need'; source = 'local' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/projects/1/questions' -ContentType 'application/json' -Body $body
```

## 验证

### 诊断

结构化模型响应诊断保存在 `TUFA_DATA_DIR/diagnostics/`，可能包含问题、用户材料和模型响应，应按私有数据保管。仓库根目录可用 `.venv/Scripts/python.exe -B -m scripts.replay_diagnostic <诊断文件路径>` 对当前六个 prompt schema 做离线检查；不联网、不访问数据库。脱敏和环境差异可能影响复现。该工具不支持旧工作流 schema。

常用检查命令在仓库根目录执行：

```powershell
uv run --locked python -m unittest discover -s tests -v
uv run --locked python -m scripts.verify_chat
uv run --locked python -m scripts.verify_web_search
pnpm check:web
pnpm build:web
```

`verify_chat` 和 `verify_web_search` 使用隔离临时数据目录，自启实际本地 API/worker，并连接本地模拟模型及搜索 HTTP 服务；它们检查业务闭环和成功、失败后的资源清理，不调用真实云服务。真实模型输出、搜索服务权限/限流/质量及浏览器交互需分别验收。

### 已完成的前端检查（2026-11-12）

- `pnpm check:web`：通过。
- `pnpm build:web`：通过。
- 临时启动构建后的 Next.js 服务，以随机回环端口请求 `/` 与 `/settings`：均返回 HTTP 200；首页加载的 JavaScript 包含八个工作流阶段映射。
- 未执行真实浏览器交互；构建和 HTTP 检查不代表浏览器端到端交互已验证。

### 新工作流与接口验收（2026-11-12）

- `uv run --locked python -m unittest discover -s tests -v`：138 tests OK，12.322 秒。
- `uv run --locked python -m scripts.verify_chat`：通过，8.48 秒。实际 API 与 worker 使用隔离临时数据，模拟模型 HTTP；覆盖未配置模型失败、重试、直接回答不调研究节点、PDF 页证据、报告导出和空收藏夹终稿。
- `uv run --locked python -m scripts.verify_web_search`：通过，9.58 秒。实际 API 与 worker 使用本地模拟 Searx 和模型 HTTP；覆盖搜索筛选、摘要证据和报告、两轮后强制终稿、供应商失败重试及无证据终稿。
- 两个脚本都验证自启 API/worker、模拟 HTTP 服务和临时目录在成功与失败路径后收尾；验收健康检查确认 API 指向本次临时 SQLite。
- 使用返回非隔离数据库路径的占用端口 HTTP mock 验证隔离保护：两个脚本均非零退出、零业务写请求，并完成自有资源清理。
- 前端检查见上节。未调用真实云端模型或真实搜索供应商，未执行浏览器交互；供应商权限、限流、内容质量及真实模型输出仍未验证。

### 开始页与工作台入口验收（2026-11-13）

- `pnpm check:web`：最终通过；修正首轮检查发现的 `ResizeObserver` 类型收窄问题后复验通过。
- `$env:TUFA_API_URL = 'http://127.0.0.1:50187'; pnpm build:web`：通过。构建后的 `/`、`/workspace`、`/settings`、`/api/projects` 均返回 HTTP 200。
- 桌面浏览器 1440×900：开始页 LED 点亮与呼吸动画符合原稿；键盘 Enter 激活“开始使用”后进入 `/workspace`，并加载模拟项目、历史和文献。设置页的“返回研究空间”进入 `/workspace`；GitHub 打开新标签页，地址为 `https://github.com/iJA774/Tufa-Scholar`。
- 返回首页后动画正常；浏览器 console 无 warning/error，浅色研究工作台未受开始页样式污染。动画的 `reduced-motion`、页面隐藏和清理行为只做源码审查，未做浏览器媒体模拟。
- 页面通过 `scripts.fixture_api` 和隔离 `.cache` 数据提供模拟内容；该 UI fixture 不启动 worker，也不调用真实模型或真实检索。临时前端、API 与参考服务已停止。截图保存在 `.cache/launch-page/desktop.jpg`。
