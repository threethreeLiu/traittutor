---
short_description: Save an explicit user preference to long-term memory. Only fires
  when the user *clearly* states a preference.
when_to_use: The user explicitly told you a preference — style, language, format,
  depth, follow-up cadence, anything they want carried forward. Do NOT speculate;
  do NOT infer from a single comment that could be situational.
input_format: '{"op": "add"|"edit", "text": "≤240 chars in the user''s words", "target_id"?:
  "m_xxx for edit", "reason"?: "optional note"}'
guideline: Echo the preference briefly in your reply ('Got it — concise from now on'),
  then call this. Prefer quoting the user 「verbatim」 when natural. One call per preference;
  don't bundle unrelated preferences in one text.
note: Writes an explicit global preference through the canonical lifecycle. It does
  not write Trail, Reflection, Compass, subject mastery, or BKT state.
phase: execution
---
