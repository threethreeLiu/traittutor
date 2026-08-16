# Learning Governance 设计

## 目标与非目标

目标是为错误、误概念、修复和复习提供统一、安全的读取契约，同时继续以现有
LearningProgress、LearnerEvent 和 Misconception 为事实源。本层不写事件、不判分、
不更新 BKT，也不复制 LearningPack 内的 server-held answer。

## 数据流

```text
authenticated owner workspace -> OwnerBoundLearningStore --+
canonical multi-user ledger ----> owner/subject/KC filter ----> safe DTO snapshot
owner-bound MisconceptionStore -----------------------------+
```

公开投影不持久化，因此不存在与 canonical truth 的双写或漂移。后续 router 只能从
认证上下文创建上述三个输入。

## 隔离与归因

- Owner：`OwnerBoundLearningStore.owner_id`、repository owner 和
  `MisconceptionStore.owner_id` 必须完全一致；ledger 查询再次校验 event owner。
- Subject：非空 `LearningProgress.subject_id` 是 canonical answer event 写入的权威分区；
  与查询 subject 不一致的路径完全排除。
- 无 subject 或缺少 owner/subject/KC 归属的记录不进入公开投影。
- Evidence：server-graded、valid、reliable 证据显示 `verified`；其余明确显示
  `attribution_pending`。找不到任何分区依据时不返回，绝不从 book/module 名猜测。
- Review：只投影具有 canonical source event 和权威 subject/KC 归属的记录。

## 数据最小化

所有 DTO 使用 `extra="forbid"` 和字段白名单。允许题目 ID、KC、状态、时间、次数及
误概念 pattern；禁止 expected answer、learner answer、rubric、correct rule、hidden
prompt、raw prompt。`rubric_ref` 只留在服务端 Misconception truth，不进入 DTO。

## 持久化 Misconception

`MisconceptionStore` 必须同时提供 `path` 和 `owner_id`，使用进程内 `RLock`、
OS 文件锁和同目录原子替换。每次读写都会刷新文件，
避免多个 store 实例丢失彼此更新；文件 owner 或 item owner 不匹配时 fail closed。

## 已知接线边界

- API/router 和认证 workspace factory 已属于 Layer 2；它们仍只能创建本层的只读
  projection，不能把 learner-safe DTO 当作判分输入。
- LearningPack repair/review mutation 仍不属于本层。`ReviewTask` 只保存调度状态，
  没有 server-held item、answer key、rubric 或 submission identity；因此本层绝不能
  接受一个 caller-provided `correct` 来写 canonical BKT。
- 生产 review adapter 必须在 LearningPack 的私有 artifact/repair truth 中完成判分，
  再以同一 `request.event_id` 调用 `CanonicalAnswerEventChain.record_server_graded`
  （`surface_type="review"`），最后更新 review schedule。它还必须持久化如下
  provenance：canonical original-event ID（而不是浏览器 component token）、
  `subject_id`、reliable `kc_id` 及 server-owned retry `question_id`。缺任一项时
  仍可更新复习队列，但 canonical event 必须是 weak/pending，不能更新 BKT。
- retrieval/flashcard self-rating 是参与数据，只能调整间隔，不得调用
  `record_server_graded` 或更新 BKT。没有可恢复服务端题目与 canonical 归属的
  quiz review 不进入当前学习治理投影。
