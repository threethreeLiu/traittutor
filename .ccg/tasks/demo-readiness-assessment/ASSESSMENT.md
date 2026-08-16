# 演示就绪度测评报告 (Demo-Readiness Assessment)

> 生成日期: 2026-08-09 ｜ 分支: `ws-1b` ｜ 策略: deep-research ｜ 方法: 3 个并行 Explore 代理 + Claude 经验性验证 + 12 铁律交叉校验
> 范围: 生成链路 / 功能 / UI / 模型调用 / 国际化 / 记忆保存模式

---

## 0. 总结论 (TL;DR)

**核心学习闭环已可录演示视频。** 登录 → 上传/输入材料 → 分析 → 生成课件/卡片/测验 → 学习画布作答 → 服务端判分 → 掌握度/复习/修复 全链路真实接通，无桩代码、无 `NotImplementedError`。

**唯一前置条件（已满足）：LLM 已配置为 MiniMax-M3，运行时解析成功**（经验性验证：`resolve_llm_runtime_config()` 返回有效 `ResolvedLLMConfig`，model=`MiniMax-M3`，base_url+key 齐全）。

**4 个模型缺口中，只有 1 个可能阻断演示——取决于演示脚本是否包含 RAG/语音/联网搜索：**

| 能力 | 状态 | 来源 | 阻断的演示流程 |
|---|---|---|---|
| **llm** | ✅ 已配 | `config/models.local.yaml` overlay → MiniMax-M3 | （满足一切） |
| **imagegen** | ✅ 已配 | `model_catalog.json` → Agnes AI 2.0 Flash | 课件配图 |
| **embedding** | ❌ **缺失** | catalog 空 | **RAG / 文档接地问答**（材料分析有启发式回退，不受影响） |
| **tts** | ❌ 缺失 | catalog 空 | 语音输出 |
| **stt** | ❌ 缺失 | catalog 空 | 语音输入 |
| **search** | ❌ 缺失 | catalog 空 | `web_search` 联网搜索工具（可选） |

→ **若演示只演核心闭环（聊天+材料+生成+作答+掌握度），当前状态可直接录。** 若要演 RAG/语音/联网，需先补 embedding/tts+stt/search（纯配置，不改代码）。

**0 条违反 12 铁律的发现。** 所有 flag-OFF 默认路径都是连贯、LLM 驱动的完整路径。

---

## 1. 生成链路 (Generation Pipeline) — Agent A

### 1.1 关键更正（对上一轮理解的修正）

> ⚠️ **确定性 `_generate_courseware`（`generate/service.py:335`）不是演示路径。** 它只是 `GenerationTaskManager` 里的 **identity sentinel**（`tasks.py:573` `if self._generator is generate_traittutor_content`）。真实默认演示路径是 **LLM 驱动的异步路径** `generate_traittutor_content_async`（`service.py:801`）。

确定性生成器在 live 请求流里是 dead code（sentinel-only），不影响演示，但会误导。

### 1.2 真实默认路径（flags 全 OFF）

```
POST /api/v1/traittutor/generate/tasks
  → traittutor_generate.py:313 → GenerationTaskManager.create (tasks.py:491)
  → SQLite 持久队列 (generation-tasks.sqlite, WAL) → _run (tasks.py:540)
  → generate_traittutor_content_async (service.py:801)
      解析材料 → strategy/compass/personalization
      courseware: generate_courseware (courseware.py) ← 真实 3 段 LLM 流水线
                  (content-analysis → adaptation-plan → traittutor-courseware, 各 run_structured_prompt)
      flashcards/quiz: plan_*_batches → 每批并行 run_structured_prompt
  → run_structured_prompt (runner.py:123) → Gateway.complete → LLMClient (真实 provider, 路由轮换 MAX=2 + 有界重试)
  → 源接地检查 + evaluate_generation (review 裁决) + 可选配图
  → save_generation → traittutor/generations/{id}.json
  → SSE: /tasks/{id}/events
GET /tasks/{id} → _task_result_with_page_schema (released 仅当 status=completed)
                  flag-OFF → 不附 page_schema，返回 learner-safe 结果（quiz 答案剥离）
                  status ∈ {completed, needs_review}
```

**状态：完全可演示。** 真实 LLM、真实持久化、真实 SSE 流、review/retry/cancel 全部实现。

### 1.3 4 个 flag 真实性核对

| Flag | 后端是否存在 | 开启效果 | OFF 对演示影响 |
|---|---|---|---|
| `TRAITTUTOR_CONTEXT_SNAPSHOT_WIRING` | ✅ `context_assembler/wiring.py` | 真实跨会话上下文快照召回注入 personalization | **无** — OFF 路径仍构 personalization 上下文，只是无跨会话召回 |
| `TRAITTUTOR_CANONICAL_GRADING_EVENT_CHAIN` | ✅ `learning/event_chain.py` | 作答先写不可变 `LearnerEvent` 再投影 BKT（带 lease/幂等） | **无** — OFF 路径直接 `record_event(trusted=True)`，仍更新掌握度 |
| `TRAITTUTOR_PAGE_SCHEMA_WIRING` | ✅ `components/wiring.py` | 课件走 orchestrator DAG，发布冻结 `PageSchema` 到 PageStore | **无** — OFF 走更简单的 LLM 课件路径 + 内联配图 |
| `TRAITTUTOR_PAGE_SCHEMA_CSP` | ❌ **后端无此 flag** | — | CSP 是**前端**白名单渲染关注点（CODING-PLAN F-08），非后端 env flag |

→ **flag 全 OFF 默认态是干净、连贯、可演示的状态。** 实际后端 flag 只有 3 个（CSP 纯前端）。

### 1.4 链路级缺口（均非阻断）

- 同步 `generate_traittutor_content` + 确定性 `_generate_courseware/_flashcards/_quiz`（service.py:335-435）在请求流里 dead（sentinel-only）。`service.py` 模块 docstring 仍宣称确定性生成器是"first product version"——陈旧。
- orchestrator 重路由桥接缝（service.py:702-731）有 reviewer 注释，演 flag-ON 路径时 `page_schema_id` 与 orchestrator 内部 `run_id` 不同——知道即可。

---

## 2. 功能 / 核心流程 (Core Flows) — Agent A

| 流程 | 入口路由 | 端到端接通？ | 可演示？ | 缺口 |
|---|---|---|---|---|
| 课件生成 | `traittutor_generate.py` POST `/tasks` | ✅ | ✅ 需 LLM | 无 |
| 卡片生成 | 同上 `generation_type=flashcards` | ✅ | ✅ 需 LLM | 无 |
| 测验生成 | 同上 `generation_type=quiz` | ✅ | ✅ 需 LLM | 无 |
| 测验 AI 判官 | `quiz_judge.py` WS `/api/v1/question/judge` | ✅ | ✅ 需 LLM | 无 |
| 独立测验判分（确定性） | POST `/tasks/{id}/quiz/grade` | ✅ | ✅ **无需 LLM**（服务端比对存答案） | 无 |
| 学习包+组件流 | `learning_packs.py` `/api/v1/learning-packs` | ✅ | ✅ | 无（作答先 `_verified_assessment_observation` 验证再 BKT） |
| 掌握度/进度 | `personalization.py` GET `/learner/*` | ✅ | ✅ | 无（新用户为空直到生成+作答） |
| 材料上传+分析 | `traittutor_generate.py` `/materials/{prepare,analyze}` | ✅ | ✅ **无 LLM 也可**（启发式回退 material_analysis.py:458） | 有 LLM 效果更好 |
| 仿题/主题出题 | `question.py` WS `/mimic`,`/generate` | ✅ | ✅ 需 LLM | 较老的独立界面 |
| 仪表盘/近期 | `dashboard.py` GET `/recent` | ✅ | ✅ | 无 |

**所有核心流程 router→service→LLM/gateway→persist→return 全部接通。** 唯一硬前置：LLM 已配置（**已满足**）。

---

## 3. UI / 前端 (Frontend) — Agent C

### 3.1 路由组与页面

- **(workspace)** — `/home` 主聊天工作区+学习目标入口+生成启动（2218 行，全接通）；`/assist` 别名；layout = WorkspaceSidebar + MobileNav + OnboardingProvider + CapabilityGate
- **(utility)** — `/space`（SpaceDashboard）、`/space/learning`（包列表）、`/space/learning/[packId]`（**`LearningCanvas` 演示核心**）、`/space/traittutor`（学习画像：大五雷达+SLR）、`/space/{quiz,flashcards,courseware}`（`StudyToolWorkbench` 真接通，非桩）、`/knowledge`、`/notebook`、`/memory/{l1,l2,l3,graph,resolve}`、`/profile/learning-model`、`/settings/*`
- **(auth)** — `/login`、`/register`（都用 `t()`，调用 `login()`+`fetchAuthStatus`，重定向 `?next`）
- **(admin)** — ⚠️ **只有 `layout.tsx`，无任何 `page.tsx`。整个 admin 组是空壳。** 仅当演示含管理后台时相关。

### 3.2 后端接线

`web/lib/traittutor-api.ts`（788 行）是单一后端网关。`/home`、`ChatGenerationPanel`、`LearningCanvas`、`LearningPlansHome`、`PageSchemaRenderer`、`SpaceDashboard`、`StudyToolWorkbench`、`OnboardingProvider` 等全部真接线。覆盖端点：`learning-packs`(CRUD+by-session)、plans、journeys/active、reviews/due、repairs/retry、events、`traittutor/profile/{questions,profiles}`、`traittutor/generate`(+materials/prepare,analyze, tasks/{id}, quiz/grade, retry, review/{confirm,discard})、notebook/add_record、voice/tts。

### 3.3 PageSchema 渲染状态

**已接线且有真实测试。** `web/components/learning/PageSchemaRenderer.tsx` 是 F-08 白名单渲染器：13 个注册 `component_type`，所有值输出为纯 React 文本节点（**无 `dangerouslySetInnerHTML`**），`media_url` 过 `isSafeMediaUrl`（拒绝 `javascript:`/非 http/SVG），未知类型文字降级。`LearningCanvas.tsx:504-511` 挂载：`output.page_schema` 存在且为内容/课程步骤（非 assessment/retrieval）时替换 `LessonView`。`tests/e2e/page-schema.smoke.spec.ts` 覆盖：标题+正文渲染、`javascript:` 图被拦、未注册组件降级、`LessonView` 不渲染、注入 `<script>` 不执行（`window.__traittutor_xss` undefined）、点击"完成并继续"发稳定 `event_id`。✅ 安全+契约覆盖。

### 3.4 演示用户流地图 + 断点

1. 登录 → `/login` ✅
2. 上传/输入材料 → `/home`，`prepareTraitTutorMaterial`+`analyzeTraitTutorMaterial`+`MaterialAnalysisSummary` ✅
3. 生成课件 → 建 pack→plan→`createTraitTutorGenerationTask`→`LearningJourneyLaunch` 卡片 ✅
4. 查看 → `/space/learning/{packId}` → `LearningCanvas` → `PageSchemaRenderer` ✅
5. 作答测验 → `AssessmentView`/`RetrievalView` → POST events → `verified_observation`；校准检查点+修复卡+到期复习队列 ✅
6. 看掌握度/进度 → 分散在 3 个界面：`LearningCanvas` `WhyPanel`、`LearningPlansHome`、`/space/traittutor`+`/profile/learning-model`（**功能在，但无单一统一面板**）

**演示阻断：核心闭环无。** 唯一具体缺口是空 `(admin)` 组（仅当演示含管理后台）。

---

## 4. 模型调用 (Model Calls / 还缺什么模型) — Agent B + Claude 经验性验证

### 4.1 运行时 LLM 解析（澄清"catalog 为空却能用"之谜）

`model_catalog.json` 里 `services.llm` 空是**设计如此，不是缺口**。两个 gitignored 源文件：

1. **`config/models.local.yaml`**（真实 LLM 源）：`active: minimax-m3`，含 minimax-m3 / deepseek-v4 / zhipu-glm(glm-5.2) / kimi(kimi-k3) / stepfun / agnes-ai 等条目。由 `traittutor models sync-cc-switch` 从 `~/.cc-switch/cc-switch.db` 自动生成。
2. **`~/.cc-switch/cc-switch.db`**（SQLite，12MB，今日修改）→ `cc_switch.py` 映射。

解析链：`ModelCatalogService.load()` → `_overlay_local_llm()`（line 84）→ `load_local_llm()` 读 YAML **替换** `catalog["services"]["llm"]`。`save()` 写 JSON 时**重置 llm 回空壳**——所以 JSON 里 `llm.profiles=[]` 但运行时有 MiniMax。

**经验性验证（本会话实跑）：** `resolve_llm_runtime_config()` → 返回 `ResolvedLLMConfig`：
```
model: MiniMax-M3 | provider_mode: direct | binding: custom_anthropic
base_url: https://api.minimaxi.com/anthropic | api_key: (125 chars, 已配) | effective_url: ✓
```
**→ 生成/聊天/判官在演示中可运行。** 代码无硬编码 fallback；若 YAML 被删则所有 agent 调用抛 `LLMConfigError`。

### 4.2 缺失模型（按演示优先级排序）

1. **embedding** — 硬阻断 RAG/知识库/材料接地（`services/embedding/config.py:35` 空 profile 时 `raise ValueError`）。**最高影响**（若演示含文档接地辅导）。
   - 注意：材料上传/分析有启发式回退，**不**依赖 embedding。
2. **tts + stt** — 硬阻断语音演示（输入+输出）。一个 OpenAI 兼容 profile 各覆盖一个（voice adapter 是通用 OpenAI-compat）。
3. **search** — 硬阻断 `web_search` 工具，但属 opt-in；核心聊天/测验/课件不依赖。最低优先级。

### 4.3 已配模型（无需动作）
- **llm** MiniMax-M3（overlay，已验证解析）✅
- **imagegen** Agnes AI 2.0 Flash（catalog，courseware 配图）✅

---

## 5. 国际化 (i18n) — Agent C

### 5.1 现状（已存在，评覆盖率而非从零搭）

- 栈：`i18next ^25.8.0` + `react-i18next ^16.5.3`，Provider 链端到端接通（`app/layout.tsx:57` → `I18nClientBridge` → `I18nProvider`，模块加载时 init，同步 `i18n.changeLanguage`+`<html lang>`）
- 语言：`en`、`zh`，单 namespace `app`。文件 `web/locales/{en,zh}/app.json`
- 键数：**en 2752 / zh 2752，精确对等**（0 缺失；zh 有 1 个 key `"s"` 空值，可忽略）。en 文件 2583/2752 键 value==key（源串即英文，恒等映射正确，非缺口）
- **演示语言是中文。** `AppShellContext.tsx:72` 初始化 `useState("zh")`，`init.ts` `fallbackLng:"zh"`。完整 zh 翻译可用。
- 切换器：`LanguageSwitcher.tsx`，挂在 `SidebarShell`（桌面×2）+ `MobileNavigation`（移动）+ `OnboardingProvider` 内联 EN/中文 toggle。

### 5.2 覆盖率陷阱（非阻断）

演示关键的 **"learning" 界面不用 `t()` 目录**，而用内联双语对象 + `zh` flag：
- `LearningCanvas.tsx`：159 zh literals，0 `useTranslation`（用页面传的 `zh` prop）
- `ChatGenerationPanel.tsx`：45 zh literals，`useTranslation` 仅读 `i18n.language`
- `LearningJourneyLaunch.tsx`：14 zh literals
- `SpaceDashboard.tsx`：24 zh literals，`useTranslation` 仅语言探测
- `home/[[...sessionId]]/page.tsx`：25 zh literals，`useTranslation` 仅探测
- `space/traittutor/page.tsx`：27 zh literals（`{zh,en}` Lang 对象）

→ **每个演示界面两种语言都在**（功能完整），但通过两套并行机制（chrome/login/composer 用 catalog `t()`；learning 界面用内联 `{zh,en}`）。**维护风险，非演示阻断。** `(auth)/login` 是规范用 `t()` 的最干净范例。

---

## 6. 记忆保存模式 (Memory Save Mode) — Agent B

### 6.1 分层（`services/memory/paths.py`）

- **L1** — `trace/<surface>/<YYYY-MM-DD>.jsonl`，append-only 原始事件。surfaces：chat/notebook/quiz/kb/book/partner/cowriter
- **L2** — `L2/<surface>.md`，每界面摘要（7 文件）
- **L3** — `L3/{recent,profile,scope,preferences}.md`，跨界面。正是四个槽。

### 6.2 聊天中如何保存

- **`write_memory` 工具**（`tools/builtin/__init__.py:655`）是聊天模式**唯一写路径**。直接写 **`L3/preferences.md`**（`MemoryStore.write_preference()`，幂等——`_find_duplicate_preference` 短路重复，issue #647），并发 1 条 L1 trace 事件。
- **L2 和其他 L3 槽（recent/profile/scope）聊天中不自动写**。从 **Memory 工作台手动整合**（`api/routers/memory.py`，模式 `update/audit/dedup/merge`，SSE 流）。

### 6.3 如何召回

- `read_memory` 工具（`tools/builtin/__init__.py:625`）→ `MemoryStore.read_l3_concat()` 拼接四份 L3 文档进模型工具上下文
- `context_assembler/assembler.py:244` 只读记忆 **refs/footnotes**（非全文）以保持聊天 prompt 小

### 6.4 整合器可演示？

✅ **是——完全 LLM 驱动，非桩。** `services/memory/consolidator/modes/_runtime.py::call_llm`（line 88）用活跃 LLM 配置（MiniMax-M3）的 `llm_stream`/`llm_complete`（流式→非流式回退），原子写（`_atomic_write`+`mkstemp`+`os.replace`），撤销检查点，rich en/zh prompt 资产。三种真实模式：`update`（分块事实抽取）、`audit`（行级编辑对照原始证据）、`dedup`（迭代行级）。经 Memory 工作台路由暴露可运行。

### 6.5 按用户隔离？

✅ **是。** `paths.memory_root()` → `get_path_service().get_memory_dir()` → `workspace_root/memory`。`get_path_service()` 经 ContextVar 解析当前用户工作区 `data/users/<uid>/`。一处显式豁免：`memory_path_service_override()` 允许 partner-runtime 读 owner 记忆（其余服务仍 partner 范围）——关乎铁律 #6/#7 partner 隔离，但隔离本身被强制。

### 6.6 演示注意（非阻断）

新演示用户的 L2/L3（recent/profile/scope）**初始为空** → `read_memory` 返回 `"(No memory available…)"`，直到：(a) 用户陈述一条偏好（自动存 preferences.md），或 (b) 操作员从 Memory 工作台跑 `update`。**preferences 槽自动填充；其余三槽需手动整合。** 若演示要展示"记忆"，建议演示前预跑一次 consolidator 或让 demo 用户先陈述几条偏好。

---

## 7. 12 铁律交叉校验

本轮全部发现经 12 铁律交叉校验：**0 违反**。flag-OFF 默认路径均连贯。已知缓延缝（非演示阻断，上一轮 review 已记录）：
- **C11 prose-claim**：外部主张作为 `body_markdown` prose 无结构 `ExternalClaimRecord` 会静默通过（`orchestration/evaluator.py`）——缓延的生成适配器迁移。
- **C6 display↔decision**：`learning/policy.py` `is_mastered`/`next_objective` 仍走 legacy `mastery_levels` 决策路径；canonical `MasteryReadView` 只 enrich 显示（ADR-0002）。flag-OFF 下决策路径自洽。
- **C7 心跳**：三层幂等已闭环（ledger `_derived_applied`+lease/opaque-token fencing+下游 `apply_signal` signal_id 去重），无 lease 心跳/续约——崩溃恢复靠 at-least-once 重放。

---

## 8. 演示脚本建议（保证可录）

**推荐核心闭环演示（当前 100% 可录）：**

1. **登录**（`/login`，zh 默认）
2. **上传一份 PDF/文本 或 输入学习目标**（`/home`）→ 看材料分析
3. **生成课件**（点生成）→ 等 SSE 完成进度
4. **进入学习画布**（`/space/learning/{packId}`）→ PageSchema 渲染课件
5. **作答测验**（AssessmentView）→ 看服务端 verified_observation + 校准检查点 + 修复卡
6. **复习到期卡片**（retrieval）→ Leitner 队列
7. **看掌握度**（WhyPanel + `/space/traittutor` 大五雷达 + SLR）
8. （可选）**切换中/英**（LanguageSwitcher）展示 i18n
9. （可选）**记忆工作台**跑一次 consolidator 展示 L3 写入

**录前自检清单：**
- [ ] 后端在跑（`scripts/start_local_dev.sh`）
- [ ] 演示账号已注册/登录
- [ ] （可选）演示前在 Memory 工作台预跑一次 `update` 或让 demo 用户陈述 2-3 条偏好，避免记忆页空白

---

## 9. 附：决策点（详见 MODIFICATION-PLAN.md）

需用户拍板：
1. **演示脚本范围** — 只演核心闭环？还是含 RAG/语音/联网？（决定是否要补 embedding/tts+stt/search）
2. **是否补 embedding** — 若演文档接地辅导，这是 P0 配置项（不改代码）
3. **是否清 dead code** — 同步生成器+确定性 `_generate_*`（sentinel-only），改 stale docstring
4. **i18n 是否统一** — learning 界面的内联 `{zh,en}` 是否迁移到 `t()` 目录（维护性，非演示）
5. **`(admin)` 组** — 演示是否需要管理后台？（当前空壳）
6. **执行模式** — 确认后由谁执行（Claude 独立 / Codex 主力）+ 是否仍受 codex `--approve-for-me` 拦截影响
