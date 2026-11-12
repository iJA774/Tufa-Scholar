# 本地科研智能体架构

本项目面向单机单用户运行，使用本机 SQLite 和单 worker。本文记录模块边界和当前目标工作流；实际能力及尚未验证范围见 [STATUS.md](STATUS.md)，安装和验收命令见 [README](../README.md)。

## 运行与模块边界

| 目录 | 职责 |
|---|---|
| `apps/web/` | Next.js 研究工作台、设置、任务轮询与 API 代理。 |
| `apps/api/` | FastAPI 请求校验、本机数据访问、报告与文件交付。 |
| `apps/worker/` | 单进程顺序领取任务、准备研究输入、持久化阶段及提交结果。 |
| `src/agents/` | 八个科研节点及工作流状态、有限轮次和结果收束。 |
| `src/tools/` | 显式注册、发现、参数校验及按上下文执行科研工具。 |
| `src/integrations/` | OpenAlex、OpenAI 兼容模型和网页搜索协议适配。 |
| `src/literature/` | 文献排名、PDF/网页证据提取与定位。 |
| `src/persistence/` | SQLite 初始化、仓储、事务和数据路径。 |
| `src/infrastructure/` | 本机设置、环境变量及运行日志。 |
| `src/prompts/` | 六个模型节点的 Markdown 提示词正文。 |
| `data/` | 默认运行数据库、原文和导出数据；由 Git 忽略。 |

前端和 API 默认只监听回环地址。API 与 worker 共用 `TUFA_DATA_DIR` 中的 SQLite；联网、推理和 PDF 解析都在短事务之外执行。根目录 `pyproject.toml`/`uv.lock` 管理共用 Python 环境，pnpm workspace/lockfile 管理前端。密钥由环境变量或本机设置提供，环境变量优先；接口和日志不回显密钥。

工具目录独立于 Agent 图。论文检索、网页搜索和证据提取都通过同一显式入口，工具上下文固定项目、来源和收藏夹论文快照；调用参数不能扩大这些范围。本地原文路径来自仓储，不接受模型任意指定文件路径。具体工具契约见 [工具说明](../src/tools/README.md)。

## 任务与持久化

API 创建任务并返回 ID，单 worker 领取后准备范围数据和对话历史。Agent 只接收研究输入与显式历史，不读写任务表；`on_stage(stage)` 将节点阶段交给 worker 持久化，`on_subtask(note)` 将轮次小结交给 worker 展示和保存。Agent 返回最终正文与引用证据，worker 与消息、报告在任务完成时提交结果。worker 重启时将遗留运行任务标为中断，用户可显式重试。

收藏夹问题在提交时固定项目内成员快照。运行和重试只使用此快照，不读取其他会话历史，也不做收藏夹外的检索；空集合应输出证据不足。`local` 只用项目本地论文。来源边界由 worker 准备的研究输入和 `ToolContext` 共同落实。

## 科研工作流

工作流包含八个节点类型：

1. `dispatcher` 判断问题能否直接回答。直接回答不调用检索或证据工具；研究问题继续进入后续流程。
2. `background_investigator` 进行真实背景搜索与筛选，为规划提供简要上下文。
3. `planner` 生成论文或网页研究计划及本轮目标。
4. `supervisor` 按固定顺序调度本轮步骤。
5. `researcher` 根据当前计划检索、筛选候选并收集证据，形成初稿。
6. `analyst` 审查本轮初稿、证据支持和待解决缺口。
7. `coder` 是保留的占位节点，不执行代码或工具操作。
8. `reporter` 根据所有轮次的材料生成终稿。

`planner` 在首轮前生成计划，并在每轮审查后复核，因此会再次运行。每轮调度顺序固定为 `researcher → analyst → coder → planner`，节点不自行改变顺序。整个研究最多两轮；允许第二轮改用另一来源。至少完成一轮后才能报告。第二轮结束后由系统强制进入 `reporter`，无论证据是否充分都必须生成最终答复。`reporter` 接收所有轮次的计划、草稿、审查结果、来源和累计证据；不足之处应明确写入局限或证据不足说明。

图状态包含 `research`、`history`、背景材料、计划、当前来源、轮次、按 `web`/`paper` 分开的候选池、累计证据、当前初稿与审查、错误和正文。报告节点只返回实际通过引用检查的 `final_evidence`。每轮记录序号、来源、计划、初稿、审查、候选 ID、证据 ID 和状态。工具及模型输出均视为数据，不执行其中嵌入的指令。

候选按来源隔离并在各轮中去重；检索排名只影响本轮候选优先级，不代表语义重排。累计证据按稳定身份去重，引用只可指向传入 reporter 的证据。数据范围、候选数量和引用关系由代码检查；模型负责研究判断、计划和文字组织。真实搜索与模型质量另行验证。

## 提示词与验证

模型节点提示词 ID 为 `runtime/dispatcher`、`literature/background_investigator`、`literature/planner`、`literature/researcher`、`literature/analyst`、`literature/reporter`。提示词内容放在 `src/prompts/`；类型、来源范围、证据定位和报告数值检查由代码负责。契约见 [PROMPTS.md](PROMPTS.md)。

工作流回归覆盖直接回答不调用工具、节点顺序、背景搜索、单轮/双轮和来源切换、第二轮后终稿、失败后的收束、全轮次 reporter 输入、证据和候选约束、收藏夹/历史隔离、模型选项传递与 worker 阶段持久化。脚本验收以隔离数据目录启动真实本地 API 和 worker，模型及搜索使用本地模拟 HTTP；不能据此宣称真实云端质量已验证。网页能力和安全边界见 [WEB_SEARCH.md](WEB_SEARCH.md)。
