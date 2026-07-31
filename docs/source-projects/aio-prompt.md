# aio-prompt Source Project

Source path: `/Users/lrm/Documents/code/aio-prompt`

## Role In TraitTutor

aio-prompt was reviewed as a source of structured learning-output patterns.
TraitTutor no longer carries source prompt files forward directly; runtime
prompts are first-party Markdown assets rewritten for the Learning Space
generation graph. GitLab/Nacos deployment workflows are out of scope.

## Reviewed Source Areas

- Shared tutor, explanation, follow-up, and question-generation patterns.
- Knowledge-map flashcard and quiz behavior.
- Study-guide generation and merge behavior.
- External synchronization workflow, which is not part of TraitTutor runtime.

## Runtime Prompt Format

TraitTutor runtime prompt assets are Markdown files with frontmatter metadata
and `## system` / `## user` blocks. Generation prompts are loaded through
`traittutor/services/prompt/markdown.py`.

## MVP Prompt Sources

The source project informed TraitTutor's source-grounded prompt behavior, but
runtime prompt assets now live only as Markdown under
`traittutor/generate/prompts/` and are rewritten for the current Learning Space
pipeline:

- Courseware: intent/material-model analysis, SLR support planning, then mixed lesson rendering.
- Flashcards: source-grounded active-recall card batches.
- Quiz: source-grounded material review, question variation, and objective checks.
- Tutor explanation style: folded into the chat and guided-solve Markdown prompts.

## Migration Notes

- Keep source-grounded and schema-constrained behavior.
- Do not migrate Nacos, GitLab CI, environment synchronization, or legacy prompt files.
- Treat strict JSON prompts carefully: stream progress immediately, but show generated flashcards/quiz only after batch JSON validation.
