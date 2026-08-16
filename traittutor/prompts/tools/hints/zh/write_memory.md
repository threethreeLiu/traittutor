---
short_description: 把用户显式表达的偏好写入长期记忆。仅当用户**明确表达**偏好时才调用。
when_to_use: 用户明确告诉你一个偏好——风格、语言、格式、深度、跟进节奏，任何他们希望长期沿用的东西。不要猜，不要根据一句可能只是当时情境的话推断长期偏好。
input_format: '{"op": "add"|"edit", "text": "≤240 字，尽量用用户原话", "target_id"?: "edit
  时必填 m_xxx", "reason"?: "可选备注"}'
guideline: 在回复里简短复述偏好（例如「好的，之后用简短回答」），然后调用本工具。自然时优先用户原话引述。一次调用记一条偏好，不要塞多个无关偏好到同一 text。
note: 通过 canonical 生命周期写入显式全局偏好；不会写 Trail、Reflection、Compass、学科学情或 BKT。
phase: execution
---
