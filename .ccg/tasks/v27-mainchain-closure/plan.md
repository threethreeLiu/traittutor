# CCG Phase 3：PRD v2.7 主链完整闭环计划

## 波次与依赖

| 波次 | 范围 | 直接验收 | 依赖/边界 |
|---|---|---|---|
| A（完成） | WS-5 online conversation bridge | session→thread 稳定映射、user/assistant turn 幂等写入、风险输入零副作用、owner/branch 隔离、最小 episode read | 不写 BKT；不改 protected UI/生成文件 |
| A（完成） | WS-10 canonical convergence | 唯一 BKT parameter/version source；repair retry 事件先行；live/rebuild posterior 一致 | 不伪称 calibration；弱 provenance 只写 pending/no BKT |
| B（完成主要入口） | WS-10 canonical derived state + server-held Quiz | 主要判分入口投影 Error/Review；浏览器无答案键；target-bound void amendment 后 effective-stream rebuild | 全部 legacy consumer 与精准历史 Review provenance 仍未完成；必须复用 LearningService/ledger |
| B（Pack 与 Mastery 路径闭环完成） | Pack repair/review 与 mastery-path identity closure | owner-derived API 将 Pack 追加式绑定到既有 confirmed-subject `LearningProgress` module graph；Mastery chat 也仅能从当前 owner 的既有 confirmed-subject path 铸造 binding；quiz/component/repair/review/Chat 仅在 server subject/KC 与 active binding 精确匹配时投影，弱/未绑定路径不更新 BKT | **不得**用 `pack_id`、session 或模型输出猜 LearningProgress。历史无 binding 事件维持 pending；Mastery 无绑定时为 unknown/no event/no BKT |
| C（完成主要用户/生成子链） | F-01/F-06 consumer closure | CapabilityDecision、风险输入零副作用、Research confirmation→Workspace/Brief/Run、Learn confirmation→Pack/Plan/Journey、Home Create confirmation→严格二次扫描的 owner-bound courseware task；经验证的 ResearchRun typed ref 已进入 ContextSnapshot 与 PromptBundle/hash；thread/memory/persona/subject refs 进入授权 Context | 完整 prompt consumer、跨域检索与真实环境验证仍未完成 |
| D（契约及主要消费者完成） | WS-7 Gateway | typed complete/stream/tool/receipt；BaseAgent non-stream first；Quiz Judge、mounted Chat WebSocket、unified AgenticChatPipeline 与 DeepResearch tool-loop、Research Workspace executor complete/stream-buffer、post-turn session title 与 Notebook summary 均为独立 default-off Gateway consumer；structured generation 另有默认关闭的有界 route policy、aggregate-safe attempt/failover telemetry 与显式 cross-process circuit；Gateway complete/stream 保留 provider 真实 aggregate token 总量，explicit exact-model price 可汇总 pico-USD 成本；真实 provider courseware smoke 通过 | 真实价格表、feature-flag rollback 与持续性 provider 演练 |
| E | WS-8/11/12/13 production closure | L3 controlled migration runbook、KB adapter/worker/Run→courseware, Persona migration/reminders, telemetry sink/alerts | 真实 provider、生产数据与通知通道须以真实环境验收，不能伪造 |
| F（部分完成） | 发布验证 | two-user/two-subject, replay/recovery, security, build、隔离认证关闭服务上的 serial browser E2E（30/30）、真实 provider courseware smoke（1/1，123s） | feature-flag enable/rollback 与费用/延迟演练后才评估 enable-ready |

## 当前实现顺序

1. 对 Gateway 的 DeepResearch capability 与 structured-generation route policy 做真实 provider 的启停/失败/延迟和成本演练；保持所有 Chat/Judge/AgenticChat/Research-complete/stream/session-title/Notebook-summary flag-off 回滚，并把 telemetry 接到真实告警 adapter；不暴露 prompt、答案、密钥和高基数标签。Gateway provider token 总量与 exact-model pico-USD 成本现可聚合落盘，但真实价格表/路由尚未验收。
2. Gateway agentic-loop 已完成 default-off 迁移：unified WebSocket 的 `subscribe/cancel/submit`、resume/regenerate/user-input 及带 session 的 start 已校验 turn→session→authenticated owner，运行时内存队列与 SQLite/PocketBase 持久化访问均复核；Gateway 保留并严格验证 assistant `tool_calls`→`role=tool` 关联，参数不进 receipt/telemetry。AgenticChatPipeline ON 路径保留 server-only tool dispatch、ask-user pause/reply 与 typed image content；Gateway cancel/timeout/error 不回退 legacy，非 typed-tool provider 明确 fail-closed。
3. 已建立 Mastery chat 受信 subject binding：只接受现有 owner-bound、confirmed-subject 的 LearningProgress 与持久化 KC 图；继续把同样的 attribution contract 用于其余 legacy consumer，绝不从 session、Pack 或模型输出推断。
4. 在真实生产数据/凭据允许时执行 L3 migration rollback、provider E2E 与 calibration；此前不得标记 enable-ready。
5. 每波均按 CCG `verify-module → verify-security → verify-change → verify-quality`，并进行全套 test/build/serial E2E 结算。

## WS-10 仍未收敛的精确合同（2026-08-10 审计）

1. **Pack path 绑定已完成，但不对历史数据作猜测回填。** `pack_id` 不是 `LearningProgress.book_id`；现已新增 server-authored、追加式 revision 的 Pack→LearningPath binding，链接时从当前 owner 的既有 confirmed-subject `LearningProgress` 重建 subject/KC graph contract。assessment 只在 server-held subject/KC 与 active binding 精确一致时写该 path；repair/review/retry 只复制相同 path 的强 source。既有无 binding 的 repair 保持可审计且 `needs_rebuild`，不能回填猜测。
2. **Mastery-path chat 已有可信 subject binding 与 picker。** Home 只列出当前 owner 的安全路径摘要及 `mastery_ready` 位，用户只能提交 `learning_path_id`；runtime 从既有 `LearningProgress` 读取 nonempty confirmed subject + 唯一 KC graph，再持久化 server-authored owner/path/subject/fingerprint binding；每个 Mastery tool 重载并复验该 binding。缺失、跨 owner 或 graph stale 均返回 unknown/no event/no BKT；模型不可创建/替换已绑定的 KC graph，不能从 chat payload、session ID 或模型生成的 module 名推断。

## 每波次必须输出的证据

- 用户任务、状态转换、失败路径和 zero-side-effect 路径的测试。
- owner/subject/KC 隔离、稳定 ID 重放和 stale/CAS 拒绝测试。
- `ruff check`、changed-file format、后端相关测试；涉及 web 时 TypeScript/lint/build/Playwright。
- 新模块的 README/DESIGN；变更后同步 `CODING-PLAN.md`/`PROGRESS.md`，只陈述已证明事实与剩余边界。
