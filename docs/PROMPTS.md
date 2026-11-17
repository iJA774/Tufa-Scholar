# 提示词与节点契约

提示词正文集中在 `src/prompts/`，按调用 ID 加载。研究工作流中的 `supervisor` 负责确定性调度，其余六个节点调用模型。`preprocesser` 独立处理本地 Markdown 分类。结构、字段范围和证据检查由 Python 代码负责。

| Prompt ID | 文件 | 职责与输出 |
|---|---|---|
| `runtime/dispatcher` | `runtime/dispatcher.md` | `auto` 判断是否进入研究；`quick` 由 dispatcher 生成直接回答，代码即使收到“需要研究”也强制直接结束；`deep` 跳过 dispatcher 的模型调用并进入背景调查。 |
| `literature/background_investigator` | `literature/background_investigator.md` | 两阶段背景调查：提出有限搜索查询，再从真实搜索候选中筛选，并给出背景、缺口和来源建议。 |
| `literature/planner` | `literature/planner.md` | 结合问题、背景与各轮审查，生成要求、本轮计划、来源选择及继续或报告决定。 |
| `literature/researcher` | `literature/researcher.md` | act 阶段通过原生 Function Calling 选择工具；draft 阶段根据已有证据写初稿。 |
| `literature/analyst` | `literature/analyst.md` | 审查本轮初稿是否回应要求、引用是否有据，并输出覆盖、缺口和下一步。 |
| `literature/reporter` | `literature/reporter.md` | 根据各轮材料自由组织 Markdown 报告。论文主线比较研究方法、实验设计和结论，网页主线组织可核查事实和来源。证据不足时具体说明影响。 |
| `literature/preprocesser` | `literature/preprocesser.md` | 从本地 Markdown 提取多个研究领域、标签和简短描述。 |

## 结构化数据

提示词只允许从受信的 `PROMPT_IDS` 白名单加载，使用 Jinja2 `StrictUndefined` 渲染系统消息。每次请求都注入带时区的北京时间 `CURRENT_TIME`。分流、背景调查和文档分类使用由代码 schema 生成的 `OUTPUT_SCHEMA`，researcher act 使用原生 Function Calling，其余研究生成阶段使用 Markdown 文本。问题、历史、检索材料和本地文档只作为 user JSON 数据发送。

各节点只接收本阶段所需的数据。background 保留筛选总结、缺口和已筛选候选 ID，不重复嵌入候选记录。planner 和 reporter 使用精简 background。researcher 的 act 阶段接收当前轮目标、计划、候选、证据预览及近期工具反馈。Jinja2 根据研究方向、本地来源开关和当前阶段选择指引。本地研究的领域名称通过 `local_domains` 传入，领域选择后通过工具反馈逐页披露文献元数据。达到上下文预算、工具预算或连续失败门槛后结束工具循环。draft 使用筛选后的证据片段和原引用 ID。reporter 汇总各轮草稿、审查和累计证据。超过[证据额度](ARCHITECTURE.md#工作流预算)时保留各轮补查材料并提供 `evidence_omitted_count`。

| 结构 | 字段 |
|---|---|
| 分流 `DispatcherOutput` | `needs_research`、`reason`、`answer`。`auto` 下直接回答时 answer 非空并由 dispatcher 结束；`quick` 也要求 answer 非空，由代码忽略 `needs_research` 并强制直接结束；`deep` 不调用 dispatcher 模型。 |
| 背景调查 `BackgroundOutput` | `source`（`web` 或 `paper`）、`queries`、`selected_candidate_ids`、`summary`、`gaps`。查询预算和每次搜索结果上限见[工作流预算表](ARCHITECTURE.md#工作流预算)；筛选阶段只能选择实际返回的候选。 |
| 文档分类 | `domains` 为一项或多项研究领域，`tags` 为主题标签，`description` 为最长 20 字的单行描述。 |
| 计划 Markdown 与调度控制块 | 正文由模型自由组织。末尾 `tufa-control` 块包含 `requirements`、`plan`、`source`、`action`、`rationale` 和深入档的 `completion_confirmed`，按 `PlannerOutput` 校验。首轮必须研究且计划非空。 |
| 工具决策 | 模型接口返回原生函数调用或表示本轮结束。函数名和参数须通过目录及范围校验。`reason` 是用户可见的简短操作目的，执行前从业务参数中取出。 |
| 初稿 | 自由 Markdown 正文，在支持主张的段落、列表项或表格行内使用 `[[证据ID]]`。无引用的过程性缺口用 `> 局限：…`。 |
| 终稿 | 自由 Markdown 正文。引用可选，建议用 `[[证据ID]]` 标注有直接依据的关键事实。区分材料支持的事实与模型的分析判断。 |
| 审查 Markdown 与调度控制块 | 正文解释问题覆盖和剩余缺口。末尾 `tufa-control` 块包含 `sufficient`、`covered_requirements`、`gaps`、`next_steps`，深入档另用 `unresolved_contradictions`、`evidence_reliability_confirmed`、`important_conclusions_cross_validated`，按 `AnalystOutput` 校验。 |

段落引用由代码映射到当前节点可见证据，遵守项目和收藏夹快照范围。初稿校验事实段落的引用、数值和成本依据。终稿保留无引用正文，只验证显式引用是否存在并符合范围。证据可追溯性与事实、数值判断由写作要求分别约束。

研究模式字段保存在 task：`auto` 是旧 API 客户端的缺省兼容模式，collection 来源在 auto 下强制研究；`quick` 优先于 collection 约束，不读取收藏夹且直接回答；`deep` 绕过 dispatcher 判断并启动研究。用户可通过 API 请求选择模式，API 校验后由 worker 传入可信模板上下文；问题、历史或检索材料中的指令不能改变该模式。任务重试复制原模式，既有 task 通过 additive migration 补为 `auto`。

研究分支使用任务快照中的深度、原始证据优先和相关性过滤。Planner 接收深度及代码计算的预算，结合 Analyst 的审查决定是否补证。深入档重点复核核心要求覆盖、证据充分性和重要矛盾，再判断是否存在可执行的补查方向。Researcher 根据偏好使用候选和已提取证据。终稿的 `search_mode` 由首轮研究方向确定；后续渠道切换只影响每轮取证。Reporter 根据实际收束原因与 `outstanding_gaps` 说明未解决事项；该字段反映最近一次累计审查，历史审查中的缺口仅作过程背景。

更改节点输入输出时，同步更新对应 Markdown、类型和 [工作流回归](../tests/AGENTS.md)。不要在提示词里重复工具参数 schema、来源范围约束或代码检查规则；工具契约位于 [工具说明](../src/tools/README.md)。
