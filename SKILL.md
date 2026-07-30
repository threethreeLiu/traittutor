# TraitTutor CLI Skill

> Teach an AI agent to configure, run, and inspect the TraitTutor MVP from the command line.

## When to Use

Use this skill when the user wants to:

- Set up local TraitTutor workspace settings.
- Start the API server or the full local Web app.
- Chat with TraitTutor from the terminal.
- Run learning exploration / research from the terminal.
- Manage knowledge bases, notebooks, sessions, memory, and model configuration.
- Check the CLI commands that match the current MVP product surface.

TraitTutor courseware, flashcard, quiz, guided solving, learning exploration, knowledge-diagram, learning-path, and Humanizer modes are first-class product surfaces. The structured learning artifacts are generated through the Web/API learning flow so the result can be saved with material metadata, learner-profile context, trace metadata, and learning-pack records.

## Prerequisites

- Python 3.11+
- Source checkout installed with `pip install -e .`
- Run `traittutor init` for first-time local setup.

## Commands

### Chat and Learning Exploration

```bash
traittutor chat
traittutor chat --kb textbook --tool rag --tool web_search

traittutor run chat "Explain Fourier transform"
traittutor run deep_research "Attention mechanisms" --config-json '{"mode":"report","depth":"standard"}'
traittutor run research "Attention mechanisms" --config-json '{"mode":"report","depth":"standard"}'
```

`traittutor run` supports `chat`, `deep_research`, and the alias `research`.

Common options:

- `--session <id>`: resume an existing session.
- `--tool/-t <name>`: enable a tool for the turn.
- `--kb <name>`: attach a knowledge base.
- `--notebook-ref <ref>`: attach notebook records as `<notebook_id>:<rec1>,<rec2>`.
- `--history-ref <id>`: attach previous sessions.
- `--language/-l <code>`: response language.
- `--config <key=value>` or `--config-json <json>`: capability configuration.
- `--format/-f <rich|json>`: output format.

### Knowledge Bases

```bash
traittutor kb list
traittutor kb info <name>
traittutor kb create <name> --doc file.pdf
traittutor kb create <name> --docs-dir ./papers
traittutor kb add <name> --doc more.pdf
traittutor kb search <name> "query text" --mode hybrid
traittutor kb set-default <name>
traittutor kb delete <name> --force
```

### Notebooks

```bash
traittutor notebook list
traittutor notebook create <name> --description "..."
traittutor notebook show <notebook_id> --format json
traittutor notebook add-md <notebook_id> <file.md> --title "..."
traittutor notebook replace-md <notebook_id> <record_id> <file.md>
traittutor notebook remove-record <notebook_id> <record_id>
```

### Sessions

```bash
traittutor session list --limit 20
traittutor session show <id> --format json
traittutor session open <id>
traittutor session rename <id> --title "..."
traittutor session delete <id>
```

### Memory and Models

```bash
traittutor memory show
traittutor memory clear --force

traittutor models list
traittutor config show
```

### Local Services

```bash
traittutor init
traittutor serve --host 0.0.0.0 --port 8001
traittutor start
```

## REPL Slash Commands

Inside `traittutor chat`, use:

| Command | Effect |
|:---|:---|
| `/quit` | Exit REPL |
| `/session` | Show current session id |
| `/status` | Print current REPL state |
| `/new` or `/clear` | Start a new session context |
| `/regenerate` or `/retry` | Re-run the last user message |
| `/tool on\|off <name>` | Toggle a tool |
| `/cap <name>` | Switch between `chat` and `deep_research` |
| `/kb <name>\|none` | Set or clear knowledge base |
| `/history add <id>` / `/history clear` | Manage history references |
| `/notebook add <ref>` / `/notebook clear` | Manage notebook references |
| `/show last\|<n>` | Expand a captured tool result or thinking block |
| `/refs` | Show all active references |
| `/config show\|set\|clear` | Manage capability config |

## Typical Workflows

First-time setup:

```bash
pip install -e .
traittutor init
```

Daily learning chat:

```bash
traittutor chat --kb textbook --tool rag --tool web_search
```

Learning exploration:

```bash
traittutor run deep_research "How do retrieval-augmented tutors evaluate grounding?" --config-json '{"mode":"notes","depth":"standard"}'
```

Run the local product:

```bash
traittutor start
```
