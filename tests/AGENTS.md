# 测试约定

## 隔离与断言

- 使用临时 SQLite 或明确隔离的 `TUFA_DATA_DIR`，不能向默认用户数据写测试项目、文献或设置。配置测试隔离环境变量并恢复现场。
- 外部服务默认用 fixture、mock 或本地模拟服务；普通回归不得依赖真实密钥、网络配额或不稳定排名。
- 断言数据、状态、引用范围、请求协议和错误脱敏等可观察行为；避免固定摘要、整页文本快照和仅镜像实现的测试。
- 子进程、模拟 HTTP 服务与日志句柄在结束或异常时清理；只停止测试自己启动的进程。
- `fixtures/` 保存可公开的固定样例，不复制用户文献、凭据或数据库。`model_discovery_server.py` 仅用于隔离验收。

## 验证入口

命令均在仓库根目录执行。按受影响行为选择测试；跨层改动或整体回归运行 `uv run --locked python -m unittest discover -s tests -v`。

| 改动范围 | 主要测试文件或入口 |
|---|---|
| HTTP、项目与文献 | `test_api.py`、`test_chat_api.py`、`test_collections_api.py` |
| 检索、科研与证据范围 | `test_retrieval.py`、`test_workflow.py`、`test_collection_workflow.py` |
| 工具发现、参数与独立调用 | `test_tools.py`，以及 API、worker、workflow 的调用入口回归 |
| 模块移动后的路径语义 | `test_layout.py`、`test_model.py`，以及实际 worker 与模拟 HTTP 闭环 |
| 设置、模型与日志 | `test_settings.py`、`test_model.py`、`test_model_discovery.py`、`test_logging.py` |
| Worker 生命周期 | `test_worker.py`，隔离数据下的实际 `python -m apps.worker` 入口 |
| Windows 进程树 | `test_launcher_job.ps1`、`launcher_wrapper.ps1`；先读参数与 [脚本规则](../scripts/AGENTS.md) |
| 前端 | `pnpm check:web`、`pnpm build:web`、页面实际 HTTP 与交互入口 |

跨进程行为可用 `scripts/verify_*.py` 验收，运行前确认各脚本隔离条件。结果写入 README 验收部分，区分单测、模拟 HTTP、浏览器与真实云端验证。
