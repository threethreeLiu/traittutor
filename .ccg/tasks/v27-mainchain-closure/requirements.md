# PRD v2.7 主链完整闭环：结构化需求

> 权威来源：`docs/PRD.md` v2.7、`docs/CODING-PLAN.md`、已接受 ADR-0001..0007。
> 本任务不把既有薄切片、单元测试或默认关闭的 feature flag 当作功能完成证据。

## 成功定义

1. **统一入口与可恢复对话（F-01/F-02/WS-5）**：同一 authenticated session 在安全检查之后能稳定写入 owner-bound ConversationThread/Turn，重放不双写，分支不改写历史；授权的最小 Thread/Episode 切片可进入后续 ContextSnapshot。风险输入零副作用，普通阅读/搜索不更新 BKT。
2. **唯一学习事实与状态（F-09/F-10/WS-10）**：所有服务端可判分入口先写 canonical event，再由同一版本 BKT/错误/复习 reducer 投影；同一事件流在线投影与重建结果一致。答案/rubric 不在客户端；弱归因、自评、阅读和搜索不更新 BKT；更正/删除可触发可追踪重建。
3. **可追溯的生成上下文（F-06/F-07/F-08）**：快照只含最小授权 refs；新 Prompt 可回查 memory/thread/research/subject/persona refs，并在删除、停用或更正后重建；DAG、PageSchema 和降级/重放保持可恢复与不重复计费。
4. **研究、记忆与 Persona（F-03/F-04/F-11/F-12）**：Research 的来源、Brief、Run、暂停/恢复/重试、报告和转课件 provenance 都有 owner-bound 真实链路；memory 的跨域授权/删除重建与 L3 provenance 不降级；Persona 只影响表达且真正进入允许的生成上下文；Why surface 展示脱敏读取与状态。
5. **模型边界与运行可观测性（F-13/WS-7/WS-13）**：产品模型调用经支持 complete/stream/tools 的 Gateway，路由、重试、fallback、receipt 与敏感信息边界一致；生产 telemetry 有异步 sink、聚合、阈值与告警，不写 prompt/答案/密钥或高基数 label。
6. **发布门（PRD §11）**：双用户/双学科隔离、幂等、服务端持答案、PageSchema 白名单、隐私/来源、失败降级、前后端 build/lint/E2E 均有直接证据。未经校准的 BKT 或未接通的生产依赖不得被标为 enable-ready。

## 已确认未完成项

- 已完成的 A/B/C/D 收口：online `ConversationStore` 写入与最小 Episode retrieval；canonical live/rebuild BKT 参数一致、repair retry、target-bound void amendment/effective rebuild 和主要 server-held grading 投影；浏览器 Quiz/Judge 不再接受答案键；typed Gateway complete/stream/tools contract；Persona style-only Context handoff；Research KB adapter、worker lease/control、source invalidation 与安全 generation queue。经验证的 ResearchRun provenance 现以最小 typed ref 进入 ContextSnapshot 与 CoursewarePromptBundle，并参与哈希；URL、标题、报告正文与 claims 不进入这些引用。
- `CapabilityDecision` 已有风险输入零副作用与确认门；确认 **Research** 会幂等创建 Workspace→Brief→queued Run，确认 **Learn** 会幂等创建 owner-local Pack/Plan/Journey；确认 **Create** 会在 Home 显式重收 generation goal 与 typed material、二次扫描后，以 owner-bound 幂等 courseware task 执行。缺字段本地零请求，422 留在弹窗内可修正重试。
- L3 仍只有 dry-run/checkpoint；没有真实 legacy 生产迁移、回滚演练与生产数据证据。
- Research 仍缺跨-owner durable dispatcher、真实 provider E2E；内部 provenance 接线已闭环，但真实环境来源/费用/失败恢复仍须验收。
- Persona 的主动提醒授权/静默时段和 legacy persona 迁移未完成。
- Gateway 的 mounted Chat WebSocket、Question/Judge、unified AgenticChatPipeline 与 DeepResearch tool-loop、Research Workspace executor complete/stream-buffer、post-turn session-title 与 Notebook summary 均有 default-off typed consumer；Research stream-buffer 只在 final 后解析并提交，tool/cancel/late/failure 一律 durable executor_failed，不回退 complete。session-title/Notebook 只投影 text，Agentic/DeepResearch 保留 server-only tool dispatch、ask-user resume/typed image 与 citation replay，并且 Gateway 异常或取消不写不完整公开内容且不回退 legacy。统一 WebSocket 的 subscribe/cancel/submit 等 turn 操作已从 turn 反查 session/认证 owner，并在 runtime/SQLite/PocketBase 复核；Gateway 已严格序列化/验证 assistant `tool_calls`→`role=tool` 轮次协议且不泄露参数。`TRAITTUTOR_GATEWAY_GENERATION_ROUTE_POLICY` 已将 structured generation 的可选路径限制为主路由+一个备路由、每 route 两次、Gateway 单次 provider retry 与 180s 总 deadline，并写 aggregate-safe attempt/failover/outcome/duration telemetry；显式文件路径可启用 file-locked 跨进程 circuit/cooldown。Telemetry 默认 NoOp，但可显式启用持久化低基数聚合 sink、脱敏阈值 hook、credential-free HTTPS webhook、PagerDuty Events v2 和 STARTTLS SMTP adapter；Gateway complete/stream 聚合 provider `total_tokens`，并在 deployment-owned exact-model 定价表命中时汇总 `total_cost_picousd`。仍缺真实价格/通知路由与真实 provider 演练。
- BKT 仍缺参数校准、持久化 canonical read-model cache 与全部 legacy consumer 收敛；无 source 的历史 Review 只能安全标 `needs_rebuild`，不能被精确撤回。
- Pack 已可经 owner-derived API 绑定既有、confirmed-subject 的 `LearningProgress` module graph，并以不可变 revision 保存 path/subject/allowed KC/图版本。新 Pack quiz/component/repair/review 仅在服务端 item 或 confirmed plan 与该 binding 精确匹配时投影；历史 `pack_id`-as-path 仍保持 pending。Mastery chat 现以当前 owner 的既有 confirmed-subject path 铸造并持久化 owner/path/subject/KC-graph fingerprint binding，每次工具调用重新验证；Home 的 owner-bound picker 仅返回安全路径摘要/`mastery_ready`，提交仅含 `learning_path_id`；缺失或 stale 仍是 unknown/no BKT。

## 非协商不变量

- 事件先于派生；只有 server-graded + valid item + reliable KC 才更新 BKT。
- user + subject + kc 隔离；跨域记忆须显式授权并可审计。
- 生成 Agent 对 Memory/BKT/Error/Review/Persona 只读；答案/rubric/未激活提示不出服务端。
- 同 event/attempt/run 重放不双写、不重复派生、不重复计费；失败与降级必须诚实可见。
- Persona 只影响表达；Research 外部主张必须有可点击来源；PageSchema 不可执行。
