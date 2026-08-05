# TraitTutor

<p align="center">
  <strong>目标驱动的 AI 学习教练：把问题和真实材料转化为可自适应、可追溯证据的学习路径。</strong>
</p>

<p align="center">
  <a href="README.md">English</a>
  ·
  <a href="#核心功能">核心功能</a>
  ·
  <a href="#适用对象与场景">适用对象与场景</a>
  ·
  <a href="#技术方案与创新点">技术方案</a>
  ·
  <a href="#快速开始">快速开始</a>
  ·
  <a href="#验证命令">验证命令</a>
  ·
  <a href="#贡献">贡献</a>
</p>

<p align="center">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white">
  <img alt="Next.js" src="https://img.shields.io/badge/Next.js-16-000000?logo=nextdotjs&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-ready-009688?logo=fastapi&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/License-Apache--2.0-blue">
</p>

TraitTutor 把一个学习目标、一道问题或真实学习材料转化为会持续根据证据调整的学习路径。用户可以不上传材料直接开始，也可以在需要时加入 PDF、Word、PPT、Excel、图片或文本；系统会结合来源证据、BKT 风格概念信号、学科支持动作和材料 affordance 安排下一学习组件，并在练习过程中始终呈现安排依据与学习证据。

产品边界很明确：人格 / 偏好信号只用于调整讲解方式，不用于诊断、能力标签或学习风格判定。

## 适用对象与场景

TraitTutor 面向需要持续学习支持的自主学习者、教师/助教和学习产品团队，而不只是回答一次问题。用户可以从学习目标、问题或真实材料三个等价入口开始：例如把教材章节组织成练习路径、围绕真实 PDF 备考，或在 Quiz/闪卡证据显示薄弱概念后继续安排学习。

## 核心功能

- **基于材料的分析快照**：保存学科、年级段、难度、语言、候选概念、页码证据和是否需要外部补足。
- **目标驱动的学习路径**：从目标、材料或问题创建一个 Learning Pack 和确定性组件计划，不要求用户先选择生成器。
- **全屏学习画布**：同时展示学习路径、当前组件和“为什么这一步”的学习依据；进入学习后工作区侧栏自动收起。
- **一个材料，多种产物**：课件、闪卡、Quiz 可以共用同一个 Learning Pack，不需要重复上传和重复分析。
- **学习事件回流**：Quiz 作答、闪卡复习会写入可审计的 LearnerEvent，并更新 BKT 风格的概念掌握进度。
- **可解释学习画像**：Reflection / Compass 记忆治理把显式偏好、推断支持、概念进度、证据和删除后重建分开管理。
- **聊天中的学习工作流**：支持聊天、Deep Research、解题、学习探索、知识图解，以及对已生成产物的二次追问。
- **基于特质的支持边界**：个人画像用于调整表达与支持动作，不把人格分数变成标签或判断。
- **统一模型网关**：生成和聊天统一走 Gateway，便于路由、重试、备用模型和调用审计。

## 工作流

```text
学习目标 / 材料 / 问题
        ↓
LearningPack + MaterialAnalysisSnapshot（有材料时）
        ↓
BKT 概念证据 + 学科 SLR 支持 + 材料 affordance
        ↓
LearningComponentPlan
        ↓
全屏学习画布
        ↓
讲解 / 评估 / 主动回忆 / 图解 / 语音执行器
        ↓
LearnerEvent → BKT / 知识图谱 / 学习画像
        ↓
只重规划未开始的路径尾部 → 下一学习组件
```

课件、闪卡和 Quiz 是组件执行器与历史产物，不是主页上的模式选择。课件完成只作为参与证据。长期掌握度只由可解释的学习事件更新，例如 Quiz 作答、闪卡复习和掌握练习。

学习画布才是真正的学习目的地；聊天负责接收目标、问题、材料追问，以及围绕已保存产物的二次问询。TraitTutor 自有 UI 状态支持中英文切换，用户输入和材料原文保持原始语言。

## 技术方案与创新点

```text
Next.js 学习工作台
        ↓ 学习目标 / 问题 / 材料
FastAPI 产品 API → 材料分析 → Learning Pack + 组件计划
        ↓                              ↓
已配置的模型网关                    可持久化学习事件
        ↓                              ↓
课件 / 闪卡 / Quiz                  BKT 风格概念证据 + 学习画像
```

核心设计是在模型生成内容时，仍让学习顺序保持可确定、可解释：材料证据、概念信号、学科支持动作和用户明确偏好共同决定组件计划，生成产物只负责执行该计划。可判分的 Quiz 作答和闪卡复习会写入可审计事件；系统仅重规划尚未开始的路径尾部，不改写已完成的学习证据。设计决策见 [ADR-001](docs/decisions/ADR-001-goal-first-learning-component-canvas.md)。

## 开源、依赖与服务边界

TraitTutor 仓库代码采用 [Apache-2.0](LICENSE) 协议；Python 依赖以 `pyproject.toml` 为准，前端依赖以 `web/package.json` 为准，第三方依赖各自保留原有许可证。模型网关可以连接本地配置的商业或开源模型服务，但仓库不包含服务商密钥、模型权重、私有用户材料或专有服务输出。完整说明见[开源、服务商、数据与来源边界](docs/OPEN_SOURCE_BOUNDARIES.md)。

## 路线图与落地计划

当前版本聚焦可运行的“目标 → 学习路径”、材料分析、练习证据回流和可解释下一步。近期优先级包括生成评测门槛、持久化 TTS 资产，以及覆盖“材料 → 练习 → 产物追问”的浏览器 smoke 测试。完整产品范围、非目标、验收标准与优先级见 [PRD](docs/PRD.md)。

## 快速开始

### 环境要求

- Python 3.11、3.12 或 3.13
- Node.js 20+
- npm

### 后端

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

traittutor serve
```

### 前端

```bash
cd web
npm install
npm run dev
```

本地运行配置来自 `web/.env.local`、`config/models.local.yaml` 等已忽略文件。请根据示例文件创建本地配置，不要提交真实模型密钥。

单机 Ubuntu 线上部署请阅读 [DEPLOYMENT.md](DEPLOYMENT.md)。首次运行 `bootstrap_production_server.sh`，之后每个已提交版本使用 `deploy_production.sh deploy`；同一脚本还提供状态检查、日志查看和回滚命令。

## 配置模型

模型配置采用本地优先：

- 将 `config/models.local.example.yaml` 复制为 `config/models.local.yaml`；
- 配置 provider profile 和 active model；
- 真实 key 只放在本地或服务器环境中；
- 新增生成路径必须通过现有 Gateway，不要直接调用模型供应商。

## 验证命令

后端核心回归：

```bash
.venv/bin/python -m pytest \
  tests/traittutor/test_business_learning_loop.py \
  tests/traittutor/test_learning_pack_events.py \
  tests/traittutor/test_generate_suite.py \
  tests/traittutor/test_personalization.py \
  tests/api/test_personalization_router.py \
  tests/services/test_evolution_core.py -q
```

前端：

```bash
cd web
npm run test:node
npm run lint
npm run build
```

如果 Next.js 构建时需要拉取远程字体，`npm run build` 可能需要网络权限。

## 目录结构

```text
traittutor/                 FastAPI 后端、生成链路、Gateway、学习画像
traittutor_cli/             本地 CLI
web/                        Next.js 前端
tests/                      后端与业务闭环回归测试
web/tests/                  前端 node 回归测试
config/                     运行配置示例
scripts/                    本地运维辅助脚本
docs/source-projects/       历史来源项目说明
```

## 产品安全边界

TraitTutor 把画像和记忆信号当作可调整的教学上下文。它不会：

- 诊断人格、认知或能力；
- 用画像数据声称客观学习增益；
- 把浏览、保存或看完课件当作已掌握；
- 在解释中暴露隐藏 prompt 或私有推理。

Why Drawer 应展示当前目标、材料证据、薄弱概念、显式偏好、教学动作和降级状态；不展示隐藏思维链、原始 prompt 或人格判断。

## 贡献

提交 PR 前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。请保持改动在 TraitTutor 学习产品边界内，为行为变更补测试，并尽量保持中英文文案同步。

## 安全

请不要在公开 issue 中提交凭证、私有部署 URL、模型 key 或用户材料。详见 [SECURITY.md](SECURITY.md)。

## 许可证

TraitTutor 使用 [Apache License 2.0](LICENSE)。
