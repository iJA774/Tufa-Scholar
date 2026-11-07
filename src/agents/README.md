# 科研 Agent

`literature.py` 保存科研问答的 LangGraph 图及结构化模型输出：规划后进入一般回答或检索取证分支，随后进行有限补检、综合和核验。入口为 `run(research, *, history, on_stage=None)`，返回回答正文与证据。

`research` 包含 `query`、`project_id`、`source`，可带 `paper_ids`、`model_name` 与 `reasoning_effort`；`history` 是调用方准备的对话消息列表。Agent 不需要任务 ID，不直接导入仓储或修改任务状态。可选 `on_stage(stage)` 在节点执行前接收阶段名；worker 负责读历史、提供回调、领取任务及原子提交结果。

```python
from src.agents.literature import run

content, evidence = run(
    {"query": "attention", "project_id": 1, "source": "local"},
    history=[],
    on_stage=print,
)
```

调用示例需已有项目与有效模型配置，省略回调同样可以执行。阶段依次包括 `planning`、`answering`（一般回答）或 `searching`、`evidence`、`assessing`（科研分支，可循环），最后是 `synthesizing` 与 `verifying`。阶段持久化错误由外层任务运行器处理，不在图节点中吞掉。

检索与取证按名称调用 [工具目录](../tools/README.md)，实际算法与限制见 [文献业务](../literature/README.md)。状态中的 `candidates` 保留本次运行的完整去重候选池，`papers` 只保存排序后本轮最多六篇精读对象。按不同查询排名进行 RRF 排序，稳定处理平局；补检新论文可以替换低排名旧论文，同一查询重复执行不会重复加分。该排序不是模型语义重排。

首轮按原始研究问题取证，最多再补检两轮。每轮用当前子问题检索并从本轮精读集合取证，按来源、定位和内容去重后加入 `evidence_pool`。旧论文退出精读集合时，其证据仍留在池中。同一材料重新入选不计作新增，补检没有新增证据时停止。

图状态中的 `evidence` 是本轮给缺口评估和综合使用的工作集，最多 18 条，正文合计最多 21,600 字符。筛选先为原问题和各轮子问题保留最相关的可用证据，再按关键词匹配填充剩余位置。不同片段的引用 ID 在运行中保持唯一。原始问题一直保存在 `research.query`。最终回答只保存被引用且支持通过核验结论的证据。

收藏夹任务传递提交时保存的项目、来源和论文快照，不读取其他对话历史；网页范围强制检索，不走无证据的一般回答。五个模型节点的输入输出与 `src/prompts/` 模板契约见 [提示词规范](../../docs/PROMPTS.md)。

图仍按既有分支执行，尚未加入模型自主选工具的循环。模拟 HTTP 与实际 API/worker 验收入口见 [根 README](../../README.md#验证)。
