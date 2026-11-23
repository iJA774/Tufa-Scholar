# 科研节点提示词

本目录包含科研工作流的模型提示词。工作流提供阶段输入，并为工具决策提供当前来源允许的原生函数定义。

| prompt ID | 职责 | 工具权限 |
|---|---|---|
| `literature/background_investigator` | 实搜并筛选问题相关背景候选 | 按来源提供检索工具 |
| `literature/planner` | 按问题、背景和审查结果编写 Markdown 研究计划 | 无 |
| `literature/route` | 通过原生工具选择研究渠道和收束决策 | 原生工具调用 |
| `literature/researcher` | 在获准的工具范围内自主检索、取证并提交本轮初稿 | 按来源提供当前允许工具 |
| `literature/analyst` | 深入分析问题覆盖、证据质量和可执行缺口 | 无 |
| `literature/reporter` | 无论证据是否完整，综合研究初稿和审查生成最终报告 | 无 |

独立文档分类节点使用 `literature/preprocesser`，从 Markdown 生成多领域、主题标签与 20 字以内的描述。文档正文通过 user JSON 传入。

`search_mode` 按节点表示 `web`、`paper` 或尚未确定的方向。模板按模式提供取证或写作指导，reporter 使用首轮研究方向。researcher 的 Jinja2 分支说明当前模式可使用的来源及操作。本地研究的领域名称、领域目录及阅读工具返回的证据均作为 user JSON 数据提供。

问题、历史、候选、用户文档、论文和网页文本始终作为待处理数据。背景调查使用公共的 [结构化输出契约](../runtime/structured_output.md)。计划使用 Markdown，路由使用独立原生工具调用，审查使用 Markdown 正文及单独的调度数据，初稿和终稿使用带证据标记的 Markdown。模板加载和上下文见 [提示词规则](../AGENTS.md) 与 [提示词规范](../../../docs/PROMPTS.md)。
