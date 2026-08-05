# TraitTutor 产品需求文档（PRD）

**状态：** 发布前演示闭环版  
**更新时间：** 2026-08-05  
**适用仓库：** `/Users/lrm/Documents/code/TraitTutor_all_in_one`

本文是 TraitTutor 当前版本的产品范围、核心链路和验收标准。当前产品以“目标驱动的学习组件路径”为主界面；课件、闪卡、Quiz、图像和语音保留为后台执行器与历史产物，不再要求用户先选择生成器。用户进入学习路径后进入全屏学习画布，桌面端自动收起左侧导航，移动端使用步骤导航和 Why Drawer。

如果本文与代码不一致，以当前代码为准，并在同一变更中修正文档。

## 1. 产品一句话

TraitTutor 是一个面向普通学习者的目标驱动 AI 学习教练：用户可以从一句目标、一份材料或一道题开始，系统将其转化为学习路径、讲解、练习与复习，并通过可解释的学习事件持续识别薄弱点和安排下一步。

TraitTutor 不以“比通用聊天模型回答得更多”为目标。通用聊天解决一次问题；TraitTutor 负责建立可持续学习目标、组织可信来源、触发主动练习、记录学习证据，并让下一次教学真正使用这些证据。

TraitTutor 使用 Big Five/TIPI 作为低权重、可关闭、可解释的教学支持线索。它不是人格诊断、能力评估、学习风格分类，也不声称画像本身带来客观学习增益。

## 2. 产品目标

### 2.1 发布前目标

当前版本优先服务比赛演示与开源发布：

1. 用户无需先上传材料，可以直接输入学习目标；系统创建真实 LearningPack 与 LearningComponentPlan，并展示统一的“开始第一步”入口。
2. 用户上传真实材料后，立即看到接收/解析状态，并看到学科、年级、难度、候选概念和页码证据。
3. 一个学习目标对应一个 Learning Pack；Pack 可以持续追加材料、可信外部来源、聊天与学习产物，并共享课件、闪卡和 Quiz。
4. Quiz 作答和 Flashcard 复习能回流为 LearnerEvent，并影响 BKT 风格概念状态、待复习负荷和学习画像。
5. 已生成课件、闪卡、Quiz 可以在主页聊天中作为 learning artifact source 被二次问询，而不是把整份内容粗暴塞进 prompt。
6. Why Drawer 能解释“为什么这样生成”，但不暴露隐藏 prompt、私有推理或人格判断。
7. 旧 foundation / DeepTutor 类产品身份不出现在用户可见产品面。

### 2.2 长期目标

1. 形成一个“目标 → 路径 → 来源 → 教学 → 练习 → 记忆治理 → 下一步”的持续学习闭环。
2. 让学习画像可查看、可纠正、可删除、可重建。
3. 让生成质量可回归测试，而不是只依赖人工试用。
4. 让不同学科、不同对话、不同材料的学习证据保持隔离，避免污染。

## 3. 非目标与边界

本阶段明确不做：

- 后测、PPS、RIMMS、PSRLS/SRL 问卷。
- 实验分组、知识前测、论文统计和 Lark 研究数据流程。
- 人格诊断、学习风格标签、能力等级、跨用户画像。
- 自动把一次点击、停留、浏览、保存推断为长期掌握。
- 视频、YouTube、实时录音和复杂多模态课程制作。语音讲解作为可降级学习组件保留。
- 暴露旧 mastery_path、math_animator、visualize、partners、co-writer 等旧产品入口，除非重新设计成 TraitTutor 当前学习体系的一部分。

必须保留的核心功能：

- Chat
- Deep Research
- 解题
- 学习探索
- 知识图解
- 课件
- Flashcards
- Quiz
- 学习画像 / Learner Model
- 材料分析
- Learning Pack
- Notebook / Question Bank / Knowledge / Settings 中与学习主线有关的功能

## 4. 用户主流程

```text
注册 / 登录
  → 输入学习目标 / 上传材料 / 提交问题（三种入口等价）
  → 创建或复用目标型 LearningPack
  → 可选完成 TIPI / Big Five profile
  → 对已有来源生成 MaterialAnalysisSnapshot
  → BKT、学科 SLR 支持与材料 affordance 选择学习组件
  → 在统一学习画布完成讲解 / 例题 / 图解 / 语音 / 练习 / 主动回忆
  → 后台执行器生成并保存课件 / Flashcards / Quiz / Media Artifact
  → 可判分练习 / 主动回忆
  → 写入 LearnerEvent
  → 更新 BKT / 知识图谱 / 学习画像 / 待复习
  → 在 Chat 中选择已生成产物继续追问
  → Why Drawer 解释本次生成依据
```

## 5. 信息架构与前端入口

### 5.1 主页 Chat

主页是学习起点，不再只显示通用聊天框。首屏必须解释用户能做什么，并同时提供：输入学习目标、上传材料、继续上次学习三个入口。

主页保留适合即时使用的能力：

- Chat
- Deep Research
- 解题
- 学习探索
- 知识图解
- Humanizer / 改写类文本能力

课件、Flashcards、Quiz 不放入通用能力下拉列表；当识别到明确学习目标时，代码路由必须创建 LearningPack 与 LearningComponentPlan，并在聊天中展示系统安排的路径预览和唯一的“开始第一步”，不能只返回科普文本，也不让用户先选生成器。生成后的产物仍可作为上下文二次问询。

产品入口与学习画布必须分离：主页负责解释产品、接收目标/材料/问题并创建或恢复 Pack；`/space/learning/{packId}` 负责承载真正的学习组件。学习画布不是聊天消息的长文本替代品，也不是生成器菜单。桌面端使用“学习路径 / 当前组件 / 学习依据”三栏全屏布局，进入路径后自动收起工作区侧栏；移动端改为顶部步骤器、中央组件和底部 Why Drawer。

国际化是产品契约而不是局部翻译：TraitTutor 自有的标题、按钮、空状态、加载、错误、弹窗、ARIA 标签和 Why Drawer 必须随全局中英文切换；用户输入、上传文件名、材料原文和生成内容保持其原始语言，不被 UI 翻译层改写。

### 5.2 我的学习

“我的学习”承担学习路径与历史资产入口，分为：

- 进行中：按目标展示当前路径、组件进度和下一步。
- 待复习：跨学科聚合到期组件，但 BKT 与证据保持学科隔离。
- 学习材料：原文件、分析快照、候选概念和页码证据。
- 历史产物：课件、Flashcards、Quiz、图解和语音，可筛选、回看、导出。
- 新目标可上传 PDF / Word / PPT / Excel / 图片 / 文本。
- 若上传 PDF，不做“转换 PDF”的误导提示；只进行解析与材料分析。
- 同一材料生成或复用 LearningPack。
- 在同一 LearningPack 下生成课件、Flashcards、Quiz。
- 继续打开已生成内容进行复习、答题、查看解释。

### 5.3 学习画像

学习画像展示可解释学习状态，而不是人格诊断：

- 学科理解概览。
- 概念状态与 BKT 风格掌握概率。
- 待复习负荷。
- 证据来源。
- Reflection Governance：候选、已确认、已拒绝、已过期的学习反思。
- Compass 中当前真正会影响生成的偏好与策略。

### 5.4 对话角色

内置对话角色应是 TraitTutor 自己的学习角色：

- 学习共振 / Learning Companion
- 证据研究员 / Evidence Researcher
- 讲解设计师 / Lesson Designer

旧的 teacher、peer、research-assistant 等 foundation 角色不再作为默认角色展示。用户自定义角色保持可用。

## 6. 核心领域模型

### 6.1 MaterialAnalysisSnapshot

每份材料进入统一分析快照，不因生成课件、闪卡或 Quiz 重复分析。

字段：

- `analysis_id`
- `subject`
- `sub_subject`
- `grade_band`
- `difficulty`
- `language`
- `material_type`
- `concept_candidates`
- `page_evidence`
- `augmentation_decision`
- `confidence`
- `created_at`
- `version`

规则：

- 学科、年级、难度、材料类型应带字段级置信度。
- 低置信度概念保持 candidate，不直接写入长期学习画像。
- 用户确认或修正学科后，相关概念与 BKT 状态应按新学科重归属或重建。

### 6.2 LearningPack

一个学习目标对应一个 LearningPack。材料不是创建 Pack 的前置条件。Pack 可以挂载多个来源：

- 用户学习目标
- 上传材料
- 可信外部来源
- 用户笔记或历史对话
- 已生成学习产物

Pack 内可挂载多个 artifact：

- `courseware`
- `flashcards`
- `quiz`

规则：

- 没有上传材料时，以用户目标创建 starter plan，并将后续可信来源与原目标分开标示。
- 课件生成后，Flashcards 和 Quiz 可以选择同一 LearningPack 的 source snapshot，不需要重新上传。
- Flashcards 生成后，Quiz 也可以继续复用同一材料和学习上下文。
- Pack 内 artifact 必须保存材料快照、生成类型、模型、prompt signature、来源引用、图像状态和降级状态。
- Pack 增量保存 `component_plans`、`active_plan_id` 和 `component_progress`；旧 artifact 结构保持兼容。

### 6.3 LearningComponentPlan

学习组件计划是确定性编排结果，不由 LLM 自由决定。它组合 BKT、学科级 SLR 支持、材料 affordance、显式请求和无障碍偏好：

- BKT 决定目标概念、知识阶段和练习难度。
- 学科级 SLR 支持决定临时教学动作与组件组合，不产生能力或学习风格标签。
- 材料分析决定图解、语音、例题和练习是否合适。
- Big Five 只在没有行为证据时提供低权重初始支持线索。

组件类型为：`goal_map`、`concept_explanation`、`worked_example`、`visual_map`、`audio_explanation`、`diagnostic_check`、`guided_practice`、`retrieval_card`、`progress_checkpoint`、`reflection_prompt`、`transfer_challenge`、`review_queue`。

完成的组件与输出固定保存；可判分事件发生后，只生成新版本并替换未开始的路径尾部。计划版本通过 `supersedes_plan_id` 串联，可重连和审计。

计划是用户可见的学习顺序，artifact 是计划组件的执行结果或历史产物。组件完成后固定保存；只有未开始的尾部可以被新计划替换。这样既保留课件、Flashcards、Quiz 的独立回看与导出能力，又避免它们重新成为首页的产品入口。

### 6.4 LearnerEvent

LearnerEvent 是学习画像的可信输入，不允许模型任意写入自由文本当作长期结论。

高权重事件：

- `quiz_answer`
- `mastery_attempt`

中权重事件：

- `flashcard_review`

低权重事件：

- `self_assessment`
- `strategy_feedback`

不作为掌握证据：

- `courseware_outcome`
- 普通浏览
- 保存
- 停留时长
- 未确认的聊天推断

### 6.5 PersonalizationContext

生成前读取最小化上下文：

- 当前目标
- 当前材料摘要
- 当前学科
- 当前 LearningPack
- 已确认显式偏好
- 当前学科薄弱概念
- SLR 支持动作
- Hermes Compass 摘要

禁止注入：

- 全量聊天历史
- 原始隐藏 prompt
- Big Five 原始人格解释
- 未确认的跨学科结论
- 与当前任务无关的其他学科薄弱点

## 7. 生成链路设计

### 7.1 统一生成入口

三类结构化产物走统一 generate runner：

- `courseware`
- `flashcards`
- `quiz`

生成入口必须完成：

1. 读取或创建 MaterialAnalysisSnapshot。
2. 读取 LearningPack。
3. 构建 PersonalizationContext。
4. 选择 SLR 支持动作。
5. 决定是否需要外部查询补足。
6. 决定是否需要图片生成。
7. 走 Gateway 调模型。
8. 校验结构化结果。
9. 保存 artifact。
10. 返回可继续打开和二次问询的结果。

### 7.2 SLR 支持动作

SLR 不再写死在代码中，而应作为可编辑动作目录存在。

设计原则：

- SLR 不是诊断或问卷分数。
- SLR 是一组“教学支持动作”，例如结构化拆解、例子补足、回忆练习、对比解释、节奏控制。
- Prompt 只能读取被选中的动作，不读取完整人格解释。
- 选动作时同时考虑：材料类型、任务类型、学科、薄弱概念、显式偏好和 Big Five 低权重 cue。

### 7.3 外部查询补足

当材料证据不足、概念需要背景知识或用户要求“研究/探索”时，可以触发 function call / 工具查询补足。

规则：

- 补足必须标记为 augmentation，不得伪装成原材料证据。
- 外部查询失败不阻断课件、闪卡或 Quiz 的基础生成。
- Why Drawer 要展示是否发生补足、补足原因和降级状态。

### 7.4 图片生成

图片不是默认生成，而是按 SLR 支持与材料需要触发。

触发条件：

- 概念高度空间化、流程化、结构化。
- 课件段落需要图示才能明显降低理解负担。
- Flashcard 需要图像帮助识别结构、流程或对比。
- Quiz 需要图像题材，且图像不会泄露答案。

规则：

- 文本生成和图片生成可以并行。
- 图片生成失败要重试；重试失败后降级为文本 artifact，不阻断主结果。
- 最终 artifact 记录 `visual_generation_status`：`not_needed`、`planned`、`generated`、`failed`、`degraded`。
- Why Drawer 展示图片生成原因和失败/降级情况。

### 7.5 并行与组装

课件、Flashcards、Quiz 的生成应支持：

- 文本结构生成与图片候选生成并行。
- Flashcard / Quiz batch 并行生成。
- 批次校验后再组装为完整 artifact。
- 不展示未通过 schema 校验的半成品 JSON。

## 8. Chat 二次问询

主页 Chat 可以选择已生成产物作为上下文：

- 课件
- Flashcards
- Quiz

注入方式：

- 作为 `learning_artifact` source 引用。
- 只注入摘要、artifact id、关键 sections/items、引用和用户当前问题相关片段。
- 不把完整产物全文复制进 prompt。

用户可以问：

- “用这个课件再解释一下第三部分。”
- “根据这套闪卡帮我生成复习计划。”
- “分析我这次 Quiz 错在哪些概念。”
- “把这个知识图解讲成适合初学者的版本。”

## 9. 学习画像、知识图谱与 BKT 整合

### 9.1 知识图谱

材料分析产生候选图谱：

```text
学科 → 模块 → 概念 → 证据页码 / 片段
```

规则：

- 低置信度节点只作为 candidate。
- 用户确认材料学科后，相关节点归属到该学科。
- 删除材料或证据后，相关图谱节点应可重建或标记待重建。

### 9.2 BKT 风格知识追踪

BKT 只接收可判分、可解释的学习事件。

更新规则：

- Quiz 正确：提高对应概念掌握概率。
- Quiz 错误：降低或保持低掌握概率，并进入待支持/待复习。
- Flashcard “掌握了”：中权重正向观察。
- Flashcard “模糊/不熟”：中权重负向或不确定观察。
- Courseware 完成：记录参与，不增加 verified mastery。

### 9.3 多学科隔离

数学、物理、商业等学科应形成独立 subject profile。

规则：

- 数学错题不能污染物理或商业 BKT。
- 物理 flashcard 的不熟不能影响商业课件生成。
- 商业聊天引用课件只能进入 source inventory，不作为掌握证据。
- 再生成时只读取当前学科相关薄弱点。

## 10. Hermes / Reflection / Compass 记忆治理

旧 L1/L2/L3 只作为内部迁移实现，不作为产品概念继续扩散。

新产品概念：

- Trail：原始学习轨迹。
- Reflection：从轨迹中提炼的学习反思。
- Compass：当前任务可用的最小个性化罗盘。
- Hermes：负责 Observe → Reflect → Confirm → Apply → Learn 的协调器。

规则：

- Candidate Reflection 只展示给用户，不进入 Compass。
- Confirmed Reflection 才能进入 Compass。
- Rejected Reflection 作为约束，避免系统继续按它生成。
- Concept Reflection 可只读展示，不允许用户随意确认成偏好。
- 删除证据后，相关 Reflection / Compass / BKT / 图谱 deterministic rebuild。

Compass 只可包含：

- 明确目标
- 已确认偏好
- 当前学科薄弱概念
- 当前任务教学策略
- 可见证据引用
- 降级状态

Compass 禁止包含：

- 隐藏推理
- 原始 prompt
- 人格分数解释
- 未确认跨学科结论
- 与当前任务无关的全量历史

### 10.1 与学习组件、BKT 和 SLR 的边界

| 层 | 保存/决定什么 | 是否直接改变掌握概率 | 是否直接驱动组件 | 作用范围 |
|---|---|---:|---:|---|
| Trail | 带来源的原始学习事件、用户纠正和结果 | 否 | 否 | 用户/学科/对话证据 |
| BKT | Quiz、掌握练习、Flashcard 等事件形成的概念状态 | 是 | 间接 | 当前学科与概念 |
| SLR 支持状态 | 临时教学支持动作与强度 | 否 | 是 | 当前学科/任务 |
| Reflection | 经治理的偏好、目标、策略或概念观察 | 否 | 经 Compass 间接影响 | 学科或全局，需范围约束 |
| Compass | 当前任务最小化的已确认上下文 | 否 | 是 | 当前 Pack/组件 |
| LearningComponentPlan | 组件类型、顺序、依赖和理由 | 否 | 是 | 当前 Pack 的路径版本 |

BKT 负责“学习证据说明概念状态如何变化”，SLR/Compass 负责“现在用什么支持动作”，LearningComponentPlan 负责“把动作排成用户可完成的组件路径”。三者不能互相冒充：浏览不能写成掌握，SLR 不能写成人格标签，Reflection 不能绕过确认直接成为长期结论。

## 11. Why Drawer

所有核心结果都应统一接入 Why Drawer：

- 课件
- Flashcards
- Quiz
- 解题
- 知识图解
- 学习探索
- Deep Research

展示内容：

- 当前目标
- 使用的材料证据
- 薄弱概念
- 显式偏好
- 教学动作
- 是否使用外部补足
- 图片生成状态
- 降级状态

禁止展示：

- 原始 prompt
- 隐藏 chain-of-thought
- 人格分数
- 能力判断
- 未确认的心理推断

## 12. 安全、隐私与开源边界

### 12.1 Auth 与数据隔离

- 注册保持 invite-only，首管理员来自部署凭据或一次性 token。
- 用户上传材料、生成结果、LearningPack、LearnerEvent、学习画像按用户隔离。
- 输出文件下载必须走 authenticated outputs gateway。

### 12.2 模型与 Gateway

- 所有模型调用必须走 Gateway。
- 未配置模型时返回用户可理解的“模型未配置/不可用”提示。
- 支持重试、备用模型、SSE 重连、任务取消和恢复。

### 12.3 开源清理

公开仓库不应包含：

- 真实服务器地址、密码、API key。
- 旧产品身份和旧文案。
- 不属于当前 TraitTutor MVP 的旧功能入口。

允许保留：

- 为兼容历史数据所需的内部迁移代码，但不应在 UI/README/PRD 中作为产品能力展示。

## 13. 当前实现状态

| 模块 | 状态 | 说明 |
|---|---|---|
| 品牌与开源 README | ✅ 已完成 | 中英文 README、Apache-2.0、公开面清理 |
| Big Five / TIPI | ✅ 已完成 | 作为低权重教学 cue |
| 材料分析快照 | ✅ 已完成 | 学科、年级、难度、概念、页码证据、补足决策 |
| LearningPack 复用 | ✅ 已完成 | 同一材料生成课件/闪卡/Quiz |
| LearningComponentSelector | ✅ 已完成基础 | BKT 阶段、SLR 四维、材料 affordance 共同选择 12 类组件 |
| 统一学习画布 | ✅ 已完成基础 | `/space/learning/{packId}` 使用全屏响应式三栏布局展示路径、组件内容与 Why Drawer；进入时自动收起左侧导航 |
| 组件事件与尾部重规划 | ✅ 已完成基础 | 可判分事件更新 BKT，新计划保留已完成组件 |
| Chat product_action | ✅ 已完成基础 | 学习意图返回 pack/plan/components/start_url，避免只返回文字 |
| Courseware | ✅ 已完成 | 结构化课件与来源 |
| Flashcards | ✅ 已完成 | 原子卡片、复习状态、最后一页完成提示 |
| Quiz | ✅ 已完成 | 多题型、逐题作答、解释和回流 |
| LearnerEvent 回流 | ✅ 已完成 | Quiz / Flashcard 写入事件 |
| BKT / 学科隔离 | ✅ 已完成基础 | 已有多学科业务闭环测试 |
| Reflection / Compass API | 🟡 部分完成 | 领域层、API、学习画像总览/学科详情页已融合；删除证据后的跨页面重建展示仍需补齐 |
| 国际化 | ✅ 基础完成 | 核心学习画像、学习画布、登录/设置和导航已有中英文状态回归；仍需持续审计新页面 |
| Why Drawer | 🟡 部分完成 | 学习画布已有，需统一到解题、知识图解、学习探索和 Deep Research 结果 |
| Chat 引用已生成产物 | 🟡 部分完成 | learning artifact source 已有，需加强前端选择体验 |
| 图片生成 | ✅ 已完成基础 | 组件语义门控、与文本并行、两次重试、按 component_id 组装与降级 |
| 语音组件 | 🟡 部分完成 | 统一画布可调用 TTS 并保留文本降级，仍需持久化音频资产 |
| 外部查询补足 | 🟡 部分完成 | 设计明确，生成链路仍需继续收口 |
| 端到端浏览器 smoke | ⬜ 待补齐 | 需要真实 PDF → Quiz → BKT → 再生成 → Why Drawer → 删除证据重建 |

## 14. 发布级验收标准

### 14.1 后端

- 上传 PDF / Word / PPT / Excel / 图片 / 文本后进入同一材料分析快照。
- Quiz 作答写入 LearnerEvent 并更新 BKT。
- Flashcard 复习写入 LearnerEvent 并影响待复习。
- Courseware 不伪造成掌握证据。
- 删除证据后 profile / BKT / 图谱可 deterministic rebuild。
- 外部补足失败不阻断生成。
- 图片生成失败不阻断文本产物。
- Gateway 重试和备用模型路径可验证。
- 多学科、多对话、多 artifact 业务闭环测试通过。
- 无证据先诊断；错题转讲解/例题；支持后转迁移。
- 四个 SLR 支持维度都会改变组件组合，但不输出学习风格或能力标签。

### 14.2 前端

- 我的学习展示进行中、待复习、材料和历史产物四个区域。
- 统一画布在同一页面展示路径、当前组件和“为什么这一步”。
- 用户不需要选择课件、闪卡或 Quiz；后台按组件计划调用它们。
- 已生成产物可在主页 Chat 选择并二次问询。
- Flashcard 最后一页显示完成提示。
- Quiz 逐题作答后能看到错题、概念和回流状态。
- Why Drawer 中英文文案完整。
- 主页隐藏不适合直接展示的课件/闪卡/Quiz快捷入口，但不删除功能页。
- 当前对话路由高亮最近对话，不错误高亮主页。
- favicon、品牌图标、模型图标正常显示。

### 14.3 Demo 流程

必须能现场演示：

1. 上传真实 PDF。
2. 查看学科、年级、难度、概念和页码证据。
3. 生成 Quiz 并故意答错部分题。
4. 查看 BKT、待复习和图谱变化。
5. 基于薄弱概念再生成闪卡或课件。
6. 打开 Why Drawer 解释生成依据。
7. 在主页 Chat 选择已生成课件/闪卡/Quiz 继续追问。
8. 删除一条证据，确认相关状态重建。

## 15. 测试计划

### 15.1 必跑后端测试

```bash
.venv/bin/python -m pytest \
  tests/traittutor/test_business_learning_loop.py \
  tests/traittutor/test_learning_pack_events.py \
  tests/traittutor/test_generate_suite.py \
  tests/traittutor/test_personalization.py \
  tests/api/test_personalization_router.py \
  tests/services/test_evolution_core.py -q
```

### 15.2 必跑前端测试

```bash
cd web
npm run test:node
npm run lint
npm run build
```

如果生产 build 需要拉取 Google Fonts，离线环境可能失败；发布环境需保证联网或改成本地字体。

### 15.3 必补浏览器 smoke

```text
profile → material upload → material analysis → generate quiz
→ answer quiz → inspect learner model → generate flashcards/courseware
→ open Why Drawer → chat with generated artifact → delete evidence → rebuild
```

## 16. 近期优先级

P0：

- 线上部署稳定化：CSS/图标/模型配置/API path/base path 不再反复出错。
- 真实 PDF demo 路径跑通并录屏。
- GitHub 开源仓库发布：README 中英、脱敏、旧产品身份清理。

P1：

- Why Drawer 统一接入解题、知识图解、学习探索、Deep Research。
- Chat 中选择 generated artifacts 的交互完善。
- 图片生成重试、状态记录和最终组装加强。
- 外部查询补足接入课件/闪卡/Quiz 主链路。

P2：

- Reflection / Compass 的删除证据后重建可视化，并把状态变化回显到学习画布。
- 无外网构建方案：本地字体、本地图标资源、部署前自动 health check。
