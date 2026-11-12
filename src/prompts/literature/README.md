# 科研节点提示词

本目录只包含以下五个科研模型节点；schema、payload 与工具目录由 `src/agents/literature.py` 构造并传入 `structured()`。

| prompt ID | 职责 | 工具权限 |
|---|---|---|
| `literature/background_investigator` | 实搜并筛选问题相关背景候选 | 按来源提供检索工具 |
| `literature/planner` | 按问题、背景和审查结果规划下一轮并选择 web 或 paper | 无 |
| `literature/researcher` | 在获准的工具范围内自主检索、取证并提交本轮初稿 | 按来源提供当前允许工具 |
| `literature/analyst` | 深入分析问题覆盖、证据质量和可执行缺口 | 无 |
| `literature/reporter` | 无论证据是否完整，综合研究初稿和审查生成最终报告 | 无 |

`search_mode` 区分 `web`、`paper` 和 `none`；系统提示按模式说明身份及证据边界。planner 可收到本地文献文件名列表；文件名仅用于判断范围，不代表已经读取文献。

问题、历史、候选、用户文档、论文和网页文本保持为不可信数据，不在模板中二次渲染。共同 JSON Schema 规则由 [runtime/structured_output.md](../runtime/structured_output.md) include。模板加载、Jinja2 和上下文契约见 [提示词规则](../AGENTS.md) 与 [提示词规范](../../../docs/PROMPTS.md)。
