# 缺失功能搭建：结构化需求

## 目标

依据 `docs/PRD.md` v2.7 与当前代码，补齐此前页面审计中缺失或仅有 analog 的产品能力，使功能具备真实契约、owner 隔离的持久化/API、可访问页面和自动化验收证据，而不是只增加静态占位 UI。

## 范围

1. **WS-11 深度研究工作区**：实现 `ResearchWorkspace`、`ResearchBrief`、`ResearchRun`、来源和笔记的 durable truth source；支持创建、查看、暂停、取消、恢复以及报告/引用状态；增加 `/research/[workspaceId]` 页面。
2. **WS-12 教练形象**：实现版本化、白名单 `TutorPersonaProfile`，确定性表达编译和 owner-bound 持久化/API；增加 `/profile/tutor` 配置与预览页；Persona 不得改变评分、BKT、答案和安全策略。
3. **WS-5/WS-8 记忆管理闭环**：以 canonical memory 为来源提供 `/profile/memory` 管理页，覆盖候选、激活、冲突、替代、停用与删除；跨项目/学科授权和来源信息可见；删除后后续上下文不得再读到旧值。L3 迁移遵循 ADR-0005，不把 text-only legacy 摘要伪装成完整 provenance。
4. **入口与兼容路由**：让 PRD IA 中的研究、记忆、教练入口可达；为 `/chat/[threadId]` 与现有 `/home/[sessionId]` 建立清晰的兼容/规范化路径，不破坏现有书签和会话。
5. **学习模型展示收敛**：补齐 PRD 要求的错误、误解、修复和复习治理展示；只展示服务端可信证据，canonical 未校准状态必须显示 unknown，不能显示 0% 或误解锁。
6. **学习治理服务契约**：提供 owner-bound、按 subject/KC 聚合的 `/errors` 与 `/reviews` 公共 DTO/API；复用现有 ErrorRecord、Misconception、Repair、Review 状态逻辑，但不得把 LearningPack 内的答案、rubric、正确规则或原始 prompt 投影到公共响应。

## 不变量

- 所有 store/API 在内部按认证 `user_id` 校验 owner，不依赖前端传入 owner。
- 研究检索主张必须有可点击来源；模型知识不得冒充检索结果。
- Persona 只影响表达，不能进入判分、BKT、正确答案或安全决策。
- 阅读、提问、搜索、收藏、自报和 Persona 设置均不得更新 BKT。
- 删除/停用记忆后必须从可召回集合消失；审计记录保留最小必要元数据。
- PageSchema 与公开 API 不泄露答案、rubric、隐藏提示、原始 prompt 或秘密配置。
- 保留当前脏工作树中的用户改动；不做无关格式化或重写。

## 验收标准

- 后端 contract/store/router 的定向 pytest 覆盖 owner 隔离、状态转换、幂等、恢复和删除语义。
- 前端新增路由均可通过 TypeScript、ESLint 和生产 build；关键状态有 loading/empty/error/disabled 文案和键盘可达交互。
- 至少一条 Playwright 联合路径证明用户可从入口创建/打开研究工作区、管理记忆和编辑教练形象。
- `ruff check .`、`ruff format --check .`、`mypy traittutor`、`npm run lint`、`npm run build` 在最终验收中有当前证据；对既有非本任务阻塞需单独列出且不得冒充通过。
- `docs/CODING-PLAN.md` 与 `PROGRESS.md` 仅在代码和测试证据成立后更新状态。

## 本任务边界

本任务完成的是此前 PRD 页面审计识别的 5 个缺口及其最小可信服务闭环，不宣称整个 v2.7 已完成。WS-7 Gateway 全量收敛、WS-13 生产 sink/聚合/告警、BKT 参数校准与所有 feature flag 正式启用属于后续 production-readiness 工作；但本轮 Research 不得新增 Gateway 旁路，新增埋点沿用现有 typed telemetry 边界。

## 需求完整性评分

- 目标明确性：3/3
- 预期结果：3/3
- 边界范围：2/2
- 约束条件：2/2
- 页面缺口与本任务交付范围：10/10
- 全部 v2.7 收敛：不在本任务范围，禁止在交付报告中宣称完成
