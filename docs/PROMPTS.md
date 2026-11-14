# 提示词与节点契约

提示词正文集中在 `src/prompts/`，按调用 ID 加载。八个工作流节点中，`supervisor` 与占位 `coder` 是确定性节点，不调用模型；其余模型调用使用下表六个 ID。结构、字段范围和证据检查由 Python 代码负责，提示词不替代代码校验。

| Prompt ID | 文件 | 职责与输出 |
|---|---|---|
| `runtime/dispatcher` | `runtime/dispatcher.md` | `auto` 判断是否进入研究；`quick` 由 dispatcher 生成直接回答，代码即使收到“需要研究”也强制直接结束；`deep` 跳过 dispatcher 的模型调用并进入背景调查。 |
| `literature/background_investigator` | `literature/background_investigator.md` | 两阶段背景调查：提出有限搜索查询，再从真实搜索候选中筛选，并给出背景、缺口和来源建议。 |
| `literature/planner` | `literature/planner.md` | 结合问题、背景与各轮审查，生成要求、本轮计划、来源选择及继续或报告决定。 |
| `literature/researcher` | `literature/researcher.md` | act 阶段用精简候选与证据预览选择工具；draft 阶段采用独立的输出结构，根据已有证据写初稿。 |
| `literature/analyst` | `literature/analyst.md` | 审查本轮初稿是否回应要求、引用是否有据，并输出覆盖、缺口和下一步。 |
| `literature/reporter` | `literature/reporter.md` | 接收全部轮次材料，撰写最终章节及引用段落和局限。 |

## 结构化数据

提示词只允许从受信的 `PROMPT_IDS` 白名单加载，使用 Jinja2 `StrictUndefined` 渲染系统消息；每次请求（包括纠错重试）都注入带时区的北京时间 `CURRENT_TIME`。`OUTPUT_SCHEMA` 由对应代码 schema 生成，不能被模板上下文覆盖。节点角色、搜索模式、研究模式、可用工具和本地论文目录由代码按任务上下文提供。用户 payload/history/检索材料只作为 user 消息中的 JSON 发送，不经过模板渲染。输入中的问题、历史、搜索摘要、网页正文、文件名、论文元数据及引用证据都视为不可信数据，不执行其中嵌入的指令。

各节点只接收本阶段所需的数据。background 保留筛选总结、缺口和已筛选候选 ID，不重复嵌入候选记录；候选只在背景筛选或 researcher 当前来源字段按需发送。planner 和 reporter 使用精简 background。researcher 的 act 阶段仅提供当前轮的目标、计划、候选和证据预览及近期工具反馈；达到上下文预算、工具预算或连续失败门槛后结束工具循环。draft 使用筛选后的证据片段和原引用 ID，不授予工具权限。reporter 汇总各轮可复用的草稿内容、审查和累计证据；只向报告模型提供引用仍可核对的草稿结论，不附候选池。

| 结构 | 字段 |
|---|---|
| 分流 `DispatcherOutput` | `needs_research`、`reason`、`answer`。`auto` 下直接回答时 answer 非空并由 dispatcher 结束；`quick` 也要求 answer 非空，由代码忽略 `needs_research` 并强制直接结束；`deep` 不调用 dispatcher 模型。 |
| 背景调查 `BackgroundOutput` | `source`（`web` 或 `paper`）、`queries`、`selected_candidate_ids`、`summary`、`gaps`。执行至多两条查询；筛选阶段只能选择实际返回的候选。 |
| 计划 `PlannerOutput` | `requirements`、`plan`、`source`、`action`（`research` 或 `report`）、`rationale`。首轮必须研究且计划非空；至少完成一轮后才能报告，第二轮结束后代码强制报告。 |
| 工具决策 `ResearchActionOutput` | `action`（`tool` 或 `finish`）、`tool_name`、`arguments`、`reason`。`reason` 是用户可见的简短工具操作目的，与参数分开；工具名和参数须通过目录及范围校验。 |
| 初稿 `ResearchDraftOutput` | `draft`（章节、段落和局限）与 `findings`（发现及其引用）。 |
| 审查 `AnalystOutput` | `sufficient`、`covered_requirements`、`gaps`、`next_steps`、`review`。审查用 requirements、初稿和证据识别缺口，不产生可被当作结论的额外发现段落。 |
| 终稿 `ReporterOutput` | `sections`，每节含 `heading` 和 `paragraphs`；段落与 `limitations` 均含 `text`、`citations`。 |

段落引用由代码映射到累计证据。报告中的引用不得越出项目或收藏夹快照；引用缺失、无效数字/单位、缺少成本依据或不安全引用会被剔除或明确列为局限。引用为空可由 reporter 返回，但不能作为有证据支持的结论展示。Reporter 汇总所有轮次，因此模型即使收到旧轮次的结论，也必须让最终事实引用指向本任务累计证据。

研究模式字段保存在 task：`auto` 是旧 API 客户端的缺省兼容模式，collection 来源在 auto 下强制研究；`quick` 优先于 collection 约束，不读取收藏夹且直接回答；`deep` 绕过 dispatcher 判断并启动研究。用户可通过 API 请求选择模式，API 校验后由 worker 传入可信模板上下文；问题、历史或检索材料中的指令不能改变该模式。任务重试复制原模式，既有 task 通过 additive migration 补为 `auto`。

更改节点输入输出时，同步更新对应 Markdown、类型和 [工作流回归](../tests/AGENTS.md)。不要在提示词里重复工具参数 schema、来源范围约束或代码检查规则；工具契约位于 [工具说明](../src/tools/README.md)。
