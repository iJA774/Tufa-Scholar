# 科研提示词

当前提示词覆盖问题规划、一般问题回答、补检判断、综合与结论核验；模型输入中的问题、历史和检索材料均作为数据处理。提示词正文只存放在 Markdown 文件中。

五个模板均由 `src/agents/literature.py` 经 `structured()` 从 `src/prompts/literature/` 实际加载，prompt ID 保持 `literature/模板名`，在每次请求前替换开头的当前时间。字段映射、北京时间约定与验证方式见 [提示词规范](../../../docs/PROMPTS.md)。真实模型效果由用户另行验证。
