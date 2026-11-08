# 科研 Agent

`literature.py` 保存科研问答的 LangGraph 图及结构化模型输出：主控动态选择直接回答、研究委派、审查或报告；论文和网页研究各有独立子图，报告子图负责综合、核验及至多一次局部修订，缺证据可反馈主控补研。入口为 `run(research, *, history, on_stage=None)`，返回回答正文与证据。

`research` 包含 `query`、`project_id`、`source`，`auto` 由主控自主选择直接回答、论文或网页，审查缺口后可在论文与网页之间切换，可带 `paper_ids`、`model_name` 与 `reasoning_effort`；`history` 是调用方准备的对话消息列表。Agent 不需要任务 ID，不直接导入仓储或修改任务状态。可选 `on_stage(stage)` 在节点执行前接收阶段名；worker 负责读历史、提供回调、领取任务及原子提交结果。

```python
from src.agents.literature import run

content, evidence = run(
    {"query": "attention", "project_id": 1, "source": "local"},
    history=[],
    on_stage=print,
)
```

调用示例需已有项目与有效模型配置，省略回调同样可以执行。阶段包括 `planning`、`answering`（一般回答）、`searching`（论文）、`searching_web`（网页）、`evidence`、`assessing`、`synthesizing` 与 `verifying`；研究、审查与主控可以循环。阶段持久化错误由外层任务运行器处理，不在图节点中吞掉。

检索与取证按名称调用 [工具目录](../tools/README.md)，实际算法与限制见 [文献业务](../literature/README.md)。状态中的 `candidates` 保留本次运行的完整去重候选池，精读只允许模型从当前候选中按稳定 ID 选择最多六篇。按不同查询排名进行 RRF 排序，稳定处理平局；补检新论文可以替换低排名旧论文，同一查询重复执行不会重复加分。该排序不是模型语义重排。

最多委派三次研究，每次至多调用六次工具。模型可多次搜索、精读或结束；每次精读用委派子问题取证，按来源、定位和内容去重后加入 `evidence_pool`。旧论文退出精读集合时，其证据仍留在池中。同一材料重新入选不计作新增，无新增证据的路径暂停，其他可用路径仍可补研。

图状态中的 `evidence` 是本轮给缺口评估和综合使用的工作集，最多 18 条，正文合计最多 21,600 字符。筛选先为原问题和各轮子问题保留最相关的可用证据，再按关键词匹配填充剩余位置。不同片段的引用 ID 在运行中保持唯一。原始问题一直保存在 `research.query`。最终回答只保存被引用且支持通过核验结论的证据。

收藏夹任务传递提交时保存的项目、来源和论文快照，不读取其他对话历史；网页范围强制进入研究子图，不走无证据的一般回答；子图可直接结束并返回证据不足。模型节点的输入输出与 `src/prompts/` 模板契约见 [提示词规范](../../docs/PROMPTS.md)。

主控不调用检索工具，研究结果返回主控；报告前必须完成审查。无进展或预算耗尽时收束并说明未解决的不足。模拟 HTTP 与实际 API/worker 验收入口见 [根 README](../../README.md#验证)。

网页专家可分页读取公网网页正文，取证保留 Markdown 字符范围。报告核验的 unsupported 坐标和原因写入私有诊断，普通日志仅记录诊断 ID 和数量。
