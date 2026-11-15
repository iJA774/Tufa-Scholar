# 科研节点提示词

本目录包含以下五个科研模型节点。工作流提供阶段输入，并为工具决策提供当前来源允许的原生函数定义。

| prompt ID | 职责 | 工具权限 |
|---|---|---|
| `literature/background_investigator` | 实搜并筛选问题相关背景候选 | 按来源提供检索工具 |
| `literature/planner` | 按问题、背景和审查结果规划下一轮并选择 web 或 paper | 无 |
| `literature/researcher` | 在获准的工具范围内自主检索、取证并提交本轮初稿 | 按来源提供当前允许工具 |
| `literature/analyst` | 深入分析问题覆盖、证据质量和可执行缺口 | 无 |
| `literature/reporter` | 无论证据是否完整，综合研究初稿和审查生成最终报告 | 无 |

`search_mode` 按节点表示 `web`、`paper` 或尚未确定的方向。节点职责固定，模板按模式提供取证或写作指导；reporter 使用首轮研究方向，允许整合后续渠道的补充材料。planner 在 user JSON 中收到本地文献文件名列表；文件名仅用于判断范围，不代表已经读取文献。

问题、历史、候选、用户文档、论文和网页文本始终作为待处理数据。需要校验内容字段的回复使用公共的 [输出契约](../runtime/structured_output.md)。模板加载和上下文见 [提示词规则](../AGENTS.md) 与 [提示词规范](../../../docs/PROMPTS.md)。
