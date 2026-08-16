# CCG Phase 2：多模型构思综合

## 当前事实

- PRD v2.7 要求 `/research/[workspaceId]`、`/profile/memory`、`/profile/tutor` 和 `/chat/[threadId]`；当前代码均没有对应产品路由。
- 现有 Research 仅在 chat 消息中展示临时 outline/report；`DynamicTopicQueue` 是执行队列，不具备 Workspace/Brief/Run/Source/Note 真相模型。
- `traittutor/memory` 已有 owner-bound canonical store 和 lifecycle，但没有认证派生 owner 的在线管理 router/client/page；旧 `/api/v1/memory` 主要服务 Markdown workbench。
- 现有 `/space/personas` 与 `/api/v1/personas` 是自由文本 `PERSONA.md`，不满足 ADR-0006 的 typed whitelist 和“只影响表达”可证明边界。
- 现有 learning model 页面/API 没有跨 pack 的 Error/Misconception/Repair/Review 聚合；前端仍有把 nullable/unknown mastery 用 `?? 0` 显示成 0% 的路径。
- 当前工作树已有用户改动：`traittutor/generate/service.py`、`web/components/learning/LearningCanvas.tsx`、`web/components/personalization/WhyThisGeneration.tsx`、`web/lib/traittutor-api.ts` 及 `tests/learning/test_adaptive_wiring.py`。实施必须避免覆盖或大规模格式化这些文件。

## 方案 A：兼容壳优先

直接把现有 chat research、legacy memory workbench、自由文本 Persona 和 LearningPack repair/review 投影到新页面。

优点：路由和视觉出现快，复用文件少。

缺点：

- Research 刷新后没有 durable Workspace/Run 真相，暂停/恢复/取消没有 fenced 状态。
- legacy L3 缺逐 claim provenance，会把未验证摘要误展示为可信记忆。
- Persona 自由文本可携带越权教学/判分指令，无法证明只影响表达。
- LearningPack DTO 含 server-held answer/rule/prompt，不能作为通用治理 API。

结论：只适合不可提交的视觉原型，不满足本任务验收。

## 方案 B：canonical 垂直切片（采用）

每项按 `contract/store → owner-safe router → typed client → page state machine → navigation → focused/E2E tests` 交付；现有研究 pipeline、Persona 页面和 Pack 逻辑只作为 adapter 或视觉参考。

### 依赖顺序

1. Learning governance 公共 DTO/API 与 unknown 展示修复，建立学习模型的安全投影边界。
2. Canonical memory 管理 API/page；WS-8 sidecar 对 legacy L3 显式标记 `legacy_unverified`，再做可回滚 rebuild。
3. Typed TutorPersonaProfile/store/compiler/API/page；通过 differential tests 证明只改变 style contract。
4. ResearchWorkspace durable truth source/router/page；执行 adapter 必须经现有 Gateway，progress event 不是真相源。
5. `/chat/[threadId]` server redirect、导航/Profile hub 与各页面入口收敛。
6. 分能力 Playwright、全量质量门禁与文档状态更新。

## 核心风险与缓解

- **Owner 越权**：router 内部从 `get_current_user().id` 和 current scope 构造 store；请求体不接收 `user_id`。
- **迟到任务复活**：Research run 用 cancellation epoch/claim token fencing；先持久化状态和 receipt，再发 progress。
- **答案泄漏**：单独定义 learner-safe governance DTO；公共响应禁止 answer/rubric/correct_rule/prompt。
- **错误记忆来源**：legacy L3 一律 `legacy_unverified`；仅 sidecar/canonical source ID 可成为可信 provenance。
- **Persona 越界**：冻结 Pydantic whitelist + deterministic compiler；compiler 输出单独 Persona Contract，不能表达 grading/BKT/answer/safety 字段。
- **前端假 0%**：unknown 使用判别状态，组件和 Playwright 明确断言页面不出现 `0%`/进度条。
- **脏树冲突**：为四项能力新建独立 API client；不扩写当前大规模改动中的 `web/lib/traittutor-api.ts`，对受保护组件只做必要小 patch 或等待用户改动稳定。

## 多模型结论

前端 analyzer 与独立验收 reviewer 均反对方案 A，推荐方案 B。两者一致要求把 Research REST truth 与流事件分离、Memory/Persona 与 legacy 页面分离、Learning governance 使用公开安全 DTO，并为每项能力设置独立 E2E，而不是只跑一条综合 happy path。
