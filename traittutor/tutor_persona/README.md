# Typed Tutor Persona

本模块实现 ADR-0006 的白名单教练形象契约。它保存用户显式选择的称呼方式、呈现身份、语音、表达风格、主动程度、quiet hours 与可访问性设置，并把这些设置确定性编译为独立的 `TutorPersonaContract`。

## 边界

- profile/store 按 owner 绑定，版本更新使用 CAS 与幂等键。
- 所有模型 `extra="forbid"` 且 frozen；没有任意 prompt/body 字段。
- compiler 不调用 LLM，不读取学习状态，只输出表达与呈现字段。
- reminder consent 与 quiet-hours 只产生 delivery-free eligibility decision；不在
  compiler、Context attachment 或 prompt 内，且没有调度/发送能力。
- context adapter 返回 typed attachment，不生成 system prompt。
- 本模块不判分、不提供答案、不读写 KC/BKT，也不能覆盖安全策略。
- 在线 `ContextAssembler` 只在认证 owner 的当前 profile 存在时读取该
  attachment，并仅把 expression contract 与 `profile_ref`/contract hash
  放入受限 prompt context；identity、voice、quiet hours、accessibility 与
  legacy 自由文本均不会进入 generation。

## 组成

- `models.py`：版本化 profile 与白名单设置。
- `store.py`：文件锁、原子替换、owner 隔离、CAS 和幂等重放。
- `compiler.py`：确定性 Persona Contract。
- `context_adapter.py`：与 teaching/learning context 分离的 typed attachment。
- `service.py`：供后续 owner-derived router 使用的应用服务。
- `reminders.py`：时区安全的 consent/quiet-hours eligibility gate；未来 owner-bound
  scheduler 必须消费此 gate，不能将 persona 风格当作联系许可。

## 示例

```python
from traittutor.tutor_persona import TutorPersonaService, TutorPersonaStore

service = TutorPersonaService(TutorPersonaStore("authenticated-owner"))
profile = service.get_profile()
contract = service.preview(profile)
```

HTTP router 与 generation composition-root 都从认证会话派生 owner。缺失或
关闭 persona 是无副作用的 no-op；存储/归属异常会 fail closed 并使快照
降级，而不会生成默认 persona 或回退到 legacy 文本。
