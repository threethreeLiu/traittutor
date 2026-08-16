# CCG Phase 3：缺失 PRD 功能实施计划

## 采用方案

采用 **canonical 垂直切片**：服务端 truth source 和公开安全 DTO 先行，再接 typed client、页面状态机、导航和 E2E。现有 Research pipeline、legacy memory workbench、自由文本 Persona 与 LearningPack repair/review 仅作为 adapter 或迁移来源，不能被新页面直接当作 canonical 功能。

本任务完成此前页面审计识别的 5 个缺口及其最小可信服务闭环；不宣称 WS-7 全量收敛、WS-13 生产观测、BKT 校准或整个 v2.7 production-ready。

## 受保护的当前改动

实施前记录并持续核对以下文件的 diff；本任务不修改、不格式化：

- `traittutor/generate/service.py`
- `web/components/learning/LearningCanvas.tsx`
- `web/components/personalization/WhyThisGeneration.tsx`
- `web/lib/traittutor-api.ts`
- `tests/learning/test_adaptive_wiring.py`

`.ccg/tasks/demo-readiness-assessment/` 与 `.ccg/tasks/v27-overnight-sprint/.turns.json` 属于现存工作区内容，不纳入本任务提交。

## Wave 0：基线与模块文档

1. 保存 `git status --short` 与保护文件 diff 基线。
2. 为新模块运行 CCG `/gen-docs`，生成并补齐 `README.md`、`DESIGN.md`：
   - `traittutor/learning_governance/`
   - `traittutor/tutor_persona/`
   - `traittutor/research_workspace/`
3. 在 DESIGN 中冻结公开 DTO、状态迁移、owner 隔离、幂等键和答案/Prompt 隐藏边界。
4. 先写 contract tests，确保页面开发不会反向定义后端真相。

## Wave 1：后端 contract/store（Layer 1，可并行）

### 1A Learning Governance

新建：

- `traittutor/learning_governance/__init__.py`
- `traittutor/learning_governance/models.py`
- `traittutor/learning_governance/repository.py`
- `traittutor/learning_governance/service.py`
- `tests/learning_governance/test_models.py`
- `tests/learning_governance/test_repository.py`
- `tests/learning_governance/test_service.py`

职责：

- 聚合 `LearningStore` 中的 `ErrorRecord/ReviewTask`、canonical LearnerEvent、持久 Misconception 和 Pack repair/review 引用。
- 只按认证 owner + 显式 subject/KC 查询；无法权威归因时返回 `attribution_pending`，不猜测。
- 定义 learner-safe `ErrorSummary`、`MisconceptionSummary`、`RepairSummary`、`ReviewSummary`；禁止 answer/rubric/correct_rule/raw prompt。
- review result 复用 canonical event chain；重复 `event_id/attempt_id` 不重复更新 BKT，弱证据不更新 BKT。
- `MisconceptionStore` 增加 owner-bound durable 模式；旧内存模式仅保留兼容测试。

### 1B Canonical Memory + WS-8

新建：

- `traittutor/memory/api_models.py`
- `traittutor/memory/management.py`
- `traittutor/memory/l3_projection.py`
- `traittutor/memory/l3_store.py`
- `traittutor/memory/l3_migration.py`
- `tests/memory/test_management.py`
- `tests/memory/test_l3_projection.py`
- `tests/memory/test_l3_migration.py`

最小修改：`traittutor/memory/store.py`。

职责：

- candidate activate/reject、conflict/supersede、deactivate/delete、grant/revoke、access record 和 rebuild 状态。
- 删除先让 canonical recall 立即不可见，再失效 L3/index，异步 rebuild 只能写当前 generation。
- L3 Markdown 只是 projection；typed sidecar 保存 source IDs/refs、观察时间、subject、confidence、version/hash。
- legacy 文本一律 `legacy_unverified`；dry-run/checkpoint/rollback 可重放，禁止用相似度伪造来源。

### 1C Typed Tutor Persona

新建：

- `traittutor/tutor_persona/__init__.py`
- `traittutor/tutor_persona/models.py`
- `traittutor/tutor_persona/compiler.py`
- `traittutor/tutor_persona/store.py`
- `traittutor/tutor_persona/service.py`
- `traittutor/tutor_persona/context_adapter.py`
- `tests/tutor_persona/test_store.py`
- `tests/tutor_persona/test_compiler.py`
- `tests/tutor_persona/test_expression_invariance.py`

职责：

- frozen/versioned whitelist profile，CAS `expected_version`，重复 idempotency key 返回原版本。
- deterministic compiler 只输出称呼、语气、解释密度、反馈格式、主动程度、语音/可访问性等表达契约。
- schema 和 compiler 都不能表达 grading、answer、BKT、安全覆盖或任意 system prompt。
- differential tests 证明换 Persona 时判分、答案、BKT 和安全输入逐项不变。

### 1D Research Workspace

新建：

- `traittutor/research_workspace/__init__.py`
- `traittutor/research_workspace/models.py`
- `traittutor/research_workspace/state_machine.py`
- `traittutor/research_workspace/store.py`
- `traittutor/research_workspace/service.py`
- `traittutor/research_workspace/source_validation.py`
- `tests/research_workspace/test_store.py`
- `tests/research_workspace/test_state_machine.py`
- `tests/research_workspace/test_fencing.py`

职责：

- durable `Workspace/Brief/Run/TaskReceipt/Source/Note/Claim/Report`，Brief 版本冻结。
- `draft/queued/running/pausing/paused/cancelling/cancelled/completed/failed/needs_review` 合法迁移表。
- revision CAS、run idempotency、lease/claim token、fencing epoch；终态不能被迟到结果复活。
- 外部 claim 必须引用 source；模型推断明确标记 inference，不能冒充检索事实。

## Wave 2：Router、执行适配与 typed clients（Layer 2）

### 2A 后端 routers

新建：

- `traittutor/api/routers/learning_governance.py`
- `traittutor/api/routers/canonical_memory.py`
- `traittutor/api/routers/tutor_persona.py`
- `traittutor/api/routers/research_workspace.py`
- `tests/api/test_learning_governance.py`
- `tests/api/test_canonical_memory.py`
- `tests/api/test_tutor_persona.py`
- `tests/api/test_research_workspace.py`

最小修改 `traittutor/api/main.py` 注册：

- `/api/v1/errors`、`/api/v1/misconceptions`、`/api/v1/reviews`
- `/api/v1/memories/*`（与 legacy `/api/v1/memory/*` 分离）
- `/api/v1/tutor-personas/*`（与 legacy `/api/v1/personas/*` 分离）
- `/api/v1/research/workspaces/*`

Router 从 `get_current_user()` 派生 owner；请求不接受 `user_id`。跨 owner object 统一 404。公开响应通过显式 DTO 构造，禁止内部对象直接 dump。

### 2B Research executor/worker

新建：

- `traittutor/research_workspace/executor.py`
- `traittutor/research_workspace/worker.py`
- `tests/research_workspace/test_executor.py`

现有 `agents/research` 仅作为 executor adapter；所有模型调用走现有 Gateway，不增加 provider 直连。先持久化状态与 receipt，再发布 progress；pause/cancel/recovery 全部使用 fencing。

### 2C 前端 typed clients

新建：

- `web/lib/research-workspace-api.ts`
- `web/lib/canonical-memory-api.ts`
- `web/lib/tutor-persona-api.ts`
- `web/lib/learning-governance-api.ts`
- `web/lib/mastery-display.ts`

共同规则：只走 `apiUrl/apiFetch`；不传 owner；支持 AbortSignal、409/version conflict 和未知状态 fail-closed；不依赖受保护的 `traittutor-api.ts`。

## Wave 3：页面状态机（Layer 3）

### 3A Research

新建：

- `web/app/(workspace)/research/page.tsx`
- `web/app/(workspace)/research/loading.tsx`
- `web/app/(workspace)/research/error.tsx`
- `web/app/(workspace)/research/[workspaceId]/page.tsx`
- `web/components/research/ResearchWorkspaceIndex.tsx`
- `web/components/research/ResearchWorkspaceApp.tsx`
- `web/components/research/ResearchBriefEditor.tsx`
- `web/components/research/ResearchRunPanel.tsx`
- `web/components/research/ResearchEvidencePanel.tsx`

覆盖 create/open、Brief revision、start/pause/resume/cancel/retry、来源/笔记/报告；刷新后以 REST store 恢复状态，progress 仅作即时展示。

### 3B Memory

新建：

- `web/app/(utility)/profile/memory/{page,loading,error}.tsx`
- `web/components/memory/CanonicalMemoryManager.tsx`
- `web/components/memory/MemoryCandidateList.tsx`
- `web/components/memory/MemoryConflictDialog.tsx`
- `web/components/memory/MemorySourceDetails.tsx`

覆盖筛选、confirm/reject、冲突显式 supersede、deactivate/delete、来源/范围、legacy_unverified 与 rebuild 状态。删除对话框复用具备 focus trap 的 Modal。

### 3C Tutor Profile

新建：

- `web/app/(utility)/profile/tutor/{page,loading,error}.tsx`
- `web/components/personalization/TutorPersonaEditor.tsx`
- `web/components/personalization/TutorPersonaPreview.tsx`
- `web/components/personalization/TutorPersonaMigrationNotice.tsx`

覆盖 typed 字段、保存/CAS、恢复默认、确定性 preview 和 legacy migration review；不调用旧 `personas-api.ts` 自由文本 CRUD。

### 3D Learning Governance

新建：

- `web/components/personalization/MasteryStateValue.tsx`
- `web/components/personalization/LearningGovernancePanel.tsx`
- `web/app/(utility)/profile/learning-model/{loading,error}.tsx`

小范围修改：

- `web/components/personalization/LearnerModelApp.tsx`
- `web/components/personalization/LearnerModelSnapshot.tsx`
- `web/app/(utility)/profile/learning-model/[subjectId]/page.tsx`

所有 mastery 统一走 `MasteryStateValue`：`state != estimated`、未校准或 probability null 时只显示“证据不足”，不显示 0%/进度条。新增 Errors/Misconceptions/Repairs/Reviews 分区；只消费 learner-safe DTO。

### 3E Chat 兼容

新建 `web/app/(workspace)/chat/[threadId]/page.tsx`，server-side redirect 到 canonical `/home/[threadId]`，保留安全 query，复用原 session owner 校验，不复制会话。

### 页面通用验收

- 原生 button/link/label；icon action 有可访问名称；dialog focus trap/Escape/焦点恢复。
- tabs 使用完整 ARIA 语义；动态状态 `aria-live`，错误 `role=alert`。
- 320px 与 400% zoom 无丢失操作；不使用 `window.prompt/window.confirm`。
- 新文案同步 `web/locales/zh/app.json` 与 `web/locales/en/app.json`，key 集合一致。

## Wave 4：IA、导航与兼容（Layer 4）

新建 `web/components/personalization/ProfileHub.tsx`，修改：

- `web/app/(utility)/profile/page.tsx`：由 account redirect 改为 Learning Model / Memory / Tutor / Account / legacy Personas hub。
- `web/components/sidebar/SidebarShell.tsx`：Research 一级入口、Profile 二级入口、`aria-current`，session path 识别 chat。
- `web/components/sidebar/MobileNavigation.tsx`：Research + 单一 Profile 入口，避免横向导航膨胀。

旧 `/space/personas` 保留并明确为 legacy/custom persona；旧书签不破坏。

## Wave 5：联合验收、CCG 门禁与文档

### 后端测试

新建 `tests/integration/test_missing_prd_backend_capabilities.py`，证明：

- 两用户及两 subject 隔离；越权查询不泄漏存在性。
- review 重放不双计分；弱证据不更新 BKT。
- memory 删除后新 recall/context 不含旧值；晚到 L3 rebuild 不复活。
- Persona 只改变 style contract。
- Research pause/cancel/restart recovery 与 late-result fencing。
- 公共 DTO 扫描无 answer/rubric/correct_rule/system/raw prompt。

### Playwright

新建：

- `web/tests/e2e/research-workspace.spec.ts`
- `web/tests/e2e/canonical-memory.spec.ts`
- `web/tests/e2e/tutor-profile.spec.ts`
- `web/tests/e2e/learning-governance.spec.ts`
- `web/tests/e2e/navigation-compat.spec.ts`
- `web/tests/e2e/missing-features.joint.spec.ts`

前五条覆盖状态/错误/键盘/409/zh-en/320px；joint 使用正式 store/router + deterministic executor，覆盖研究刷新恢复、memory 删除、persona 持久化、unknown 非 0%、chat 规范化。

### 质量门禁

按 CCG 顺序执行：

1. 新模块 `/verify-module`
2. `/verify-security`
3. `/verify-change`
4. `/verify-quality`

再运行：

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy traittutor
cd web && npm run lint && npm run build && npm run test:e2e
```

只在上述证据成立后更新 `docs/CODING-PLAN.md` 与 `PROGRESS.md`，精确标注本轮五个缺口闭环与仍未完成的 production-readiness 项。

## Agent Teams 文件归属

- Builder A：Learning Governance + canonical Memory（后端及 tests）。
- Builder B：Typed Tutor Persona + Research Workspace（后端及 tests）。
- Builder C：四个 typed clients + Research/Memory 页面。
- Layer 2 Builder D：Tutor/Learning Governance 页面 + chat/profile/navigation/i18n（等 A/B API 稳定后开始）。
- Reviewer：只读全 diff + 测试；Critical 由新的 fix Builder 按文件 allowlist 修复。

不同 Builder 不共享可写文件；`traittutor/api/main.py` 和 locales 在依赖完成后的单一 Builder 中串行处理。

## 风险与回滚

- 新 API 使用独立路径，与 legacy router 并存，可按 router 注册回滚。
- 新页面入口可独立撤回，不影响现有 `/home`、Learning Pack 和 legacy Personas。
- Research executor 默认 deterministic/fake 测试，不把真实 provider 作为 CI 前置。
- WS-8 migration 默认 dry-run，checkpoint/rollback manifest 必须先通过再允许写 projection。
- 保护文件 diff 每个 Wave 前后比对；任何漂移立即停止重叠写入。
