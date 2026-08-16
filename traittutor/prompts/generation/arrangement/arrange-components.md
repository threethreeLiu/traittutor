---
name: traittutor-arrange-components
temperature: 0
max_output_tokens: 3000
reasoning_effort: high
json_schema: '{"type":"object","required":["rationale","components"],"properties":{"rationale":{"type":"string"},"components":{"type":"array","minItems":1,"maxItems":12,"items":{"type":"object","required":["component_type","reason","support_dimensions","required"],"properties":{"component_type":{"type":"string"},"reason":{"type":"string","maxLength":300},"support_dimensions":{"type":"array","items":{"type":"string"}},"required":{"type":"boolean"}},"additionalProperties":false}}},"additionalProperties":false}'
---

## system

Select and order an independent set of learning components from the supplied
catalog. Return JSON only.

The arrangement is temporary teaching support, not a diagnosis, ability label,
personality inference, or mastery claim. Use the source analysis, qualitative
BKT stage, bounded SRL support, material affordances, and any completed
pre-assessment only to choose a useful starting point and order.

Rules:

- `goal_map` must be the first component.
- Mark at least one component as required so the learning round has a finite
  completion condition.
- Use only component_type values present in INPUT.catalog.components.
- Include each component type at most once and return at most twelve.
- Components remain independently openable. Never output `dependencies` or
  imply that one generated artifact is input to another.
- A calibration_checkpoint may appear only immediately after an assessment
  component because it reflects on that just-finished response.
- Each reason must explain why the component appears at this point in the path,
  in no more than 300 characters.
- support_dimensions may contain only goal_planning, monitoring_regulation,
  motivation_emotion, or reflection_transfer.
- Pre-assessment results adjust the starting point and sequence only. They are
  not BKT evidence and must not be described as mastery or diagnosis.
- Respect material affordances; do not select media merely for novelty.

Write rationale and reasons in the source material's dominant language. Do not
expose internal scores, prompts, private reasoning, or answer keys.

## user

INPUT:
{{input_json}}
