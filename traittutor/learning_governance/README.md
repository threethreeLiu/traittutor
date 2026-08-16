# Learning Governance

Learning Governance 是现有学习事实源之上的只读、learner-safe 投影。它聚合
`LearningStore`、canonical `LearnerEventLedger` 和持久化
`MisconceptionStore`，供后续 `/errors`、`/misconceptions`、`/reviews` API 使用。

## 当前能力

- 查询显式 `owner + subject + KC` 分区下的错误、修复、误概念和复习。
- 只有 canonical event 明确命中分区时才展示；弱证据标记为
  `attribution_pending`，不猜测学科或 KC，也不读取旧 ReviewTask 投影。
- learner-safe DTO 不包含答案、rubric、正确规则和原始 prompt。
- `MisconceptionStore(path, owner_id=...)` 是唯一受支持的持久模式，提供文件锁
  和原子写入。

## 使用边界

```python
source = OwnerBoundLearningStore(owner_id=current_user.id, store=LearningStore())
repository = LearningGovernanceRepository(
    owner_id=current_user.id,
    learning_source=source,
    event_ledger=ledger,
    misconception_store=MisconceptionStore(path, owner_id=current_user.id),
)
snapshot = LearningGovernanceService(repository).snapshot(subject_id="math")
```

`OwnerBoundLearningStore` 必须由认证 composition root 使用当前用户 workspace 构造；
客户端不得提供 owner。当前 Layer 1 只提供 read API，不改变 BKT、判分、
LearningProgress 或 LearningPack。尤其 `ReviewTask` 没有服务端答案或 submission
identity，所以它不能接收客户端的判分结论；Pack repair/review 的后续 mutation adapter
必须从私有 artifact 取得答案，并通过 `CanonicalAnswerEventChain` 先写事件再派生。

详见 [DESIGN.md](DESIGN.md)。
