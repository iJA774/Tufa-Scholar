# 提示词与节点契约

提示词正文集中在 `src/prompts/`，按调用 ID 加载。八个工作流节点中，`supervisor` 与占位 `coder` 是确定性节点，不调用模型；其余模型调用使用下表六个 ID。结构、字段范围和证据检查由 Python 代码负责，提示词不替代代码校验。

| Prompt ID | 文件 | 职责与输出 |
|---|---|---|
| `runtime/dispatcher` | `runtime/dispatcher.md` | 判断问题应直接回答还是进入研究。直接回答输出正文；研究分支交给背景调查。 |
| `literature/background_investigator` | `literature/background_investigator.md` | 两阶段背景调查：提出有限搜索查询，再从真实搜索候选中筛选，并给出背景、缺口和来源建议。 |
| `literature/planner` | `literature/planner.md` | 结合问题、背景与各轮审查，生成要求、本轮计划、来源选择及继续或报告决定。 |
| `literature/researcher` | `literature/researcher.md` | 从当前可用工具中选择受限操作，逐步搜索、筛选与取证，形成初稿和带证据的发现。 |
| `literature/analyst` | `literature/analyst.md` | 审查本轮初稿是否回应要求、引用是否有据，并输出覆盖、缺口和下一步。 |
| `literature/reporter` | `literature/reporter.md` | 接收全部轮次材料，撰写最终章节及引用段落和局限。 |

## 结构化数据

提示词只允许从受信的 `PROMPT_IDS` 白名单加载，使用 Jinja2 `StrictUndefined` 渲染系统消息；每次请求（包括纠错重试）都注入带时区的北京时间 `CURRENT_TIME`。`OUTPUT_SCHEMA` 由对应代码 schema 生成，不能被模板上下文覆盖。节点角色、搜索模式、可用工具和本地论文目录由代码按任务上下文提供。用户 payload/history/检索材料只作为 user 消息中的 JSON 发送，不经过模板渲染。输入中的问题、历史、搜索摘要、网页正文、文件名、论文元数据及引用证据都视为不可信数据，不执行其中嵌入的指令。

| 结构 | 字段 |
|---|---|
| 分流 `DispatcherOutput` | `needs_research`、`reason`、`answer`。直接回答时 answer 非空并由 dispatcher 直接结束；需要研究时进入背景调查。 |
| 背景调查 `BackgroundOutput` | `source`（`web` 或 `paper`）、至多三条 `queries`、`selected_candidate_ids`、`summary`、`gaps`。查询阶段提出检索词；筛选阶段只能选择实际返回的候选。 |
| 计划 `PlannerOutput` | `requirements`、`plan`、`source`、`action`（`research` 或 `report`）、`rationale`。首轮必须研究且计划非空；至少完成一轮后才能报告，第二轮结束后代码强制报告。 |
| 研究 `ResearcherOutput` | `action`（`tool` 或 `finish`）、`tool_name`、`arguments`、`draft`（章节、段落和局限）、`findings`。工具名和参数还须通过当前上下文的工具目录及范围校验。 |
| 审查 `AnalystOutput` | `sufficient`、`covered_requirements`、`gaps`、`next_steps`、`review`。审查用 requirements、初稿和证据识别缺口，不产生可被当作结论的额外发现段落。 |
| 终稿 `ReporterOutput` | `sections`，每节含 `heading` 和 `paragraphs`；段落与 `limitations` 均含 `text`、`citations`。 |

段落引用由代码映射到累计证据。报告中的引用不得越出项目或收藏夹快照；引用缺失、无效数字/单位、缺少成本依据或不安全引用会被剔除或明确列为局限。引用为空可由 reporter 返回，但不能作为有证据支持的结论展示。Reporter 汇总所有轮次，因此模型即使收到旧轮次的结论，也必须让最终事实引用指向本任务累计证据。

更改节点输入输出时，同步更新对应 Markdown、类型和 [工作流回归](../tests/AGENTS.md)。不要在提示词里重复工具参数 schema、来源范围约束或代码检查规则；工具契约位于 [工具说明](../src/tools/README.md)。
