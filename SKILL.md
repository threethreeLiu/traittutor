# TraitTutor CLI Skill

> Teach your AI agent to configure, manage, and use TraitTutor — an intelligent learning platform — entirely through the command line.

## When to Use

Use this skill when the user wants to:
- Set up or configure TraitTutor
- Chat with TraitTutor or run a capability (deep solve, quiz generation, deep research, visualize, math animation, mastery path)
- Create, manage, or search knowledge bases
- Create, manage, or run Partners (IM-connected companions)
- Search, install, or manage skills from a hub (ClawHub)
- Inspect or maintain interactive Books
- View or manage learning memory, sessions, or notebooks
- Start the TraitTutor API server or the full Web app

## Prerequisites

- Python 3.11+
- TraitTutor installed: `pip install traittutor` for the full Web app, `pip install traittutor-cli` for CLI-only, or `pip install -e .` from a source checkout
- Run `traittutor init` for first-time interactive setup. It walks a guided wizard (ports → LLM → embedding → search → review) and writes the same settings as the Web Settings page under `data/user/settings`. Add `--cli` to skip the ports step for CLI-only use, or `--home <path>` to target a specific workspace.

## Commands

### Chat & Capabilities

```bash
# Interactive REPL
traittutor chat
traittutor chat --capability deep_solve --kb my-kb --tool rag --tool web_search

# One-shot capability execution
traittutor run chat "Explain Fourier transform"
traittutor run deep_solve "Solve x^2 = 4" --tool rag --kb textbook
traittutor run deep_question "Linear algebra" --config num_questions=5
traittutor run deep_research "Attention mechanisms" --kb papers --config mode=report --config depth=standard
traittutor run visualize "Plot the unit circle"
traittutor run math_animator "Visualize a Fourier series"

# Capabilities accepted by `run` / `chat -c`:
#   chat, deep_solve, deep_question, deep_research, visualize, math_animator, mastery_path

# Options for `run`:
#   --session <id>         Resume existing session
#   --tool/-t <name>       Enable tool (repeatable)
#   --kb <name>            Knowledge base (repeatable)
#   --notebook-ref <ref>   Notebook reference, "<notebook_id>:<rec1>,<rec2>" (repeatable)
#   --history-ref <id>     Referenced session id (repeatable)
#   --language/-l <code>   Response language (default: en)
#   --config <key=value>   Capability config (repeatable)
#   --config-json <json>   Capability config as JSON
#   --format/-f <fmt>      Output format: rich | json (default: rich)
```

`traittutor chat` accepts the same `--session / --tool / --kb / --notebook-ref / --history-ref / --language / --config / --config-json` options, plus `--capability/-c <name>` to set the initial capability.

**Tools** for `--tool` / `-t`: user-toggleable tools are `brainstorm`, `web_search`, `paper_search`, `reason`, `geogebra_analysis`, `imagegen`, and `videogen`. Context-gated tools (`rag`, `code_execution`, `read_source`, `web_fetch`, `github`, `ask_user`, …) auto-mount when their context is present, but can also be force-enabled with `--tool`. Run `traittutor plugin list` for the full registered set.

### Knowledge Bases

```bash
traittutor kb list [--format rich|json]              # List all knowledge bases
traittutor kb info <name>                            # Show knowledge base details (JSON)
traittutor kb create <name> --doc file.pdf           # Create from documents (--doc/-d repeatable)
traittutor kb create <name> --docs-dir ./papers      # ...or from a directory of documents
traittutor kb add <name> --doc more.pdf              # Add documents incrementally
traittutor kb search <name> "query text" [--mode hybrid] [--format rich|json]
traittutor kb set-default <name>                     # Set as default KB
traittutor kb delete <name> [--force]                # Delete a knowledge base
```

### Partners

Partners are IM-connected learning companions (the former "TutorBot").

```bash
traittutor partner list                              # List all partners
traittutor partner create <id> -n "My Tutor"         # Create and start a new partner
#   -n/--name <text>   Display name
#   -s/--soul <md>     Soul markdown (the persona)
#   -m/--model <id>    Model override
traittutor partner start <id>                        # Start a partner
traittutor partner stop <id>                         # Stop a running partner
```

### Skills

Install and manage skills, including packages from external hubs (ClawHub).
Hub refs use `<hub>:<slug>[@version]` (the hub prefix defaults to `clawhub`).

```bash
traittutor skill search "flashcards" [--hub clawhub] [--limit 10]
traittutor skill install clawhub:some-skill[@1.2.0] [--name local-name] [--force] [--allow-unverified]
traittutor skill list                                # List local skills (with hub provenance)
traittutor skill remove <name>                       # Remove a user-layer skill
```

### Books

Maintenance commands for the BookEngine (authoring/reading is via the Web app).

```bash
traittutor book list                                 # List all books (flags stale pages)
traittutor book health <book_id>                     # Inspect KB drift + log.md health
traittutor book refresh-fingerprints <book_id>       # Re-snapshot KB fingerprints
```

### Memory

```bash
traittutor memory show [<target>]    # target: L3 (all global docs, default) | L2 (all surfaces) | a doc name (e.g. profile, chat)
traittutor memory clear [<target>]   # target: all (default) | trace (all L1) | a surface name (clears that surface's L1)
#   --force/-f   Skip confirmation
```

### Sessions

```bash
traittutor session list [--limit 20]                 # List sessions
traittutor session show <id> [--format rich|json]    # View session messages
traittutor session open <id>                         # Resume session in the REPL
traittutor session rename <id> --title "..."         # Rename a session
traittutor session delete <id>                       # Delete a session
```

### Notebooks

```bash
traittutor notebook list                             # List notebooks
traittutor notebook create <name> [--description "..."]
traittutor notebook show <notebook_id> [--format rich|json]
traittutor notebook add-md <notebook_id> <file.md> [--title "..."] [--type chat|question|research|solve]
traittutor notebook replace-md <notebook_id> <record_id> <file.md>
traittutor notebook remove-record <notebook_id> <record_id>
```

### Providers

```bash
traittutor provider login openai-codex               # OAuth login for OpenAI Codex
traittutor provider login github-copilot             # Validate an existing Copilot auth session
```

### System

```bash
traittutor config show                               # Print resolved configuration
traittutor plugin list                               # List registered tools and capabilities
traittutor plugin info <name>                         # Show a tool/capability's schema + availability
traittutor serve [--host 0.0.0.0] [--port 8001] [--reload]   # Start the API server
traittutor start [--home <path>]                     # Launch backend + frontend together
traittutor init [--cli] [--home <path>]              # Create/update workspace settings
```

## REPL Slash Commands

Inside `traittutor chat`, use these:

| Command | Effect |
|:---|:---|
| `/quit` | Exit REPL |
| `/session` | Show current session id |
| `/status` | Print the current REPL state |
| `/new` or `/clear` | Start a new session context |
| `/regenerate` or `/retry` | Re-run the last user message |
| `/tool on\|off <name>` | Toggle a tool |
| `/cap <name>` | Switch capability |
| `/kb <name>\|none` | Set or clear knowledge base |
| `/history add <id>` / `/history clear` | Manage history references |
| `/notebook add <ref>` / `/notebook clear` | Manage notebook references |
| `/show last\|<n>` | Expand a captured tool result or thinking block |
| `/refs` | Show all active references |
| `/config show\|set\|clear` | Manage capability config |

## Typical Workflows

**First-time setup:**
```bash
cd TraitTutor
pip install -e .
traittutor init        # Interactive guided setup (add --cli for CLI-only)
```

**Daily learning:**
```bash
traittutor chat --kb textbook --tool rag --tool web_search
```

**Build a knowledge base from documents:**
```bash
traittutor kb create physics --doc ch1.pdf --doc ch2.pdf
traittutor run chat "Explain Newton's third law" --kb physics --tool rag
```

**Generate quiz questions:**
```bash
traittutor run deep_question "Thermodynamics" --kb physics --config num_questions=5
```

**Run the full Web app locally:**
```bash
traittutor start       # backend + frontend; Ctrl+C to stop
```
