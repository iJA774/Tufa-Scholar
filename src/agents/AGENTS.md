# Agent 工作流约定

- `literature.py` 维护单张八节点科研问答图：dispatcher、background_investigator、planner、supervisor、researcher、analyst、coder、reporter。supervisor 只按 researcher → analyst → coder → planner 的顺序分派；coder 为不调用模型的占位节点。研究流程最多两轮。
- dispatcher 负责直接回答或进入深研；collection 由代码强制深研。background_investigator 先实搜并筛选背景候选，最多三条查询。planner 首轮必须研究，审查后可以补查、切换获准来源或报告；最多两轮后必须调用 reporter。
- researcher 只能使用本节点目录中的来源匹配工具，最多八个工具动作。搜索失败可在预算内用改写查询重试；不得构造项目范围、候选 ID 或不在当前候选池中的网页 URL。
- reporter 对深研流程始终生成终稿，不以审查充分或有证据作为是否调用的门槛。代码负责引用 ID、来源范围、数值、单位和成本缺口校验；证据不足时明确保留局限，不编造事实。
- 模型节点只使用 `src/prompts/` 中六个真实模板。调用前核对 payload、Pydantic schema、`search_mode`、`available_tools` 和 `local_papers`；受信系统模板才经 Jinja2 渲染，用户输入、history、候选与证据作为数据单独传入。同步维护 [提示词规则](../prompts/AGENTS.md)。
- 候选池按 web/paper 分开；不同查询排名以 RRF 合并，同查询不重复加权，入池前不能按精读上限截断。累计证据跨轮保留并保证本次运行内引用 ID 唯一；每个模型输入遵循条数与文本长度上限。
- 研究问题、history、项目和来源范围作为输入；Agent 不接收数据库任务行、不查询任务历史、不修改任务状态。collection 忽略外部 history，调用方传入提交时论文快照。阶段和研究小结通过回调通知运行器，worker 与仓储负责生命周期及持久化。
- 检索和取证经 [工具入口](../tools/AGENTS.md) 调用，算法见 [文献模块](../literature/AGENTS.md)。修改图节点时验证 direct、证据不足、服务失败、来源切换、两轮上限和 reporter 收束路径；使用 [工作流测试规则](../../tests/AGENTS.md) 区分模拟与真实服务验收。
