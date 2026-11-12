# 测试约定

## 隔离与断言

- 使用临时 SQLite 或明确隔离的 `TUFA_DATA_DIR`，不能向默认用户数据写测试项目、文献或设置。配置测试隔离环境变量并恢复现场。
- 外部服务默认用 fixture、mock 或本地模拟服务；普通回归不得依赖真实密钥、网络配额或不稳定排名。
- 断言数据、状态、引用范围、请求协议和错误脱敏等可观察行为；避免固定摘要、整页文本快照和仅镜像实现的测试。
- 科研图回归按八节点类型和两轮调度契约验证：直接回答不调用工具、背景检索与筛选、监督顺序、planner 复审、来源切换、强制终稿、累计证据/候选/引用边界、collection/history 隔离、模型选项及阶段/小结持久化。节点模型输出 fixture 应使用当前 prompt schema，不保留已移除工作流的 schema。
- 子进程、模拟 HTTP 服务与日志句柄在结束或异常时清理；只停止测试自己启动的进程。
- `fixtures/` 保存可公开的固定样例，不复制用户文献、凭据或数据库。`model_discovery_server.py` 仅用于隔离验收。

## 验证入口

命令均在仓库根目录执行。按受影响行为选择测试；跨层改动或整体回归运行 `uv run --locked python -m unittest discover -s tests -v`。

| 改动范围 | 主要测试文件或入口 |
|---|---|
| HTTP、项目与文献 | `test_api.py`、`test_chat_api.py`、`test_collections_api.py` |
| 检索、科研与证据范围 | `test_retrieval.py`、`test_workflow.py`、`test_collection_workflow.py`、`test_report.py`、`test_pdf_evidence.py` |
| 网页科研流程与读取边界 | `test_web_workflow.py`、`test_web_search.py`、`test_web_page.py`、`test_tools.py` |
| 工具发现、参数与独立调用 | `test_tools.py`，以及 API、worker、workflow 的调用入口回归 |
| 模块移动后的路径语义 | `test_layout.py`、`test_model.py`，以及实际 worker 与模拟 HTTP 闭环 |
| 设置、模型、prompt 与日志 | `test_settings.py`、`test_model.py`、`test_model_discovery.py`、`test_question_models.py`、`test_diagnostics.py`、`test_logging.py` |
| Worker 生命周期 | `test_worker.py`，隔离数据下的实际 `python -m apps.worker` 入口 |
| Windows 进程树 | `test_launcher_job.ps1`、`launcher_wrapper.ps1`；先读参数与 [脚本规则](../scripts/AGENTS.md) |
| 前端 | `pnpm check:web`、`pnpm build:web`、轮询调度测试与页面实际 HTTP/交互，命令见 [前端说明](../apps/web/README.md) |

跨进程科研闭环使用 `scripts/verify_chat.py` 和 `scripts/verify_web_search.py`，必须设置独立临时 `TUFA_DATA_DIR`，通过实际 `apps.api.main` 与 `apps.worker` 运行，并在成功或异常后关闭子进程、HTTP 模拟服务和临时目录。`scripts.fixture_api.py` 仅用于前端 UI fixture，不作为科研工作流验收。结果写入 README 验收部分，区分单测、模拟 HTTP、浏览器与真实云端验证。
