# TraitTutor

<p align="center">
  <strong>面向学习者的 AI 学习工作台：基于真实材料生成课件、闪卡、测验，并沉淀可解释的学习记忆。</strong>
</p>

<p align="center">
  <a href="README.md">English</a>
  ·
  <a href="#核心功能">核心功能</a>
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

TraitTutor 用来把真实学习材料变成可用的学习产物。你可以上传 PDF、Word、PPT、Excel、图片或文本；系统会分析材料，识别学科、年级、难度、概念和页码证据，再生成课件、闪卡、测验，并在练习后更新学习画像。

产品边界很明确：人格 / 偏好信号只用于调整讲解方式，不用于诊断、能力标签或学习风格判定。

## 核心功能

- **基于材料的分析快照**：保存学科、年级段、难度、语言、候选概念、页码证据和是否需要外部补足。
- **一个材料，多种产物**：课件、闪卡、Quiz 可以共用同一个 Learning Pack，不需要重复上传和重复分析。
- **学习事件回流**：Quiz 作答、闪卡复习会写入可审计的 LearnerEvent，并更新 BKT 风格的概念掌握进度。
- **可解释学习画像**：Reflection / Compass 记忆治理把显式偏好、推断支持、概念进度、证据和删除后重建分开管理。
- **聊天中的学习工作流**：支持聊天、Deep Research、解题、学习探索、知识图解，以及对已生成产物的二次追问。
- **基于特质的支持边界**：个人画像用于调整表达与支持动作，不把人格分数变成标签或判断。
- **统一模型网关**：生成和聊天统一走 Gateway，便于路由、重试、备用模型和调用审计。

## 工作流

```text
上传 / 粘贴材料
        ↓
MaterialAnalysisSnapshot
        ↓
LearningPack
        ↓
PersonalizationContext + SLR 支持动作
        ↓
课件 / 闪卡 / Quiz / 聊天模式
        ↓
LearnerEvent
        ↓
BKT 风格概念进度 + 知识图谱 + 学习画像
```

课件完成只作为参与证据。长期掌握度只由可解释的学习事件更新，例如 Quiz 作答、闪卡复习和掌握练习。

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
