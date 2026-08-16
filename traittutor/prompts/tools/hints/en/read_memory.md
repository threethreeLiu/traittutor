---
short_description: 'Read the user''s persistent memory: recent learning summary, profile,
  knowledge scope, and explicit preferences.'
when_to_use: When the answer's tone, depth, examples, or recommendations can be meaningfully
  personalised to THIS user. Skip for pure factual / computational / translation questions.
input_format: No parameters. Returns active, non-sensitive canonical memory items.
guideline: Call at most once per turn. Use memory to shape HOW you explain — examples,
  depth, follow-ups. Do NOT quote memory back at the user as factual content.
note: Retrieval is owner-scoped. Subject/project/research memories retain their
  explicit partition identifiers and never count as mastery evidence.
phase: exploration
---
