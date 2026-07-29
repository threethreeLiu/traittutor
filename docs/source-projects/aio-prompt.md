# aio-prompt Source Project

Source path: `/Users/lrm/Documents/code/aio-prompt`

## Role In TraitTutor

aio-prompt is the prompt source for structured learning outputs. TraitTutor should migrate selected YAML prompt files into a local prompt catalog, not the GitLab/Nacos deployment workflow.

## Observed Structure

- `common/`: shared tutor, explain, follow-up, question-generation prompts.
- `knowledge/flashcard/`: knowledge-map flashcard prompts.
- `knowledge/quiz/`: knowledge-map quiz prompts.
- `knowledge/sg/`: study-guide operation prompts.
- `workflow/sg/`: study-guide generation and merge prompts.
- `workflow/card/`: study-guide card prompts.
- `workflow/quiz/`: study-guide quiz prompts.
- `scripts/sync-to-nacos.sh` and `.gitlab-ci.yml`: external synchronization workflow, not part of TraitTutor runtime.

## Prompt Format

Prompt files are YAML and commonly include:

- `type`
- `name`
- `module_name`
- `description`
- generation settings such as `temperature`, `reasoning_effort`, `max_output_tokens`, `fallback`
- optional `json_schema`
- `prompt_structure` with system/user prompt blocks

## MVP Prompt Sources

- Courseware/study guide: `workflow/sg/sg-full-note.yml` and related study-guide prompts.
- Flashcards: `knowledge/flashcard/km-card-note.yml`.
- Quiz: `knowledge/quiz/km-question-note.yml`.
- Tutor explanation style: `common/ai-tutor.yml`.

## Migration Notes

- Keep source-grounded and schema-constrained behavior.
- Do not migrate Nacos, GitLab CI, or environment synchronization.
- Treat strict JSON prompts carefully: stream progress immediately, but show generated flashcards/quiz only after batch JSON validation.
