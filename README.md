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

模型可在设置页配置 OpenAI 兼容 Base URL、密钥和默认模型名。也可在运行 API/worker 的环境设置 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`，这些环境变量优先。本机设置保存在 `TUFA_DATA_DIR/research.db`，密钥以明文保存在该本机数据库，接口只返回配置状态和来源。提问模型可手动填写，任务和重试保留提交的模型。工作流按研究深度及节点选择默认思考强度。深度研究需要服务支持结构化输出和原生 Function Calling。

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

在提问框的模式菜单中选择“自动”“深度研究”或“快速探索”。默认使用自动档，由模型选择直接回答或进入研究流程。深度研究无条件进入研究流程，快速探索直接回答并跳过检索工具。设置中的“通用”可选择研究模式及发送快捷键，也可手动填写提问模型 ID。通用页底部的“重置所有设置”会恢复默认模式、发送快捷键、研究偏好和外观。API 密钥、服务地址、搜索配置和提问模型沿用当前值。

在“研究偏好”中可选择轻量、标准或深入的研究档位，以及是否优先使用原始证据和相关性过滤强度。研究档位决定预算、自动思考强度和提前结束条件。手动选择思考强度时优先使用该值。偏好随新提问提交，重试沿用原任务的模式和偏好。报告格式目前只保存在前端，终稿仍按现有工作流生成。

提问框内的“网络搜索”和“本地文献”默认点亮。关闭“网络搜索”会停用网页和 OpenAlex 检索及远程原文读取，模型仍可回答；关闭“本地文献”会停用项目文献库检索。开关状态随每次提问保存，重试和重新回答沿用原任务状态。

从侧边历史打开会话后，可点击原问题下方的“重新回答”，无需重新输入。已完成、报告降级、失败或中断的问题均可重新回答。新任务使用当前的提问模型或服务默认模型，保留原问题、来源范围、收藏夹成员快照和研究偏好；旧回答与报告继续保留。同一会话有待处理或运行中的任务时，需等待其结束。原有“重新执行”仍沿用失败任务保存的模型配置。

深度研究先搜索和筛选背景资料，再规划并收集证据，最后生成报告。右侧“活动”可查看计划、审查、搜索结果与工具操作。支持流式响应的模型会边生成边显示 Markdown 草稿。任务失败或终稿降级时，工作区顶部会显示可关闭的原因和处理建议。降级后保存最近一份通过引用检查的研究草稿或当前证据缺口。快速探索的回答预览显示在聊天区。

点击“设置”会在当前工作区上方打开设置卡片。在“服务与模型”和“网页搜索”中保存配置。“本地文档”可打开原文目录并批量转换 Markdown。“关于”在新标签页打开项目的 GitHub 仓库。

### 本地文档转换 Markdown

在“设置 → 本地文档”点击“打开目录”，Windows 资源管理器会打开 `TUFA_DATA_DIR/documents/originals/`，默认是项目下的 `data/documents/originals/`。将待转换文件放入该目录，再点击“一键转换 Markdown”。转换会扫描子目录，在本机完成，无需调用模型或云端 OCR。

自动识别并转换 PDF、DOCX 和 TXT。PDF 尝试保留字号标题、双栏阅读顺序和有边框表格，逐页写入 `<!-- page:N -->`；DOCX 保留标题、段落、列表、表格、粗斜体及安全链接，图片用位置标记代替。TXT 自动识别 UTF-8/BOM、UTF-16 和部分常见编码，检测失败时可先将原文件另存为 UTF-8。旧版 DOC 请先另存为 DOCX；扫描 PDF 需要先提取文本，加密 PDF 需要先解密。复杂版式、合并表格、公式和浮动对象仍可能不完整，请核对原文。

进度和单文件状态来自服务端，可取消批次、逐个预览与下载，结束后下载保留相对目录结构的 ZIP。同名不同格式采用包含原扩展名的输出名，特殊冲突加稳定哈希；不会覆盖原文件。ZIP 附带转换清单和页码/字符偏移元数据（偏移按下载 Markdown 的 Unicode 字符计数）。预览最多显示 64,000 字符，完整内容通过下载获取。

转换成功的 PDF 自动加入提交时的项目文献库，通过原文哈希关联已有论文和版本。新文献按真实原页保存可检索文本，已有版本和证据 ID 保留。DOCX 和 TXT 支持转换及下载，项目的本地文献检索目前使用 PDF 文献库。

每批最多 100 文件 / 200 MiB，单文件 20 MiB、PDF 200 页、Markdown 8 MiB。批次优先处理待转换文件，超出一批时可再次点击转换。最多 4 个未结束批次、1 个 ZIP 打包，独立转换执行器一次运行一个解析子进程，每文件 45 秒、768 MiB 内存上限。关闭设置后转换继续执行，重新打开可读取最近批次。API 关闭或重启会结束遗留转换任务。

设置页生成的 Markdown 保存在 `TUFA_DATA_DIR/documents/conversions/` 下对应的相对目录中，例如 `originals/topic/paper.pdf` 对应 `conversions/topic/paper.pdf.md`。同一相对目录下已有 `paper.pdf.md` 或 `paper.md` 时按已转换跳过。需要重新转换时，可先移走已有的同名 Markdown。

新批次的下载文件和元数据保存在 `documents/conversions/.batches/` 下。临时输入副本在批次结束、取消或恢复时清理。导入文献库的 PDF 快照按内容哈希保存在 `documents/originals/.store/`。历史文献和批次仍可通过原入口读取。

已有文献和通过本机 API 导入的 PDF 按项目保存，新研究沿用当前项目。PDF 按可提取文本取证并保留版本与页码，图片型或加密 PDF 可能无法取证，重复文件按内容摘要去重。

网页搜索工具也可独立调用，结果不自动导入论文库。网页正文读取限制公网地址和重定向；摘要和网页片段不能视作完整原文。报告正文按来源首次出现的顺序显示 `[1][2]` 等编号，同一来源复用编号。标题、已有出版信息和原文定位集中列在末尾的“参考文献”。完成任务后可下载同样格式的 Markdown 报告和逐条证据的 CSV 表。工具注册、参数和来源约束见 [src/tools/README.md](src/tools/README.md)；六个模型提示词 ID 与数据契约见 [docs/PROMPTS.md](docs/PROMPTS.md)。

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
| `POST /conversion-batches/open-originals` | 在资源管理器打开本机原文目录 |
| `POST /conversion-batches/from-originals` | 从固定原文目录创建并启动转换批次，传入当前 `project_id` 时导入转换成功的 PDF |
| `POST /conversion-batches` | 创建转换清单（相对路径、大小、可选项目和 TXT 编码） |
| `POST /conversion-batches/{id}/files/{file_id}` | 单文件 multipart 上传；服务端生成物理文件名 |
| `POST /conversion-batches/{id}/start`、`POST /conversion-batches/{id}/cancel` | 显式开始或取消批次 |
| `GET /conversion-batches/{id}` | 实际单文件状态、数量、错误码和耗时 |
| `GET /conversion-batches/{id}/files/{file_id}/preview`、`.../markdown` | 有限文本预览或单个 Markdown 下载 |
| `GET /conversion-batches/{id}/zip` | 已结束批次打包下载，含定位元数据与清单 |
| `GET /projects/{project_id}/web-search?q=...` | 独立网页搜索 |
| `POST /projects/{project_id}/questions` | 提交科研问题 |
| `GET /runs/{run_id}`、`POST /runs/{run_id}/retry` | 查询或重试任务 |
| `POST /runs/{run_id}/regenerate` | 重新回答已结束的问题；可传 `model_name`，缺省使用当前服务默认模型，环境变量优先 |
| `GET /runs/{run_id}/events?after=0` | 订阅研究事件，按事件 ID 续读 |
| `GET /conversations/{conversation_id}/messages` | 查询对话消息和证据 |
| `GET /reports/{report_id}/markdown`、`GET /reports/{report_id}/evidence.csv` | 下载报告或证据表 |

提问示例，`source` 可为 `auto`、`local`、`openalex`、`all`、`web` 或 `collection`；`research_mode` 可为 `auto`、`deep` 或 `quick`：

```powershell
$body = @{ question = 'Attention Is All You Need'; source = 'local'; research_mode = 'deep' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/projects/1/questions' -ContentType 'application/json' -Body $body
```

可选任务参数包括 `research_depth`（`light|standard|deep`，默认 `standard`）、`prefer_primary_evidence`（JSON 布尔值，默认 `true`）和 `relevance_filter`（`relaxed|standard|strict`，默认 `standard`）。`network_search` 和 `local_literature` 均为 JSON 布尔值，默认 `true`。各档位预算见[架构文档](docs/ARCHITECTURE.md#工作流预算)。

## 验证

在仓库根目录按改动范围运行。

```powershell
uv run --locked python -m unittest discover -s tests -q
uv run --locked python -m scripts.verify_chat
uv run --locked python -m scripts.verify_web_search
uv run --locked python -m scripts.verify_conversion
pnpm check:web
pnpm build:web
```

这些 `verify_*` 命令使用临时 SQLite、实际本地 API/worker 和模拟上游；`verify_conversion` 检查转换、可选导入、检索与原页证据报告。仅运行转换单测可用 `uv run --locked python -m unittest tests.test_document_conversion -q`。固定合成样本由 `tests/conversion_fixtures.py` 生成，不使用用户材料。浏览器交互和真实供应商需要分别验收。浏览器验收入口见 [前端说明](apps/web/README.md)。

排查研究请求时，可在 `TUFA_DATA_DIR/logs/worker.log` 按 `run_id` 查询模型调用、工具失败和报告校验。供应商用量缺失时日志显示 `na`。结构化响应诊断位于 `TUFA_DATA_DIR/diagnostics/`，可能包含用户材料。可在仓库根目录用 `uv run --locked python -m scripts.replay_diagnostic <诊断文件路径>` 离线校验当前提示词契约。
