# Benchmark 评测

Tufa 的第一期评测接入原版 DeepResearch Bench（DRB v1）。题库包含 100 道题，中文和英文各 50 道，覆盖 22 个领域。题库、评测提示词、参考报告、标准和评分代码锁定到同一个官方提交。题库下载及手动导入校验 SHA-256，允许 UTF-8 BOM 和 Windows 换行。安装评分器时逐文件校验，运行评分前重新校验。官方资产按需下载到本机数据目录。其 Apache 2.0 许可证随评分器保留。[官方提交及许可证](https://github.com/Ayanami0730/deep_research_bench/tree/f2735b5c3636759c22f9e936c0232de8bf545b67) · [论文](https://arxiv.org/abs/2506.11763)

## 工作台

1. 按根目录 [README](../README.md#配置与启动) 启动 API、单 worker 和前端。在「设置 → Benchmark 评测」打开工作台，也可访问 `/benchmark`。
2. 下载数据集或导入锁定版本的官方 `query.jsonl`。预览可按 ID、语言、领域和当前批次的研究状态筛选。
3. 选择当前或指定待测模型、研究模式、深度、来源及搜索引擎。默认使用深度研究、标准深度、自动来源。每题拥有独立会话，批次项目为空。
4. 选 1、5、10、20 道题或全部匹配题目。按语言、领域缩小题库，或者明确指定任务 ID。随机种子和题目 ID 列表保存在批次中。默认最多 5 道，较大评测需调高「最多任务」，并审核累计时间、请求数和服务费用。
5. 确认配置的外部服务和预算，再选择「仅生成报告」或「开始完整评测」。完整评测需要先安装评分器，并在「独立 Judge 和正文提取服务」中选定 RACE、FACT 或两项。
6. 批次详情显示当前题目和阶段、样本数、成功率、评分覆盖率、计时、用量、雷达图、语言与领域对比及任务详情。可暂停派发、继续、跳过尚未执行的题目、停止派发或重试失败阶段。关闭页面后仍由 worker 调度。
7. 在批次详情用「仅对已有报告评分」为已生成的报告运行选定评分。对失败的 FACT 或 RACE 可单独重试。历史记录可以选择多个批次对比，并导出 JSONL、JSON、CSV、Markdown 或 ZIP。

评分覆盖率使用全批次选中题目数作分母。对比会检查样本、数据集版本、评分器版本、Judge 配置和覆盖率。样本差异或服务差异需要结合各批次配置解读。官方默认模型标记反映锁定代码的默认模型和服务组合，榜单的提交条件及可比性由官方规定。

## Judge 配置

Judge 与 Tufa 模型使用各自的地址和密钥。锁定版本默认使用 OpenRouter，RACE 使用 `openai/gpt-5.5`，清洗使用 `openai/gpt-5.6-luna`，FACT 使用 `openai/gpt-5.4-mini`，正文提取使用 Jina Reader。可改用兼容 Chat Completions 的服务，以及返回 Jina Reader JSON 结构的正文提取端点。评分模型需要支持上游规定的推理强度、输出预算和输出格式。

在工作台输入密钥，也可在启动 API 前配置以下环境变量。环境密钥只匹配对应服务的地址。

| 变量 | 用途 |
| --- | --- |
| `TUFA_BENCHMARK_JUDGE_API_KEY` | 独立 Judge 密钥 |
| `TUFA_BENCHMARK_JUDGE_URL` | 该环境密钥对应的地址，默认 `https://openrouter.ai/api/v1` |
| `TUFA_BENCHMARK_READER_API_KEY` | FACT 正文提取密钥 |
| `TUFA_BENCHMARK_READER_URL` | 该环境密钥对应的地址，默认 `https://r.jina.ai` |

批次凭据快照存在本机 SQLite，供重启恢复，与 Tufa 本机模型配置一样需要保护数据库文件。导出与评测接口只提供可公开的配置和结果。选择自定义 Judge 时，历史记录会标记该配置。开始评分后更换模型或服务需要新建评测批次，避免把不同 Judge 的得分混在一起。

## CLI

在仓库根目录运行。CLI 调用回环 API，已有单 worker 执行队列。CLI 提交后打印批次 ID。

```powershell
uv run --locked python -m scripts.run_benchmark download
uv run --locked python -m scripts.run_benchmark import C:/path/to/query.jsonl
uv run --locked python -m scripts.run_benchmark setup-scorer
uv run --locked python -m scripts.run_benchmark run --limit 1 --generate-only --authorize
uv run --locked python -m scripts.run_benchmark run --limit 5 --seed 42 --metrics race fact --authorize
uv run --locked python -m scripts.run_benchmark run --ids 51 52 --generate-only --authorize
uv run --locked python -m scripts.run_benchmark run --all --budget C:/path/to/budget.json --authorize
uv run --locked python -m scripts.run_benchmark status --run-id RUN_ID
uv run --locked python -m scripts.run_benchmark pause --run-id RUN_ID
uv run --locked python -m scripts.run_benchmark resume --run-id RUN_ID --authorize
uv run --locked python -m scripts.run_benchmark evaluate --run-id RUN_ID --metrics race --authorize
uv run --locked python -m scripts.run_benchmark retry --run-id RUN_ID --metrics fact --authorize
uv run --locked python -m scripts.run_benchmark compare FIRST_RUN_ID SECOND_RUN_ID
uv run --locked python -m scripts.run_benchmark export --run-id RUN_ID --format zip
```

`--language zh`、`--language en` 和 `--topic` 可与抽样配合。`--budget` 接收包含 `max_tasks`、`task_seconds`、`total_seconds`、`max_requests`、`max_tokens` 和 `max_cost` 的 JSON 对象，后两项可省略。全量评测需将 `max_tasks` 设为 100，并按授权额度制定时间和请求上限。`--judge` 接收 Judge 的 JSON 配置，可填写 `base_url`、`race_model`、`clean_model`、`fact_model`、`reader_url`、`timeout`、`retries`。优先使用相应环境变量提供密钥，避免把凭据写入命令行或配置文件。重复提交可传入相同的 `--request-id`。

## 解读结果

- RACE 使用上游清洗、按题目的官方评测标准、加权计算及目标相对参考的比值。Overall 和四个维度按成功评分题目进行算术平均。
- FACT 使用上游提取、按 URL 去重、正文提取和逐条支持性判断。准确率为支持数除以可判定数。有效引用为支持数除以提取出引用的报告数。分母为零时显示缺失。可查看支持、不支持、未知、来源不可访问、格式异常及服务失败等情况。提取覆盖不足与评分覆盖率一起保留。上游只提取带链接的事实，诊断记录没有 URL 的引用编号和没有提取到的引用，报告正文中所有未引用事实的数量未知。
- 正式 Markdown、引用证据和适配后的报告独立保留。引用适配从已有编号与正式参考文献解析安全 URL，在原标记位置添加链接。报告降级依照正常研究工作流记录。
- 已报告的 Token 和费用用于预算检查。缺少供应商字段时显示缺失，并记录已知用量的覆盖数。请求数可用于约束支持该功能的模型调用。已发出的请求可能继续计费，单次调用可超过剩余 Token 或费用额度。暂停和停止默认等待当前阶段完成；单阶段超时会终止本机执行。
- 自动恢复从未完成阶段开始。已经提交的报告及评分继续可用。中断且上游已收到的请求可能产生费用。公开语料和预训练中仍可能包含基准材料，浏览来源过滤只覆盖已知官方发布路径。

## 扩展其他数据集

新增适配器的注册入口位于 [registry.py](../src/benchmark/registry.py)。注册题库身份与校验、原始题目的映射、报告适配和评分入口，再定义该基准自己的结果指标与界面。批次、阶段检查点、预算和普通任务优先级由共用调度处理。

| 数据集 | 接入时需要校验的协议 |
| --- | --- |
| DeepResearch Bench II | 固定第二版任务、评分标准及官方评分器的独立版本 |
| ResearchRubrics | 从其发布版本读取任务级条款及适用的聚合方式 |
| BrowseComp | 使用其答案格式和答案核验协议 |
| GAIA | 保留任务附件、权限及数据分割，采用其答案规范 |
| DeepSearchQA | 保存答案项和查找来源，按其规则比较多项答案及完整性 |

## HTTP API

启动后可在 [FastAPI 交互式文档](http://127.0.0.1:8000/docs) 查看评测路由的请求与响应结构。

| 方法与路径 | 功能 |
| --- | --- |
| `GET /benchmark`、`GET /benchmark/dataset/tasks` | 锁定题库及评分器状态、预览原始题目 |
| `GET/POST /benchmark/runs`、`POST /benchmark/runs/{id}/actions` | 批次历史、创建与运行控制 |
| `GET /benchmark/runs/{id}/tasks/{task_id}`、`GET /benchmark/runs/{id}/export`、`POST /benchmark/compare` | 单题报告、结果导出及可比性检查 |

## 评测验收

在仓库根目录运行以下命令。脚本使用隔离数据目录和模拟上游，默认从官方 GitHub 下载锁定题库和评分器。已有校验过的资产目录可用 `--assets` 指定。

```powershell
uv run --locked python -m scripts.verify_benchmark
```

## 维护入口

| 路径 | 职责 |
| --- | --- |
| [主 API](../apps/api/main.py)、[评测 API](../apps/api/benchmarks.py) | 注册评测端点、校验请求、隔离普通查询和交付文件 |
| [Worker](../apps/worker/__main__.py) | 优先处理普通任务、运行中断恢复及单阶段子进程 |
| [题库](../src/benchmark/datasets.py)、[锁定清单](../src/benchmark/drb_v1_manifest.json)、[注册表](../src/benchmark/registry.py) | 官方来源、哈希、加载与适配器入口 |
| [请求模型](../src/benchmark/models.py)、[命令服务](../src/benchmark/service.py)、[调度器](../src/benchmark/scheduler.py) | 预算和配置快照、对外命令及阶段时限 |
| [评分器](../src/benchmark/evaluator.py)、[报告适配](../src/benchmark/reports.py)、[结果](../src/benchmark/results.py) | 官方算法封装、引用映射、统计与五种导出 |
| [基准表结构](../src/persistence/benchmark_schema.py)、[基准仓储](../src/persistence/benchmarks.py)、[普通仓储](../src/persistence/repository.py)、[存储初始化](../src/persistence/storage.py) | SQLite 增量建表、任务衔接与状态转换 |
| [运行范围](../src/infrastructure/execution.py)、[本机设置](../src/infrastructure/settings.py)、[模型](../src/integrations/model.py)、[OpenAlex](../src/integrations/openalex.py)、[网页检索](../src/integrations/web_search.py)、[网页读取](../src/integrations/web_page.py) | 阶段配置、模型与检索计量以及已知基准来源过滤 |
| [设置入口](../apps/web/app/settings/SettingsPanel.tsx)、[工作台](../apps/web/app/benchmark/page.tsx)、[接口类型](../apps/web/app/benchmark/api.ts)、[配置表单](../apps/web/app/benchmark/forms.tsx)、[结果组件](../apps/web/app/benchmark/results.tsx)、[样式](../apps/web/app/benchmark/benchmark.module.css) | 浏览器配置、进度、历史、详情与图表 |
| [CLI](../scripts/run_benchmark.py)、[入口验收](../scripts/verify_benchmark.py)、[回归测试](../tests/test_benchmark.py) | 命令行操作、真实入口的模拟验收及隔离测试 |
| [依赖清单](../pyproject.toml)、[锁文件](../uv.lock) | Python 3.13 根环境的评分器依赖 |
| [README](../README.md)、[架构](ARCHITECTURE.md)、[开发取舍](DEVELOPMENT.md)、[状态](STATUS.md)、[本使用说明](BENCHMARK.md) | 使用入口、设计、技术债和验证边界 |
