# TraitTutor 产品宣传主页 — 执行计划

## 任务理解
- 基于 PRD 设计产品宣传主页（单页 HTML），**突出"人格个性化"**核心卖点
- 必备元素：试用按钮 → https://threethreeliu.top/traittutor-all-web/
- 主叙事（PRD 产品立场）：「人格决定怎么教，BKT 决定教什么」；Big Five/TIPI → SRL 支持维度 → 组件编排第一输入

## 阶段 1 — 内容架构（从 PRD 提炼）
- Hero：产品定位 + 主标语 + 双 CTA（试用 / 了解原理）
- 痛点：AI 记得你问过什么，却不知道你真正学会了什么
- 人格个性化核心章节：Big Five → SRL 六维支持（支架/节奏/反馈/结构/挑战/互动）→ 组件编排
- 个性化改变了什么：结构、节奏、反馈、挑战（四张卡）
- 核心流程：目标/材料 → 支持方式 → 学习证据 → 安排教学 → 生成校验 → 作答反馈
- 五大功能：学习入口 / 研究入口 / 我的学习 / 学习工具 / 性格设置
- 信任与边界：不做诊断、不代写、记忆可管理、证据驱动 BKT
- 最终 CTA + Footer

## 阶段 2 — 视觉与实现
- 单文件 HTML（内联 CSS + 少量 JS），无需构建
- 视觉规范：低饱和暖色调（米白底 + 陶土橙点缀 + 墨色文字），充足留白，清晰层级
- 交互：IntersectionObserver 滚动渐入、Big Five 雷达/维度条动效、组件编排示意动画
- 响应式：移动端适配
- 输出：/mnt/agents/output/traittutor-landing/index.html

## 阶段 3 — 交付
- website_version_manager build_version（type: html）
- 验证试用链接正确性
