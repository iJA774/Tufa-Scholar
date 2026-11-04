# Tufa Scholar

本地单用户科研助手。当前提供项目与对话、论文检索和导入、文献列表及单 worker 任务。架构与分阶段目标见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

本地项目文献通过 SQLite FTS5/BM25 关键词检索，外部结果由 OpenAlex 文本检索提供；选择两者时用 RRF 合并排名并保留来源排名。当前没有向量检索或模型重排，也没有综述、修订、OCR 和引用扩展。上传 PDF 时提取可复制文本；扫描件无可提取文本时保留原文件并标记状态。检索只返回元数据与链接，不自动下载 PDF。

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

OpenAlex 支持无密钥试用，可在 worker 进程环境中设置 `OPENALEX_API_KEY` 使用自己的额度；密钥仅从环境变量读取。额度与限制见 [OpenAlex 认证说明](https://help.openalex.org/api/authentication/)。选择 `all` 时如果 OpenAlex 请求失败，整个任务会失败；可改用 `local` 独立搜索项目文献。

推荐在项目根目录的 PowerShell 中一条命令启动全部服务：

```powershell
.\start.ps1
```

脚本启动 API、单 worker 和前端，前台窗口按 `Ctrl+C` 停止；Windows Job Object 管理本次启动的进程树，停止时只清理这些进程；各服务的标准输出和错误日志保存在启动时显示的 `.cache/start/` 子目录。启动前需完成上面的依赖安装，且 3000、8000 端口必须空闲；脚本不会停止已有服务。脚本固定将前端 `TUFA_API_URL` 设为本次启动的 `http://127.0.0.1:8000`，并向所有子进程继承当前的 `TUFA_DATA_DIR` 和 `OPENALEX_API_KEY`。若执行策略阻止脚本，可单次使用 `powershell -ExecutionPolicy Bypass -File .\start.ps1`，无需修改持久策略。

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

worker 按顺序领取任务；重启时将遗留的运行中任务标记为中断，可在界面或 API 重新执行。`uv run --locked python -m apps.worker --once` 最多处理一个待处理任务并退出，`--check` 只检查存储并退出。不要同时运行多个 worker。

## 使用与接口

在首页创建项目与对话，输入检索问题并选择本地、OpenAlex 或全部来源；任务完成后可将结果保存到项目文献。也可以上传 PDF；重复字节的文件按内容摘要去重，DOI 或 arXiv ID 用于识别文献。项目内文献跨对话共享。

API 具体字段和交互可在 `/docs` 查看。主要接口：

| 方法与路径 | 功能 |
|---|---|
| `GET /health` | 健康与 SQLite/FTS5 检查 |
| `GET/POST /projects` | 列出、创建项目 |
| `GET/POST /projects/{project_id}/conversations` | 列出、创建对话 |
| `GET/POST /projects/{project_id}/papers` | 列出文献、保存元数据 |
| `POST /projects/{project_id}/papers/import` | multipart 上传 PDF，可附 `title`、`doi`、`arxiv_id` |
| `GET /paper-versions/{version_id}/file` | 查看已上传原文 |
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

### 本机实测（2026-11-04）

| 命令或入口 | 结果 |
|---|---|
| `uv run --locked python -m unittest tests.test_retrieval tests.test_worker -v` | 5 项通过：假上游 fixture 映射、凭据放在请求头、去重与排名、实际 worker 入口本地固定问题排名、重启中断处理 |
| `uv run --locked python -m unittest discover -s tests -v` | 6 项通过 |
| `pnpm check:web`、`pnpm build:web` | 前端类型检查和生产构建通过 |
| API 与单 worker 共用 `.cache/acceptance-retrieval-20261104`，设置 `TUFA_API_URL=http://127.0.0.1:8000` 后执行 `uv run --locked python scripts/verify_retrieval.py` | 实际 HTTP 与 worker 验收通过：PDF 正文词检索、无可提取文本标记、重复导入、新版本、跨项目和会话、404/422；`/health` 返回 WAL、外键和 FTS5 正常 |
| 从 `E:\` 使用绝对路径运行 `start.ps1`，数据目录设为 `.cache/acceptance-launcher-20261104` | 首页、健康检查与 `/api/projects` 均 HTTP 200，`scripts/verify_retrieval.py` 通过；重复启动因端口占用退出 1 且原服务保持运行；Ctrl+C 后 3000、8000 端口释放，验收服务已停止 |
| PowerShell 7.6.9 实际运行 `start.ps1`，数据目录 `.cache/acceptance-shutdown-ps51` | API、前端和代理均 HTTP 200，`scripts/verify_retrieval.py` 通过；连续两次 Ctrl+C 后端口与 worker 无残留；运行时再次启动被拒绝且原服务保持运行 |
| Windows PowerShell 5.1.26100.9711 交互会话运行 `start.ps1`，数据目录 `.cache/acceptance-shutdown-real-ps51` | 同一宿主连续两次 Ctrl+C 返回提示符后立即重启成功；重启后 `scripts/verify_retrieval.py` 通过，主动结束本次 worker helper 后启动器检测失败并清理所有服务，返回同一提示符 |
| 前端生产服务 `next start` 监听 3107 端口 | 首页和 `/api/projects` 均 HTTP 200；浏览器提交 OpenAlex 检索，原论文排第 1，保存后刷新仍能看到文献及任务 |
| `uv run python -c "from src.integrations.openalex import search_works; print([(x['title'], x['doi']) for x in search_works('Attention Is All You Need', 5)])"` | 真实 OpenAlex 无密钥请求成功；《Attention Is All You Need》（DOI `10.48550/arxiv.1706.03762`）第 1，第 2 为《Attention Is All You Need In Speech Separation》，第 3 为《Channel Attention Is All You Need for Video Frame Interpolation》 |

以上 OpenAlex 排名是该次请求的观察结果，服务数据和排名可能变化。OpenAlex 检索参数和默认相关性排序见[官方搜索文档](https://help.openalex.org/api/searching/)。

验收使用独立目录，未向默认 `data/` 写入测试文献；验收服务已停止，测试数据和截图保留在 `.cache/acceptance-retrieval-20261104/`。未验收开发模式热更新；向量检索、模型重排、OCR、综述和修订尚未实现或验收。
