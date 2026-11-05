# Tufa Scholar

本地单用户科研助手。当前提供项目与对话、基于文献的科研问答、论文检索与导入、证据和单 worker 任务。架构与分阶段目标见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

开发前从 [AGENTS.md](AGENTS.md) 按目录读取规则；能力进度、已知限制与未验证范围统一见 [docs/STATUS.md](docs/STATUS.md)。本 README 保留使用说明和历次实测记录。

开发规范采用根目录全局约束、模块就近补充、按任务读取的方式。没有独立规则的目录继承父级约束，不要求每个目录都建立 `AGENTS.md`。

中文提示词的节点契约、当前时间注入与维护规范见 [docs/PROMPTS.md](docs/PROMPTS.md)。

### 中文提示词接入验收（2026-11-05）

- `uv run --locked python -m unittest discover -s tests -v`：39 项通过。新增模拟 HTTP 边界测试通过实际 `structured()` 加载五个模板，检查 system 消息开头北京时间、跨年逐次更新、用户输入不参与模板替换和输出结构解析。
- `git diff --check`：通过。未调用真实云端模型，未启动真实业务链路；提示词遵循程度与实际回答质量由用户后续自行验证。

本地项目文献通过 SQLite FTS5/BM25 关键词检索，外部结果由 OpenAlex 文本检索提供；选择两者时用 RRF 合并排名并保留来源排名。当前没有向量检索或模型重排，尚无完整综述修订、OCR 和引用扩展。上传 PDF 时提取可复制文本；扫描件无可提取文本时保留原文件并标记状态。检索只返回元数据与链接，不自动下载 PDF。

## 环境与安装

- Python 3.13 和 uv：根目录 `pyproject.toml`、`uv.lock` 管理 API、worker 共用环境。
- Node.js 22.14 或兼容版本和 pnpm 11.25.0：根目录 pnpm workspace、`pnpm-lock.yaml` 管理前端。
- SQLite 由 Python 提供，无需数据库服务、Docker 或 Conda。

在项目根目录执行：

```powershell
uv sync --locked
pnpm install --frozen-lockfile
```

## 配置与启动

API 与 worker 必须使用同一个 `TUFA_DATA_DIR`；不设置时使用项目根目录下 `data/`。相对路径以项目根目录为基准。运行数据与上传原文保存在该目录；默认的 `data/` 被 Git 忽略。设置独立验收目录示例：

```powershell
$env:TUFA_DATA_DIR = '.cache/data-acceptance'
```

OpenAlex 支持无密钥试用，可在 `/settings` 配置密钥，或在 worker 进程环境中设置 `OPENALEX_API_KEY` 使用自己的额度；额度与限制见 [OpenAlex 认证说明](https://help.openalex.org/api/authentication/)。设置页也可保存 OpenAI 兼容模型的地址、模型名和密钥，用于 LangGraph 科研问答，需配置有效服务与模型；真实模型服务尚未在本次验收中实测。本机设置保存在 `TUFA_DATA_DIR/research.db`，密钥以明文存储在该本机数据库中；`OPENALEX_API_KEY`、`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL` 环境变量优先于本机设置。接口只返回密钥是否配置及来源，不回显密钥；清除密钥仅删除本机保存值，环境变量仍然生效。模型地址或密钥变动后，设置页自动获取模型列表，也可刷新或手动填写；更换服务来源时若未输入新密钥，将清除旧的本地模型密钥。环境变量中的地址、模型名和密钥仍优先。支持 OpenAI 兼容模型列表，包括火山方舟、腾讯 TokenHub；百炼兼容列表不可用时使用其官方分页列表。列表包含的模型不一定都支持本应用所需的对话和 JSON 输出。自定义地址应填写 OpenAI 兼容 Base URL，未提供模型列表的服务可手动填写。OpenAlex 配置在每次请求时读取，无需重启 worker。选择 `all` 时若 OpenAlex 请求失败，保留本地结果并提示外部检索不可用；单独选择 OpenAlex 时返回错误。

推荐在项目根目录的 PowerShell 中一条命令启动全部服务：

```powershell
.\start.ps1
```

脚本默认先构建前端，再以生产模式启动 API、单 worker 和前端。构建失败时不启动服务。需要开发模式时运行 `.\start.ps1 -Dev`，跳过生产构建并使用开发服务器。前台窗口按 `Ctrl+C` 停止；Windows Job Object 管理本次启动的进程树，停止时只清理这些进程；构建与服务日志保存在启动时显示的 `.cache/start/` 子目录。启动前需完成上面的依赖安装，且 3000、8000 端口必须空闲；脚本不会停止已有服务。脚本固定将前端 `TUFA_API_URL` 设为本次启动的 `http://127.0.0.1:8000`，子进程继承当前环境。若执行策略阻止脚本，可单次使用 `powershell -ExecutionPolicy Bypass -File .\start.ps1`，无需修改持久策略。

也可以分别在三个 PowerShell 终端进入项目根目录手动启动，并为 API 和 worker 设置同样的数据目录：

```powershell
# 终端 1：API
uv run --locked uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
```

```powershell
# 终端 2：单 worker
uv run --locked python -m apps.worker
```

```powershell
# 终端 3：前端
pnpm dev:web
```

打开 [首页](http://127.0.0.1:3000)、[健康检查](http://127.0.0.1:8000/health) 或 [API 文档](http://127.0.0.1:8000/docs)。前端默认将 `/api` 请求代理至 `http://127.0.0.1:8000`；使用其他地址时，在前端启动前设置 `TUFA_API_URL`。生产构建的 Next.js rewrites 在构建时读取该变量，因此需在 `pnpm build:web` 前设置。默认前端和 API 监听回环地址。

开发模式左下角的 N 图标是 [Next.js 开发工具入口](https://nextjs.org/docs/app/api-reference/config/next-config-js/devIndicators)；默认生产模式不显示该图标。

worker 按顺序领取任务；重启时将遗留的运行中任务标记为中断，可在界面或 API 重新执行。`uv run --locked python -m apps.worker --once` 最多处理一个待处理任务并退出，`--check` 只检查存储并退出。不要同时运行多个 worker。

## 日志

API 和 worker 分别将诊断日志写入 `TUFA_DATA_DIR/logs/api.log` 与 `worker.log`，单文件达到 5 MiB 后轮转，最多保留 3 个备份；直接启动服务同样生效。日志记录请求路由、状态、耗时、任务阶段和服务调用状态，不记录密钥、请求正文或完整问题。启动器的标准输出日志仍保存在 `.cache/start/`。

## 使用与接口

在首页创建项目与对话，输入研究问题并选择本地、OpenAlex 或全部来源。任务完成后可查看回答及证据，下载报告与证据表；右上角搜索项目内会话、消息和文献。也可以上传 PDF；重复字节的文件按内容摘要去重，DOI 或 arXiv ID 用于识别文献。项目内文献跨对话共享。

文献搜索可查询本地项目和 OpenAlex，并将结果保存到项目。点击文献收藏按钮后，先选择目标收藏夹并确认；取消不会添加。已收藏的文献可再次点击移出。左侧收藏夹入口支持创建、查看成员、移除文献和删除收藏夹；删除收藏夹保留项目文献及已提交任务。提问时可选择收藏夹：任务提交时固定文献列表，检索和引用仅限该列表与当前项目的交集，不使用其他对话历史或外部检索；空收藏夹直接报告证据不足。之后修改或删除收藏夹不改变已提交任务及其重试的文献范围。

空间右侧菜单可删除空间；有执行中的任务时需等任务结束。删除空间会移除其对话、任务与文献关联，保留全局文献和原文文件以及其他空间的关联。

API 具体字段和交互可在 `/docs` 查看。主要接口：

| 方法与路径 | 功能 |
|---|---|
| `GET /health` | 健康与 SQLite/FTS5 检查 |
| `GET/POST /projects` | 列出、创建项目 |
| `DELETE /projects/{project_id}` | 删除空间；存在运行中任务时返回 409 |
| `GET/PUT /settings` | 读取配置状态或保存本机配置；不返回密钥原文 |
| `POST /settings/models` | 使用草稿地址与密钥查询模型列表，不保存设置或回显密钥 |
| `GET/POST /projects/{project_id}/conversations` | 列出、创建对话 |
| `GET/POST /projects/{project_id}/papers` | 列出文献、保存元数据 |
| `POST /projects/{project_id}/papers/import` | multipart 上传 PDF，可附 `title`、`doi`、`arxiv_id` |
| `GET /paper-versions/{version_id}/file` | 查看已上传原文 |
| `POST /projects/{project_id}/questions` | 提交科研问题，返回任务与会话 ID |
| `GET /conversations/{conversation_id}/messages` | 读取会话消息及证据 |
| `GET /projects/{project_id}/search?q=...` | 搜索项目内会话、消息和文献 |
| `GET /reports/{report_id}/markdown`、`GET /reports/{report_id}/evidence.csv` | 下载报告与证据表 |
| `GET/POST /projects/{project_id}/runs` | 列出任务、提交检索任务 |
| `GET /runs/{run_id}` | 查询任务状态、结果或错误 |
| `POST /runs/{run_id}/retry` | 重新执行失败或中断任务 |

提交任务示例，`source` 可为 `local`、`openalex` 或 `all`，`conversation_id` 可省略：

```powershell
$body = @{ query = 'Attention Is All You Need'; source = 'local' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/projects/1/runs' -ContentType 'application/json' -Body $body
# 用返回的 run_id 查询，worker 必须运行
Invoke-RestMethod 'http://127.0.0.1:8000/runs/1' | ConvertTo-Json -Depth 10
```

## 验证

```powershell
uv run --locked python -m unittest discover -s tests -v
powershell -NoProfile -ExecutionPolicy Bypass -File tests/test_launcher_job.ps1
uv run --locked python -m apps.worker --check
pnpm check:web
pnpm build:web
```

`--check` 检查 SQLite WAL、外键和 FTS5。测试可使用独立数据目录，避免修改已有项目数据。

空间删除与设置的 HTTP 验收使用 `scripts/verify_workspace_settings.py`：先启动 API 并指向独立的空数据目录，停止 worker，确保 API 进程未设置 OpenAlex/OpenAI 环境配置；在脚本进程设置同一个 `TUFA_DATA_DIR`、API 地址 `TUFA_API_URL` 和 `TUFA_VERIFY_ISOLATED=1`，再运行 `uv run --locked python scripts/verify_workspace_settings.py`。脚本会在独立数据库模拟运行中任务、创建空间与 PDF，并写入后清除测试设置，不使用默认数据目录。

### 本机实测（2026-11-04）

| 命令或入口 | 结果 |
|---|---|
| `uv run --locked python -m unittest tests.test_retrieval tests.test_worker -v` | 5 项通过：假上游 fixture 映射、凭据放在请求头、去重与排名、实际 worker 入口本地固定问题排名、重启中断处理 |
| `uv run --locked python -m unittest discover -s tests -v` | 6 项通过 |
| `uv run --locked python -m unittest discover -s tests -v`（空间删除与设置更新后） | 10 项通过 |
| 在独立目录 `.cache/acceptance-workspace-settings-20261104` 启动 API、停止 worker，设置 `TUFA_VERIFY_ISOLATED=1` 后执行 `uv run --locked python scripts/verify_workspace_settings.py` | 实际 HTTP 验收通过：设置保存、密钥不回显及清除，空间删除的 409/204/404、共享文献与原文保留 |
| `./start.ps1` 默认模式 | 先执行 `pnpm build:web`，再运行 `pnpm start:web`；`/settings` HTTP 200，`scripts/verify_retrieval.py` 通过。浏览器验证删除的取消和确认、自动选择另一空间，以及设置保存、刷新、不回显和清除；生产模式无 N 图标 |
| `./start.ps1 -Dev` | 跳过构建并运行 `pnpm dev:web`；首页和 `/settings` 均 HTTP 200。最终 `pnpm check:web` 通过 |
| `pnpm check:web`、`pnpm build:web` | 前端类型检查和生产构建通过 |
| API 与单 worker 共用 `.cache/acceptance-retrieval-20261104`，设置 `TUFA_API_URL=http://127.0.0.1:8000` 后执行 `uv run --locked python scripts/verify_retrieval.py` | 实际 HTTP 与 worker 验收通过：PDF 正文词检索、无可提取文本标记、重复导入、新版本、跨项目和会话、404/422；`/health` 返回 WAL、外键和 FTS5 正常 |
| 从 `E:\` 使用绝对路径运行 `start.ps1`，数据目录设为 `.cache/acceptance-launcher-20261104` | 首页、健康检查与 `/api/projects` 均 HTTP 200，`scripts/verify_retrieval.py` 通过；重复启动因端口占用退出 1 且原服务保持运行；Ctrl+C 后 3000、8000 端口释放，验收服务已停止 |
| PowerShell 7.6.9 实际运行 `start.ps1`，数据目录 `.cache/acceptance-shutdown-ps51` | API、前端和代理均 HTTP 200，`scripts/verify_retrieval.py` 通过；连续两次 Ctrl+C 后端口与 worker 无残留；运行时再次启动被拒绝且原服务保持运行 |
| Windows PowerShell 5.1.26100.9711 交互会话运行 `start.ps1`，数据目录 `.cache/acceptance-shutdown-real-ps51` | 同一宿主连续两次 Ctrl+C 返回提示符后立即重启成功；重启后 `scripts/verify_retrieval.py` 通过，主动结束本次 worker helper 后启动器检测失败并清理所有服务，返回同一提示符 |
| 前端生产服务 `next start` 监听 3107 端口 | 首页和 `/api/projects` 均 HTTP 200；浏览器提交 OpenAlex 检索，原论文排第 1，保存后刷新仍能看到文献及任务 |
| `uv run python -c "from src.integrations.openalex import search_works; print([(x['title'], x['doi']) for x in search_works('Attention Is All You Need', 5)])"` | 真实 OpenAlex 无密钥请求成功；《Attention Is All You Need》（DOI `10.48550/arxiv.1706.03762`）第 1，第 2 为《Attention Is All You Need In Speech Separation》，第 3 为《Channel Attention Is All You Need for Video Frame Interpolation》 |

以上 OpenAlex 排名是该次请求的观察结果，服务数据和排名可能变化。OpenAlex 检索参数和默认相关性排序见[官方搜索文档](https://help.openalex.org/api/searching/)。

本次改动的真实 `attention` 检索在默认配置下成功返回结果；独立空配置、无密钥测试中，原 urllib 请求发生 TLS 超时或异常 EOF，同一端点的 httpx 请求返回 3 篇结果。现已使用项目已有的 httpx；外部网络可用性仍可能波动。

验收使用独立目录，未向默认 `data/` 写入测试文献；验收服务已停止，测试数据和截图保留在 `.cache/acceptance-retrieval-20261104/` 和 `.cache/acceptance-workspace-settings-20261104/`。未验收开发模式热更新；向量检索、模型重排、OCR 和完整综述修订尚未实现；真实模型服务尚未实测。


### 科研问答闭环验收

- `uv run --locked python -m unittest discover -s tests -v`：21 项通过，覆盖会话/报告原子保存、重试上下文边界、历史隔离、图分支、补检停止及证据核验。
- `uv run --locked python scripts/verify_chat.py`：使用本地**模拟** OpenAI 兼容接口、临时 SQLite、实际 API 和 worker 验证 PDF 页码引用、无配置失败、重试、缺失成本证据及 Markdown/CSV 导出。不会调用云端模型。
- `pnpm check:web`、`pnpm build:web`：通过。浏览器在隔离模拟环境中完成提问、回答、历史搜索命中及返回会话、刷新恢复；预览保存于 `.cache/chat-ui/`。

真实云端模型由用户稍后配置，尚未验证服务兼容性、实际回答质量与调用耗时。当前证据范围和数值格式限制见 `src/literature/README.md`。验收未向默认数据目录写入测试文献或模型配置。

本轮模拟验收服务已停止，未修改默认用户数据；截图中的回答来自测试接口，并非真实云端模型输出。


### 文献搜索、收藏夹与日志验收

- `uv run --locked python -m unittest discover -s tests -v`：31 项通过；包含收藏夹项目隔离、提问快照、重试、模型输入排除其他论文、日志脱敏和轮转。
- `uv run --locked python scripts/verify_chat.py --serve`：在独立数据库、模拟模型和真实 API/worker 中验证收藏夹成员变更不改变已提交问题的论文范围；真实云端模型仍未实测。
- `pnpm check:web`、`pnpm build:web`：通过。浏览器已验证自定义收藏夹创建，搜索结果和本地论文加入后变为减号、再次点击移除，以及收藏夹列表移除、空夹禁用提问和限定收藏夹问答。截图位于 `.cache/collections-ui/collections.jpg`。
- 修改后的 OpenAlex 浏览器入口实测匿名请求返回 HTTP 429，页面正确显示限流/外部检索未完成提示，不再误报没有结果。可在设置中配置 OpenAlex 密钥或稍后重试，外部服务可用性不由本地应用保证。
- 真实 API/worker 的日志文件已核对，包含路由、任务阶段、模型调用耗时和状态；没有测试正文或密钥。默认路径是 `data/logs/api.log` 和 `data/logs/worker.log`，自定义数据目录下使用相同的 `logs/` 子目录。

本轮验收服务已停止，未向用户默认数据库写入测试论文、收藏夹或模型配置。

### 收藏交互与侧栏验收

- `uv run --locked python -m unittest discover -s tests -v`：33 项通过；`pnpm check:web` 和 `pnpm build:web` 均通过。
- 在隔离模拟 API 和模型环境中，浏览器验证了取消不收藏、确认后显示减号、刷新后保持、点击减号移除，以及侧栏浏览收藏夹、从当前夹移除文献和打开删除确认弹窗；最终删除由后端 API 测试验证。
- 页面滚动到 `scrollY=720` 时，侧栏顶部为 0、高度为 720，且不再显示旧的“本地研究空间”标签。本轮未验证真实云端模型或 OpenAlex 联网。

### 模型列表验收

- `uv run --locked python -m unittest discover -s tests -v`：38 项通过，覆盖模型列表获取、密钥优先级和脱敏、百炼分页与路径、设置保存。
- `pnpm check:web`、`pnpm build:web` 和 `git diff --check` 均通过。
- 使用 `uv run --locked python -m tests.model_discovery_server --serve` 启动临时数据库和本地模拟模型服务；设置 `TUFA_API_URL=http://127.0.0.1:18180`，执行 `pnpm --filter @tufa-scholar/web exec next dev --hostname 127.0.0.1 --port 18300`。通过 PowerShell `Invoke-WebRequest` 和 `Invoke-RestMethod` 验证前端 HTTP 首页、设置页、模型发现及选中模型保存和回读，以及空列表与上游错误脱敏。
- 本轮未完成浏览器交互验收，也未连接真实云端模型服务。验收服务已停止，18180、18181 和 18300 端口无监听；未修改默认用户数据。
