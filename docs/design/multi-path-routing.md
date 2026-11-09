# 多路径择优路由（GUI / CLI / API）

2026-11-09 按当前源码（`computer_use_agent` 基于 openJiuwen DeepAgent）提出。此页记录结构、依据与约束，不承诺真机效果；实现见 `computer_use_agent/routing/`。

## 目标

同一个用户任务，存在多种完成通道：engine 已封装好的 SDK 结构化操作（**API 路径**，如 `Element.type_text(replace=True)`、`Element.read_text()`、树读取）、外部应用 HTTP API、shell 命令（**CLI 路径**）、坐标/截图交互（**GUI 路径**，本项目 `UI.click(at=…)`、`read(view="image")` 一类）。路由层要在任务级做一次显式选择：

- 每条候选路径带 **时间、成本、成功率** 等指标（在线学习，持续修正）；
- API 可以注册**多条**路径（同一意图的不同 SDK 路线 / 不同端点 / 不同粒度），各自独立累计指标；
- **GUI 是兜底**：无合格候选或指标不足时落到 GUI；但 agent 也可以选择**拒绝**（说明理由）或**拆分**为若干子任务分别路由。

## 业界方案调研

| 方案 | 关键机制 | 可借鉴点 |
| --- | --- | --- |
| [Simular Agent-S2](https://arxiv.org/html/2504.00906v1) | Proactive Hierarchical Planning + Mixture of Grounding：执行时按运行时证据在"直接 GUI 操作"与"代码块执行"间动态选择（Hybrid Mode）；经验知识库沉淀 | Strategy 显式分支 + 运行时证据选择；经验沉淀为指标 |
| [vercel-labs/agent-desktop](https://github.com/vercel-labs/agent-desktop) | 无内置模型的 CLI：ref/快照契约、错误带 `disposition.retry`、"loop lives in your agent" | 路径契约、失败分类与校验留在后端；agent 只做决策 |
| [UI-TARS-desktop](https://dev.to/wonderlab/one-open-source-project-a-day-no-62-ui-tars-desktop-bytedances-open-source-multimodal-gui-53pm) | 浏览器侧 GUI / DOM / Hybrid 三条策略动态选择；Event Stream 全程可回放 | 同域多路径并存、运行时择优；轨迹即指标数据源 |
| [Microsoft《API Agents vs. GUI Agents》](https://arxiv.org/html/2503.11069v2) | 定量对比：GUI+API 混合执行成功率 +6.1%/+8.2%，步数 -6.5%/-58.5%；给出 API/GUI/混合的选择判据 | 确定性优先 + GUI 兜底的量化依据 |
| [Qwen-UI-Agent](https://arxiv.org/html/2607.28227v1) | 统一 GUI+CLI action space，模型在同一空间交错选择，CLI 占 40~55% 动作 | 多通道统一调度而非硬编码优先级 |
| [AppWorld](https://github.com/StonyBrookNLP/appworld) | API-first 世界：代码交互 + 程序化 ground-truth 校验 | API 路径的结果可程序化校验，成功率可精确统计 |
| [Mobile-Agent-E / UFO2](https://www.emergentmind.com/topics/hybrid-gui-api-action-space) | 系统级 API 与 UI 动作混合；双检测流融合 | 能力检测（API 可用性）是路由的前提证据 |

综合业界结论与"确定性优先路由"范式（Rule > API > CLI > GUI > free-form），本设计：**候选路径按运行时证据打分择优，而非固定优先级；GUI 永驻兜底；拒绝与拆分是一等决策**。

## 路径模型

`TaskPath`（`routing/paths.py`）：

- `kind`：`api` / `cli` / `gui`。`api` 与 `gui` 的 engine 路线都以 SDK 代码模板（`SdkSpec.code_template`，`{参数}` 占位）经 `computer_use` 工具在同一个 Python 会话执行——**单一执行主干不变**，路由只决定"跑哪段代码 / 哪个命令 / 哪个端点"。区分在于语义：api 路线使用元素级结构化操作（可声明预期效果），gui 路线使用坐标/视觉操作（通用、概率性）。
- 执行端点三选一：`api`（`ApiSpec`: method/url/headers/timeout）、`cli`（`CliSpec`: argv 模板 + 前置二进制）、`sdk`（`SdkSpec`）。`gui://fallback` 是隐式注册的兜底子任务路径，代表"模型自行 computer_use"的原始行为。
- `arguments`：JSON Schema，路由执行前校验参数。
- `risk`：`read` / `write` / `destructive`。destructive 默认需确认回调放行。
- `preconditions`：声明式前置（如 CLI 二进制存在、SDK 路线要求某 app 在运行）。前置不满足的候选直接不可选（eligible=false），不参与打分。
- `verify`：校验契约（见下）。缺校验的路径执行后 verdict 为 UNKNOWN，**不得伪造成功**。
- `granularity`：`route`（一次执行=一次任务尝试，注册路线的默认）/ `operation`（每次 computer_use 调用=一个样本，兜底的 `gui://fallback` 用此粒度累计操作可靠性）。

## 指标体系

`PathMetricsStore`（唯一指标所有者，`routing/metrics.py`）：

- **成功率**：Beta 后验均值 `(successes+α)/(attempts+α+β)`，先验 α/β 按 kind 配置（api 偏高、gui 偏低的冷启动偏差），用真实执行逐步修正；效果未知的尝试（超时/取消后 effect=unknown）**不计入**后验更新。
- **时间 / 成本**：EMA 滚动均值；无样本时回退到路径声明的 `time_hint` / `cost_hint`。GUI 兜底的任务耗时按"操作样本 × 每任务操作数估计（`gui_ops_estimate`）"折算。
- **探索奖励**：UCB1 风格 `c·sqrt(ln N / n)`，鼓励在得分相近时试探样本少的路径，防止冷启动路径永无数据。
- 持久化：JSONL 追加（可选路径），进程重启后指标延续。

打分（`routing/scorer.py`，纯函数）：

```
score = p_success − w_time·min(t̂,1) − w_cost·min(ĉ,1) + exploration
t̂ = 预计时延 / time_scale，ĉ = 预计成本 / cost_scale
```

权重与尺度均可在 `RoutingConfig` 配置。

## 决策与执行管线

`Router`（`routing/router.py`）一次路由：

1. **Discover**：`registry.query(task, app)` 按 app/关键词取候选 + 隐式 GUI 兜底。
2. **Filter**：enabled、参数 Schema 可校验、前置满足、风险策略（destructive 无确认则剔除）。
3. **Score**：纯打分，产出 `RouteCandidate(score, breakdown)`，breakdown 展示成功率/时延/成本各项，模型可解释。
4. **Decide**：
   - 最优分 ≥ `min_confidence` → **EXECUTE** 该路径；
   - 否则有兜底 → **EXECUTE gui://fallback**（模型继续 computer_use）；兜底自身后验也低于地板且尝试数足够 → **REFUSE**（给出理由与证据，模型向用户说明而非硬操作）；
   - 调用方传入 `subtasks` → **DECOMPOSE**：每个子任务独立走完整管线，顺序执行，首个失败即停止并保留已完成子结果。
5. **Execute**：按 kind 分派执行器（`routing/executors.py`）——API 走 stdlib urllib（线程内同步调用）、CLI 走 asyncio 子进程（argv 直传，无 shell）、SDK/GUI 走 `computer_use` 工具。超时/取消按项目原则**不重放**，结果记为 effect 未知。
6. **Verify**：按契约校验（http_status / exit_code / output_contains / output_regex / json_field；gui_observe、llm_judge 为扩展口）。UNKNOWN 保留不确定性。
7. **Record**：`OutcomeRecord` 写回指标库；工具层对非路由发起的 `computer_use` 调用按 operation 粒度自动采样到兜底路径。

## 集成

- `AgentConfig.routing: RoutingConfig`（开关、权重、先验、paths/metrics 文件、风险策略）。
- 新增一个模型工具 `route_task(task, app?, arguments?, subtasks?)`（`ComputerUseToolkit`）：返回排序后的候选与指标明细、决策与理由；决策为 EXECUTE 时直接执行并回传结果与校验结论。刻意保持单工具最小接口。
- `computer_use` 调用计时并自动累计到兜底指标（路由发起的调用去重）。
- 系统提示增补：何时调用 route_task、gui_fallback / refuse / decompose 各自如何继续。

## 失败语义与安全

- 执行结果区分 `completed` / `failed` / `cancelled` / `effect_unknown`；effect 未知时禁止换路重放（与 AGENTS.md 一致）。
- CLI 永不经过 shell 解释器；API 永不带注册之外的隐式凭据（headers 显式声明）。
- shared 桌面模式下，路由只影响"用哪条通道"，不突破桌面模式与用户操作范围约束；engine 层的前台/后台规则不受影响。

## 测试

- 纯单元：scorer 排序、metrics 后验/EMA/持久化、registry 加载与 OpenAPI 导入、verifiers 各契约、executors（本地 HTTP server / echo 命令 / 超时）、router 全部决策分支。
- 集成：`route_task` 工具经 fake model server 端到端；CLI 路径用真实子进程，不依赖桌面。
- 故障机制回归：效果未知不重放、destructive 需确认、缺校验不伪造成功、拆分首个失败即停。
