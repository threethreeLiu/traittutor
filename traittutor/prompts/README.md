# Prompt assets

All checked-in prompt text is organized below this directory by runtime
function. Python modules should load assets through `traittutor.prompts` (or
the shared `PromptManager`) instead of resolving paths relative to their own
package.

| Directory | Scope |
| --- | --- |
| `agents/` | Chat, research, question, notebook, visualization, math animation, and vision agents |
| `capabilities/` | Solve, mastery, attached-context exploration, and Obsidian capability prompts |
| `generation/` | Courseware, flashcard, quiz, knowledge-graph, and podcast generation |
| `learning/` | Learning-path diagnostics, explanations, practice, review, and notebook generation |
| `memory/` | Memory consolidation and focus metadata |
| `tools/` | Per-tool prompt hints |

Use `asset_path(...)` for a direct asset and `PromptManager.load_prompts(...)`
for localized agent bundles. Runtime prompt loading uses this canonical asset
tree only; package-local prompt layouts are not part of the product contract.
