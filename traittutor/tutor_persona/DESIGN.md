# Typed Tutor Persona 设计

## 目标

实现 ADR-0006：用户可配置的教练形象具有稳定、版本化、跨文本/语音一致的表达契约，同时无法改变判分、答案、KC、BKT 或安全策略。

## 非目标

- 不迁移或注入 legacy `PERSONA.md` 自由文本。
- 不生成 system prompt。
- 不决定内容正确性、教学顺序、题目难度、掌握度或安全策略。
- 不在本模块注册 HTTP router 或修改生成主链路。

## 数据流

```text
authenticated owner
       |
       v
TutorPersonaStore --CAS/idempotency--> TutorPersonaProfile vN
       |                                      |
       |                                      v
       +----------------------------> deterministic compiler
                                              |
                                              v
                                    TutorPersonaContract
                                              |
                                              v
                                  typed context attachment
                                              |
                                              v
                            ContextAssembler (expression + provenance only)
                                              |
                                              v
                                  bounded generation prompt constraints
```

## 契约

`TutorPersonaProfile` 是 frozen、`extra="forbid"` 的完整版本快照。自由编辑面只允许短 display name；它受字符白名单约束。称呼、语气、直接程度、幽默、鼓励、反馈格式、主动程度、表情、头像和声音均使用闭集枚举。quiet hours 与 accessibility 是 typed 子模型。

`TutorPersonaContract` 仅含 identity、expression、modality、quiet hours 和固定 safety-version 引用。它没有 instruction/body/prompt 字段；固定 safety version 只是审计引用，不是可编辑 override。

主动提醒还要求 profile 中独立的 `reminder_consent=true`，且 `proactivity` 不能为
`off`。`reminders.py` 按 profile 的 IANA 时区和 quiet-hours 做纯 eligibility
decision；跨午夜与相同开始/结束时间都 fail closed。该 decision 不入 contract、
Context 或 prompt，也不会创建、排队或发送任何通知。

## Store 一致性

- 构造时绑定 owner；所有读取和幂等查找都包含 owner 条件。
- profile 版本只追加，不原地重写历史版本。
- CAS 在文件锁内比较 `expected_version`。
- 幂等记录在 CAS 之前检查；相同 key 与相同请求返回原版本，相同 key 与不同请求 fail closed。
- idempotency key 只持久化 SHA-256，不保存原 key。
- profile 与幂等记录在同一 JSON 原子替换中提交。

## 威胁模型

- **越权对象读取**：owner 不来自请求 payload；store 不提供跨 owner ID 查询。
- **并发丢更新**：文件锁 + CAS，仅一个相同 expected version 可成功。
- **幂等 key 改义**：request hash 不同会抛出 conflict。
- **Prompt 注入**：没有自由文本 instruction；display name 使用单行字符白名单，compiler 输出结构化数据而非 prompt。
- **Persona 越界**：contract schema 本身不具备 grading/answer/BKT/security override 字段；差分测试冻结此边界。

## 后续接线

Router 应挂载于 ADR/PRD 确认的 typed Tutor Persona 路径，从 `get_current_user()` 派生 owner，并把 version conflict 映射为 409。生成 composition root 只能把 `TutorPersonaContext` 作为与 teaching/learning context 并列的 style attachment。

当前在线接线由 `ContextAssembler` 完成：它用 owner-bound store 的
`get_current()`（而非 get-or-create）读取 profile，先检查 store/profile
归属，再通过 `TutorPersonaContextAdapter` 编译。冻结 snapshot 仅保存
`profile_ref` 与 contract hash；提示词接收的结构仅有这两个 provenance
字段加 closed-enum `expression`。identity、modality、quiet hours、
accessibility、答案、KC、BKT 与安全状态不在投影内。缺失/关闭 profile 是
no-op；存储异常或归属不匹配则忽略 attachment 并记录 degradation，绝不使用
legacy 自由文本或隐式创建 profile。
