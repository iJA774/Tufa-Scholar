# 本机 HTTP API

按 [README](../README.md#配置与启动) 启动后，访问 [FastAPI 交互式文档](http://127.0.0.1:8000/docs) 查看完整路由、请求和返回结构。以下列出常用入口。

## 常用端点

| 方法与路径 | 功能 |
|---|---|
| `GET /health` | 健康与 SQLite/FTS5 检查 |
| `GET/POST /projects` | 列出、创建项目 |
| `GET/PUT /settings` | 读取配置状态或保存本机配置，不返回密钥原文 |
| `POST /settings/models`、`GET /models` | 发现或列出模型 |
| `GET/POST /projects/{project_id}/conversations` | 列出、创建对话 |
| `GET /conversations` | 跨项目平铺列出历史会话，置顶优先，其余按更新时间倒序，返回 `project_id` 与 `pinned` |
| `PATCH /conversations/{conversation_id}` | 设置 `pinned` 布尔值，返回更新后的会话 |
| `DELETE /conversations/{conversation_id}` | 删除该会话、消息、报告及其任务。若有待处理或正在运行的任务则返回 409 |
| `GET/POST /projects/{project_id}/papers` | 列出、保存论文元数据 |
| `POST /projects/{project_id}/papers/import` | 上传 PDF；大小、页数和解析资源限制见 [文档指南](DOCUMENTS.md)，解析繁忙返回 503 |
| `POST /conversion-batches/open-originals` | 在资源管理器打开本机原文目录 |
| `POST /conversion-batches/prepare` | 转换和分类，并为传入的 `project_id` 建立本地 Markdown 检索索引 |
| `POST /conversion-batches/clear` | 发送 JSON `{"confirm": true}` 清除已保存的预处理产物和记录 |
| `POST /conversion-batches/from-originals` | 从固定原文目录创建并启动转换批次，传入当前 `project_id` 时导入转换成功的 PDF |
| `POST /conversion-batches/preprocess` | 发送 JSON `{}` 启动已转换 Markdown 的分析与分类批次 |
| `POST /conversion-batches` | 创建转换清单（相对路径、大小、可选项目和 TXT 编码） |
| `POST /conversion-batches/{id}/files/{file_id}` | 单文件 multipart 上传，由服务端生成物理文件名 |
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

## 提交研究问题

在 PowerShell 执行以下示例，先将 URL 中的 1 替换为已有项目 ID。`source` 可为 `auto`、`local`、`openalex`、`all`、`web` 或 `collection`。`research_mode` 可为 `auto`、`deep` 或 `quick`。

```powershell
$body = @{ question = 'Attention Is All You Need'; source = 'local'; research_mode = 'deep' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/projects/1/questions' -ContentType 'application/json' -Body $body
```

可选任务参数包括 `research_depth`（`light|standard|deep`，默认 `standard`）、`prefer_primary_evidence`（JSON 布尔值，默认 `true`）和 `relevance_filter`（`relaxed|standard|strict`，默认 `standard`）。`network_search` 和 `local_literature` 均为 JSON 布尔值，默认 `true`。各档位预算见[架构文档](ARCHITECTURE.md#工作流预算)。
