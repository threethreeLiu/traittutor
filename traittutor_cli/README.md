# TraitTutor CLI

The CLI is a local operations surface for the TraitTutor MVP. It is meant for starting the app, configuring models, inspecting knowledge/session state, and running chat or learning exploration from a terminal.

## Commands

```bash
traittutor start
traittutor serve
traittutor chat
traittutor run chat "Explain this topic"
traittutor run deep_research "Compare retrieval strategies" --config-json '{"mode":"report","depth":"standard"}'
traittutor kb list
traittutor notebook list
traittutor session list
traittutor memory show
traittutor models list
traittutor config show
traittutor init
```

## Product boundary

Courseware, flashcards, quizzes, guided solving, knowledge-diagram, learning-path, and Humanizer modes are preserved in the TraitTutor product. Structured courseware/flashcard/quiz artifacts are generated through the TraitTutor web/API generation flow, where material analysis, learner profile, BKT/KG signals, validation, persistence, and artifact routing are available together.

The CLI intentionally exposes only the current MVP capabilities:

- `chat`
- `deep_research`

The `research` alias resolves to `deep_research`.
