# Tufa Scholar

本地单用户科研助手。当前提供项目与对话、基于文献的科研问答、论文检索与导入、证据和单 worker 任务。架构与分阶段目标见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

开发前从 [AGENTS.md](AGENTS.md) 按目录读取规则；能力进度、已知限制与未验证范围统一见 [docs/STATUS.md](docs/STATUS.md)。本 README 保留使用说明和历次实测记录。

开发规范采用根目录全局约束、模块就近补充、按任务读取的方式。没有独立规则的目录继承父级约束，不要求每个目录都建立 `AGENTS.md`。

中文提示词的节点契约、当前时间注入与维护规范见 [docs/PROMPTS.md](docs/PROMPTS.md)。

可独立调用的科研工具及扩展方式见 [src/tools/README.md](src/tools/README.md)。当前任务、工作分支与协作安排统一见 [开发进度](docs/DEVELOPMENT.md)。

当前 Python 代码按以下职责组织（省略规则与说明文件）：

```text
src/
├── __init__.py
├── agents/
│   ├── __init__.py
│   └── literature.py
├── tools/
│   ├── __init__.py
│   ├── registry.py
│   ├── search.py
│   └── read_paper.py
├── integrations/
│   ├── openalex.py
│   ├── model.py
│   └── web_search.py
├── literature/
│   ├── retrieval.py
│   └── evidence.py
├── persistence/
│   ├── __init__.py
│   ├── storage.py
│   └── repository.py
├── infrastructure/
│   ├── __init__.py
│   ├── settings.py
│   └── logging_setup.py
└── prompts/
    ├── literature/
    └── runtime/
```

工作流说明见 [agents](src/agents/README.md)，检索与取证业务见 [literature](src/literature/README.md)。提示词从 `src/prompts/` 加载，调用 ID 仍为 `literature/模板名`。默认数据仍在仓库根目录 `data/`，相对 `TUFA_DATA_DIR` 也以仓库根目录解析。

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

OpenAlex 支持无密钥试用，可在 `/settings` 配置密钥，或在 worker 进程环境中设置 `OPENALEX_API_KEY` 使用自己的额度；额度与限制见 [OpenAlex 认证说明](https://help.openalex.org/api/authentication/)。设置页可保存 OpenAI 兼容模型的地址和密钥，提问页选择模型和思考强度，用于 LangGraph 科研问答，需配置有效服务与模型；真实模型服务尚未在本次验收中实测。本机设置保存在 `TUFA_DATA_DIR/research.db`，密钥以明文存储在该本机数据库中；`OPENALEX_API_KEY`、`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL` 环境变量优先于本机设置。接口只返回密钥是否配置及来源，不回显密钥；清除密钥仅删除本机保存值，环境变量仍然生效。服务配置保存后，提问页从有效设置获取模型列表，可刷新或手动填写模型；更换服务来源时若未输入新密钥，将清除旧的本地模型密钥。环境变量中的地址、模型名和密钥仍优先。支持 OpenAI 兼容模型列表，包括火山方舟、腾讯 TokenHub；百炼兼容列表不可用时使用其官方分页列表。列表包含的模型不一定都支持本应用所需的对话和 JSON 输出。自定义地址应填写 OpenAI 兼容 Base URL，未提供模型列表的服务可手动填写。OpenAlex 配置在每次请求时读取，无需重启 worker。选择 `all` 时若 OpenAlex 请求失败，保留本地结果并提示外部检索不可用；单独选择 OpenAlex 时返回错误。

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

更新依赖后重新启动服务前，在仓库根目录运行 `uv sync --locked`。网页搜索默认使用免密钥 DuckDuckGo；可在设置页切换 Tavily、InfoQuest、Brave、Serper、arXiv、Searx 或 Wikipedia。右上角搜索窗口新增“网页搜索”；提问默认“自动”可由主控选择网页研究；所选搜索受阻或连续两次无候选空结果时，可尝试 DDGS 的 Bing/Yahoo 备用检索，也可明确选择“网页搜索”。论文和网页范围优先对应来源，发现缺口时可互相补充；“仅项目文献”和收藏夹保持隔离。服务配置、环境变量与证据限制见 [网页搜索说明](docs/WEB_SEARCH.md)。

科研问答输入框下方，检索范围旁可选择模型和思考强度。优先使用供应商 `effort.supported_levels` 与 `default_level`；未声明档位或目录暂不可用时，提供低、中、高三个选项，映射为 `reasoning_effort` 的 `low`、`medium`、`high`。“供应商默认”不发送该参数。通用回退不保证每个模型支持显式强度，服务拒绝时可改回默认。能力字段参考 [DeepSeek](https://api-docs.deepseek.com/api/list-models/) 与 [火山方舟](https://console.volcengine.com/ark/region%3Acn-beijing/docs/models-api) 官方文档。

模型与强度随本次问题存入任务，重试沿用；环境变量 `OPENAI_MODEL` 在提交时优先。任务各科研节点使用保存的选择，服务地址与密钥仍在调用时从有效配置读取。`GET /models` 返回已配置服务的模型 ID、思考档位与默认档位；提问接口可传 `model_name` 和 `reasoning_effort`，省略时使用配置模型和供应商默认行为。

### 提问模型与思考强度验收（2026-11-05）

- `uv run --locked python -m unittest discover -s tests -v`：43 项通过；覆盖供应商档位、三档回退、环境变量优先、快照与重试，以及实际模型调用函数发送的参数。模型 HTTP 使用模拟响应。
- `pnpm check:web`、`pnpm build:web`：通过。
- `uv run --locked python -m tests.model_discovery_server --serve` 配合 `TUFA_API_URL=http://127.0.0.1:18180` 和 18300 端口的 Next dev，以 `Invoke-WebRequest` / `Invoke-RestMethod` 检查首页、设置页均为 200、代理目录返回两项模型、提问后任务保存指定模型与 medium 强度，保持 pending；未启动 worker。
- 隔离服务已停止，未修改默认用户数据；未运行真实模型链路，浏览器交互与真实供应商参数兼容性留待用户验证。

在首页创建项目与对话，输入研究问题，默认由模型自动路由，也可选择本地、OpenAlex、本地论文和 OpenAlex、网页或收藏夹。任务执行中可展开“研究过程”查看初步小结与来源，完成后可在历史回答中查看；小结仍需最终核验。任务完成后可查看回答及证据，下载报告与证据表；右上角搜索项目内会话、消息和文献。也可以上传 PDF；重复字节的文件按内容摘要去重，DOI 或 arXiv ID 用于识别文献。项目内文献跨对话共享。

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

提交任务示例，`source` 可为 `auto`、`local`、`openalex`、`all`、`web` 或 `collection`，默认 `auto`，`conversation_id` 可省略：

```powershell
$body = @{ query = 'Attention Is All You Need'; source = 'local' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/projects/1/runs' -ContentType 'application/json' -Body $body
# 用返回的 run_id 查询，worker 必须运行
Invoke-RestMethod 'http://127.0.0.1:8000/runs/1' | ConvertTo-Json -Depth 10
```

## 失败诊断与离线重放

模型结构或决策失败时，诊断材料保存在 `TUFA_DATA_DIR/diagnostics/`，普通日志中的诊断 ID 可用于定位。材料可能包含研究输入和模型原始响应，请按用户数据保管；报告核验的 unsupported 诊断只保存段落坐标和原因，已知密钥和凭据字段会被剔除或脱敏。仓库根目录执行 `.venv/Scripts/python.exe -B -m scripts.replay_diagnostic <诊断文件路径>` 可在当前代码下离线检查，不联网或访问数据库。重放使用当前结构而非历史代码，环境差异和脱敏可能影响结果；网络或 HTTP 故障不能离线复现。

## 验证


### 备用搜索与研究小结验收（2026-11-09）

以 `.cache/research-notes-tests` 为 `TEMP`、`TMP`，其 `data` 为 `TUFA_DATA_DIR`，后端回归 168 项通过，耗时 11.343 秒，日志为该目录的 `unittest.log`。隔离运行实际 API/worker，模拟搜索、网页读取和模型的 `verify_web_search` 与 `verify_chat` 均通过，日志位于 `.cache/research-notes-acceptance/web/verify.log` 和 `chat/verify.log`；持久化测试覆盖运行中、完成后及重试。`pnpm check:web` 和 `pnpm build:web` 通过。隔离 IAB 提交自动范围的 `typed-rate-limit` 后，展开研究过程并验证了初步发现与引用链接。

公开组件试验使用与本机 DeerFlow 一致的 DDGS 9.9.1 和 primp 0.15.0：DuckDuckGo HTML 返回 HTTP 202；Bing 和 Yahoo 对两个样例查询分别各返回 3 条。未调用真实模型，也未重放用户原问题；结果不保证后续供应商可用。


### 搜索服务受阻处理验收（2026-11-08）

以 `.cache/web-provider-recovery-tests` 为 `TEMP`、`TMP`，其 `data` 为 `TUFA_DATA_DIR`，运行 `.venv/Scripts/python.exe -B -m unittest discover -s tests -v`：160 项通过，耗时 11.933 秒，日志为该目录的 `unittest.log`。

在隔离目录 `.cache/web-provider-recovery-acceptance` 使用模拟模型、搜索和网页读取运行实际 API/worker：`-m scripts.verify_chat` 和 `-m scripts.verify_web_search` 均以退出码 0 完成，日志分别为 `chat/verify.log` 和 `web/verify.log`。验收包含限流后停止重复搜索、阶段性结果，以及显式网页搜索故障重试。未在此模拟验收中调用真实模型或搜索供应商。


### 网页搜索受阻公开组件观察（2026-11-08）

独立公开查询中，DuckDuckGo 仍返回 HTTP 202 验证挑战；同次通过项目 `fetch_page` 直接读取 EAC 的公开页面，得到 2699 个 Markdown 字符。这只验证搜索与网页读取组件，未重新运行真实模型任务，也不代表自动切换了搜索供应商。


### 研究充分性与阶段结果验收（2026-11-08）

以 `.cache/research-completion-tests` 为 `TEMP`、`TMP`，其 `data` 为 `TUFA_DATA_DIR`，运行 `.venv/Scripts/python.exe -B -m unittest discover -s tests -v`：153 项通过，耗时 13.276 秒，日志为该目录的 `unittest.log`。

隔离目录 `.cache/research-completion-acceptance` 下，实际 API/worker 使用模拟模型、搜索和网页读取运行 `-m scripts.verify_chat`、`-m scripts.verify_web_search`，两者均以退出码 0 完成。日志分别位于该目录的 `chat/verify.log` 和 `web/verify.log`。验收覆盖审查后跨来源补研、审查不足时的阶段性结果、报告核验与导出；未调用真实模型或搜索供应商。


### 报告形状兼容与降级验收（2026-11-08）

以 `.cache/report-shape-tests` 为 `TEMP`、`TMP`，其 `data` 为 `TUFA_DATA_DIR`，执行 `.venv/Scripts/python.exe -B -m unittest discover -s tests -v`：140 项通过，耗时 15.912 秒，日志为该目录的 `unittest.log`。在 `.cache/report-shape-acceptance` 同样隔离运行 `-m scripts.verify_chat` 和 `-m scripts.verify_web_search`，均以退出码 0 完成；实际 API/worker 配合模拟服务验证顶层章节归位、空引用局限剔除，以及两次无效 Writer 输出后保留资料清单和原 PDF 页码且不再调用其他模型节点。

两份真实任务 #14 的诊断离线只读重放通过：第一份的两条无引用局限被核验规则拒绝，第二份错放的章节按原值归位。本轮未重新调用真实模型或搜索服务。


### 报告输入字段回显修复验收（2026-11-08）

以 `.cache/report-echo-tests` 为 `TEMP`、`TMP`，其 `data` 为 `TUFA_DATA_DIR`，执行 `.venv/Scripts/python.exe -B -m unittest discover -s tests -v`：137 项通过，耗时 14.653 秒，日志为该目录的 `unittest.log`。在独立 `.cache/report-echo-acceptance` 数据目录下运行 `-m scripts.verify_chat` 和 `-m scripts.verify_web_search`，均以退出码 0 完成；实际 API/worker 使用模拟模型和搜索服务。

两份任务 #13 失败诊断经 `scripts.replay_diagnostic.replay` 和 Draft 离线只读重放通过；比较确认只移除 `verification_gaps`，其他字段值完全相同。本轮未重新调用真实模型或搜索服务，也未修改默认数据。


### 网页搜索失败恢复验收（2026-11-08）

以 `.cache/web-search-recovery-tests` 为 `TEMP`、`TMP`，其 `data` 为 `TUFA_DATA_DIR`，执行 `.venv/Scripts/python.exe -B -m unittest discover -s tests -v`：136 项通过，耗时 9.581 秒，日志位于该目录的 `unittest.log`。以 `.cache/web-search-recovery-acceptance` 同样隔离运行 `-m scripts.verify_web_search` 和 `-m scripts.verify_chat`，均以退出码 0 完成；模拟搜索和模型、实际 API/worker 验证五次空结果后 503 的自动审查及证据不足，以及显式网页失败和重试。

相同公开查询复测记录 `engine=duckduckgo outcome=failed category=challenge status=202`。当前 DuckDuckGo 仍要求验证，系统不会绕过验证或自动更换供应商；该独立观察不能推断任务 #12 当时各次请求的上游响应。

### DuckDuckGo 独立观察（2026-11-08）

公开查询 `Python official documentation` 的 HTML 请求返回 HTTP 202 及 anomaly-modal 验证标记，DDGS 将该响应报告为无结果。此次独立观察不能说明任务 #12 各次请求的上游响应；适配后的同查询复测结果见上方网页搜索失败恢复验收。


### 网页正文读取与报告核验验收（2026-11-08）

以 `.cache/web-reader-tests` 为 `TEMP`、`TMP`，其 `data` 为 `TUFA_DATA_DIR`：最终 `.venv/Scripts/python.exe -B -m unittest discover -s tests -v` 共 131 项通过，耗时 9.151 秒，日志为该目录下 `unittest.log`。在独立 `.cache/web-reader-acceptance` 中，`-m scripts.verify_chat` 和 `-m scripts.verify_web_search` 均以退出码 0 完成；使用模拟搜索、HTML transport 和模型，实际 API/worker 验证解析、取证、保存、CSV 字符位置与带引用的局限。该模拟验收发生在最终 TLS 信任配置调整前，fixture 不依赖 TLS。

独立真实 HTTPS 验收 `https://example.com/`：`limit=64` 返回 64 字符和 `next_offset=64`；从 64 起用默认长度返回 92 字符且无下一页。首次使用标准库系统信任根时出现证书验证 code 20，改为已有 HTTPX 的 certifi 信任根并保留证书验证和域名 SNI 后复测成功。未验收其他真实供应商或前端。


### 研究报告与跨来源补研验收（2026-11-08）

以 `.cache/report-research-acceptance` 为 `TEMP`、`TMP`，其 `data` 为 `TUFA_DATA_DIR`，在仓库根目录执行：

- `.venv/Scripts/python.exe -B -m unittest discover -s tests -v`：115 项通过，耗时 8.754 秒。`-m scripts.verify_chat` 成功；`-m scripts.verify_web_search --serve` 的实际 API/worker 业务断言通过，使用模拟 OpenAlex 摘要、审查缺口和 SearX 方法细节，同一报告的三章节、Markdown 和 CSV 均保留两类证据。验收后通过 Ctrl+C 停止 `--serve` 和 Next，二者因中断退出码均为 1；18188、18308 端口已释放，临时数据库目录已清理。
- `pnpm check:web` 通过。`pnpm build:web` 首次在沙箱内因生成目录的 EPERM 失败，沙箱外重跑通过；构建时 `TUFA_API_URL=http://127.0.0.1:18188`，Next 生产服务使用 18308 端口。
- 隔离浏览器选择“优先 OpenAlex”，提交 `cross-source attention method`，显示概述、主题分析、综合判断及两类引用链接和下载入口。模型与搜索服务均为模拟，未调用真实供应商。


### 自动路由验收（2026-11-07）

以 `.cache/auto-route-acceptance` 为 `TEMP`、`TMP`，其 `data` 为 `TUFA_DATA_DIR`，在仓库根目录执行：

- `.venv/Scripts/python.exe -B -m unittest discover -s tests -v`：107 项通过，耗时 9.409 秒。`scripts.verify_chat` 和 `scripts.verify_web_search --serve` 的本机模拟服务与实际 API/worker 断言通过；网页服务本次用于浏览器验收，之后以 Ctrl+C 停止，`--serve` 因中断退出码为 1。Next 也已停止，18189、18309 端口已释放，临时数据库目录已清理。
- `pnpm check:web`、`pnpm build:web` 通过；构建前设置 `TUFA_API_URL=http://127.0.0.1:18189`，Next 生产服务使用 18309 端口。
- 隔离浏览器中默认选择“自动”，提交“汇报一下最近七天国际大事”，任务 #5 成功，页面显示 SearX 摘要引用，代理 API 回读确认 `source=auto`。模拟模型按问题选择网页；此项验证链路和范围，未调用真实模型。


### 模型失败诊断与收束修复验收（2026-11-07）

在仓库根目录，以 `.cache/agent-failure-acceptance` 为 `TEMP`、`TMP`，其 `data` 子目录为 `TUFA_DATA_DIR`，使用隔离数据执行：

- `.venv/Scripts/python.exe -B -m unittest discover -s tests -v`：103 项通过，耗时 8.670 秒，日志位于 `.cache/agent-failure-acceptance/unittest.log`。
- 新增模型调用到诊断材料再到离线重放的集成回归后，`tests.test_model` 共 8 项通过；上述全量 103 项在新增该测试前运行。
- `.venv/Scripts/python.exe -B -m scripts.verify_chat` 和 `.venv/Scripts/python.exe -B -m scripts.verify_web_search` 使用本机模拟服务及实际 API、worker，均以退出码 0 完成并正常清理。
- 用户此前的真实失败可定位到主控非法动作和研究节点结构校验，但旧日志未保存原始响应，无法离线重放或确认后者具体字段。本轮未重试真实供应商；`all` 仍仅包括本地文献与 OpenAlex。


### Supervisor 重构验收（2026-11-07）

在仓库根目录，将 `TEMP`、`TMP` 指向 `.cache/supervisor-acceptance`，`TUFA_DATA_DIR` 指向其 `data` 子目录，使用隔离数据执行：

- `.venv/Scripts/python.exe -B -m unittest discover -s tests -v`：最终 93 项通过，耗时 6.763 秒，日志为 `.cache/supervisor-acceptance/unittest.log`。首次运行 87 项时因 `test_question_models` 仍导入旧 `Answer` 而失败，修复后重跑通过。
- 在沙箱外隔离运行 `.venv/Scripts/python.exe -B -m scripts.verify_chat` 和 `.venv/Scripts/python.exe -B -m scripts.verify_web_search`，均以退出码 0 完成并成功收尾。前者验证收藏快照、模型隔离、引用、重试和导出；后者使用模拟 Searx 与模型及实际 API/worker，验证来源引用、CSV、空结果与错误、论文库隔离和重试。
- 本轮未调用真实外部服务，未运行前端构建或浏览器验收，未修改默认 `data/`。

### 跨轮证据、PDF 分页与轮询重试验收（2026-11-07）

在仓库根目录使用现有依赖和 E 盘隔离目录执行：

```powershell
$acceptanceTemp = (New-Item -ItemType Directory -Force .cache/evidence-polling-acceptance).FullName
$env:TEMP = $acceptanceTemp
$env:TMP = $acceptanceTemp
$env:TUFA_DATA_DIR = Join-Path $acceptanceTemp 'data'
$env:PYTHONDONTWRITEBYTECODE = '1'
& .venv/Scripts/python.exe -B -m unittest discover -s tests -v
$env:NEXT_TELEMETRY_DISABLED = '1'
$env:NODE_OPTIONS = '--max-old-space-size=1536'
pnpm check:web
pnpm build:web
pnpm --dir apps/web exec tsc app/hooks/runPolling.ts --target es2020 --module commonjs --outDir ../../.cache/polling-test --skipLibCheck
node apps/web/app/hooks/runPolling.test.cjs (Resolve-Path .cache/polling-test/runPolling.js)
```

- 后端 89 项通过。使用隔离 SQLite 和模拟外部服务，验证旧表重复升级、分页补建、上传实际 PDF、取证读取第 26 页、长页分片和原文下载。旧论文退出精读集合后，其片段与新论文证据仍可一起引用并通过模拟核验。工作证据的条数和文本上限通过回归验证。
- 前端类型检查、生产构建和 5 项轮询调度测试通过，覆盖瞬时失败后恢复、计数清零、连续三次失败后停止及取消。在前端目录用 `node --max-old-space-size=1024 node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port 18301` 检查生产首页，返回 HTTP 200。验收后关闭进程，端口已释放。
- 沙箱内 TestClient 和回环 HTTP 检查未完成，最终在沙箱外对隔离数据通过，已停止此前卡住的测试进程。真实模型、真实外部搜索及浏览器断网交互未实测。

### 补检子问题取证验收（2026-11-07）

在仓库根目录执行：

```powershell
$testTemp = (New-Item -ItemType Directory -Force .cache/supplement-evidence-tests).FullName
$env:TEMP = $testTemp
$env:TMP = $testTemp
$env:TUFA_DATA_DIR = Join-Path $testTemp 'data'
& .venv/Scripts/python.exe -B -m unittest -v tests.test_workflow tests.test_collection_workflow tests.test_web_workflow tests.test_tools
```

四组共 32 项通过。新增用例从 `run()` 执行 LangGraph，模拟模型、检索结果和 PDF 页文本，使用实际工具入口和取证评分。首轮选择第 1 页优先，两轮补检分别把第 4、5 页选入证据并排在首位。缺口评估和综合都收到原始研究问题。收藏夹隔离用例在沙箱内停滞，停止后在沙箱外使用隔离数据通过。验收使用模拟外部服务，未启动应用服务。

### 前端工作台分责验收（2026-11-06）

- `pnpm check:web`、`pnpm build:web`：最终版本通过。构建前设置 `TUFA_API_URL=http://127.0.0.1:18180`，使用 E 盘 `.cache/frontend-refactor-tmp` 作为 `TEMP`/`TMP`，设置 `NEXT_TELEMETRY_DISABLED=1` 与 `NODE_OPTIONS=--max-old-space-size=1536`。
- `pnpm --dir apps/web exec tsc app/hooks/runPolling.ts --target es2020 --module commonjs --outDir ../../.cache/polling-test --skipLibCheck`，随后 `node apps/web/app/hooks/runPolling.test.cjs (Resolve-Path .cache/polling-test/runPolling.js)`：2 项通过，覆盖无重叠、终态停止、取消、中止与旧响应忽略、错误后停止。
- `.venv/Scripts/python.exe -B -m scripts.fixture_api --data-dir E:\Tufa-Scholar\.cache\frontend-refactor-acceptance-20261106 --port 18180` 配合前端目录下 `node --max-old-space-size=1024 node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port 18300`：实际浏览器通过项目创建/切换、删除确认取消、收藏确认/取消/移除、创建空夹及问答禁用、草稿保留、论文搜索空态/失败恢复/收藏、网页检索、历史跳转、PDF 上传与列表刷新。
- 模拟任务通过 `POST /__fixture/advance` 显式推进；浏览器验证 pending、failed、重试 succeeded 与回答/引用刷新。HTTP 回读确认选择的模型和 medium 强度保存，证据 CSV 返回 200；任务在旧项目完成时未污染新项目。最终构建复查同一会话历史跳转保持四条消息，以及移除后的成功提示；浏览器错误日志为空。
- 预览位于 `.cache/frontend-refactor-tmp/workspace.jpg`。本轮 API、前端均已停止，18180/18300 端口无监听；未启动 worker、未修改默认用户数据、未调用真实模型或搜索服务。仅使用模拟目录和任务结果，不推定真实供应商质量或完整后端工作流验证通过；未重跑后端全量回归或根启动器验收。

### 候选池与 Agent 解耦验收（2026-11-06）

在仓库根目录使用隔离临时目录执行：

```powershell
New-Item -ItemType Directory -Force .cache/agent-refinement-unit-tmp | Out-Null
$env:TEMP = (Resolve-Path .cache/agent-refinement-unit-tmp).Path
$env:TMP = $env:TEMP
uv run --locked python -m unittest discover -s tests -v
New-Item -ItemType Directory -Force .cache/agent-refinement-tmp | Out-Null
$env:TEMP = (Resolve-Path .cache/agent-refinement-tmp).Path
$env:TMP = $env:TEMP
uv run --locked python -m scripts.verify_chat
uv run --locked python -m scripts.verify_web_search
git diff --check
```

- 最终后端回归 81 项通过。覆盖首轮六篇满额后补检新论文进入精读与回答、七条候选保留与 DOI 去重、重复查询不加票、八篇收藏夹候选保留、相同证据条数下继续补检、真正重复停止及临时摘要编号不影响新增判断。
- Agent 在无任务 ID、禁止任务历史查询和阶段写库的条件下，使用显式历史与可选回调运行；隔离 SQLite 验证 worker 读取历史、收藏夹不读取历史、阶段回调实际落库与成功提交。
- `verify_chat` 使用临时 SQLite、本地模拟模型及实际 API/worker，通过原文引用、收藏夹快照隔离、报告导出、服务失败和重试闭环，脚本退出成功。
- `verify_web_search` 使用临时 SQLite、本地模拟搜索/模型及实际 API/worker，网页引用、CSV 导出、论文库隔离、服务失败和重试的业务断言通过；首次收尾时 Windows `taskkill /T` 超时，进程未退出导致临时日志占用，脚本退出失败。已核实并停止该次残留进程；重跑的业务断言再次通过，但在 C 盘空间告急后由用户要求中断，未确认重跑的进程收尾。该脚本不能记为完整验收通过。
- 已逐份审查 16 份 `AGENTS.md`，将当前分支及协作进度移入 [开发进度](docs/DEVELOPMENT.md)。30 份文档的本地链接与 `git diff --check` 通过。
- 未修改默认用户数据，未调用真实云端模型或搜索供应商；本轮未重新运行前端构建、浏览器交互或根启动器完整验收。排名融合是检索启发式，真实语义相关性未实测。
- 中断后恢复时仅复核已有测试日志、暂存差异和文档链接，没有重新运行测试或启动服务。

### Python 目录分责与提示词迁移验收（2026-11-06）

在仓库根目录执行，验收使用独立临时数据库与本地模拟服务：

```powershell
New-Item -ItemType Directory -Force .cache/architecture-layout-tmp | Out-Null
$env:TEMP = (Resolve-Path .cache/architecture-layout-tmp).Path
$env:TMP = $env:TEMP
uv run --locked python -m unittest discover -s tests -v
uv run --locked python -m scripts.verify_chat
uv run --locked python -m scripts.verify_web_search
git diff --check
```

- 后端回归 75 项通过。新增路径回归确认从仓库根目录以外调用 `structured()` 仍能加载模板，默认数据目录仍是仓库根目录 `data/`，路径测试不写入该目录。
- 实际 API/worker 的 `verify_chat` 模拟闭环通过：PDF 页码与版本引用、证据不足、报告导出、失败重试和收藏夹快照隔离。
- 实际 API/worker 的 `verify_web_search` 模拟闭环通过：网页检索、空结果与失败、引用与 CSV 导出、论文库隔离和重试。
- 五份提示词正文与移动前一致，prompt ID 未变；新旧模块引用与 29 份文档本地链接已检查，`git diff --check` 通过。
- 未修改默认用户数据，未调用真实搜索供应商或云端模型；本轮未重新运行浏览器交互、前端构建或根启动器的完整进程管理验收。

### 工具目录与调用入口重构验收（2026-11-06）

在仓库根目录执行，临时目录固定到被忽略的 `.cache/architecture-refactor-tmp`：

```powershell
New-Item -ItemType Directory -Force .cache/architecture-refactor-tmp | Out-Null
$env:TEMP = (Resolve-Path .cache/architecture-refactor-tmp).Path
$env:TMP = $env:TEMP
uv run --locked python -m unittest discover -s tests -v
uv run --locked python -m scripts.verify_chat
uv run --locked python -m scripts.verify_web_search
git diff --check
```

- 后端回归 73 项通过，含 9 项工具专项测试：按上下文发现、参数校验、独立调用、显式注册扩展、冻结快照、收藏夹交集、本地证据真实性及网页证据去重。
- `verify_chat` 使用临时 SQLite、本地模拟模型及实际 API/worker，通过原文页码与版本引用、数值拒绝、报告导出、失败重试及收藏夹快照隔离。
- `verify_web_search` 使用临时 SQLite、本地模拟 Searx/模型及实际 API/worker，通过搜索、空结果、服务失败、网页引用与 CSV 导出、论文库隔离及重试。
- 文档本地链接与 `git diff --check` 通过。首轮沙箱执行遇到服务启动与临时目录访问限制，以上结果来自允许本机进程和网络访问后的隔离重跑。
- 未修改默认用户数据，未调用真实搜索供应商或云端模型，未重新运行浏览器交互或前端构建。工具独立调用已实现，科研问答仍使用现有 LangGraph 编排。

### 网页搜索验收（2026-11-06）

- `uv run --locked python -m unittest discover -s tests -v`：63 项通过，涵盖八种搜索协议、配置脱敏、证据层级、范围隔离和失败处理；Wikipedia 多页正文参数补正后对应测试再次通过。
- `pnpm check:web`、`pnpm build:web`：通过。
- `uv run --locked python -m scripts.verify_web_search`：临时 SQLite、本地模拟 Searx/模型 HTTP 与实际 API/worker 通过搜索、空结果、错误响应、网页问答、证据导出、全失败与重试验收。
- 同一脚本加 `--serve`，配合 `TUFA_VERIFY_API_PORT=18180`、前端 `TUFA_API_URL=http://127.0.0.1:18180` 和 18300 端口的 Next dev：浏览器验证网页搜索结果与失败提示、八种引擎选项、切换清除草稿密钥、保存不回显、清除密钥及网页问答完成后的引用。首页、设置页及代理请求正常。
- 未调用真实搜索供应商或云端模型；未修改默认用户数据。验收服务已停止。供应商实际可用性和模型效果由用户后续验证。

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
