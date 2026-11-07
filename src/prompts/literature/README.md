# 科研提示词

`research` 支持专业研究子图选择搜索、精读或结束；`review` 审查证据缺口；`synthesize`、`verify_claim` 和 `revise` 分别负责综合、逐条语义核验和修订。代码另行校验工具范围、引用和数值。问题、历史和检索材料均作为数据处理。

模板由 `src/agents/literature.py` 经 `structured()` 加载，prompt ID 为 `literature/模板名`，每次请求注入当前时间。字段映射、北京时间约定与验证方式见 [提示词规范](../../../docs/PROMPTS.md)。真实模型效果需另行验证。
