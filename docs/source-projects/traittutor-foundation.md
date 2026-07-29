# TraitTutor Source Project

Source path: `/Users/lrm/Documents/code/TraitTutor`

## Role In TraitTutor

TraitTutor is the engineering foundation to be renamed and refit as TraitTutor. It provides the web app, CLI, API server, capability runtime, tools, streaming protocol, knowledge center, memory, notebooks, question bank, settings, and learning-space UI patterns.

## Observed Stack

- Python package: `traittutor`
- CLI package: `traittutor_cli`
- Web package: `web/package.json`, currently named `opentutor-web`
- Backend framework: FastAPI
- Frontend framework: Next.js 16, React 19, TypeScript
- CLI framework: Typer
- Runtime settings: JSON settings under runtime data directories

## Important Entry Points

- `pyproject.toml`: Python project metadata, package discovery, script entry point, pytest/ruff config.
- `traittutor_cli/main.py`: CLI entry point and `traittutor` command wiring.
- `traittutor/api/main.py`: FastAPI app and router mounting.
- `traittutor/runtime/bootstrap/builtin_capabilities.py`: built-in capability class registry.
- `traittutor/core/stream.py` and `traittutor/core/stream_bus.py`: stream event protocol.
- `web/app/`: Next.js routes.
- `web/components/space/SpaceDashboard.tsx`: learning-space dashboard and personalization entry pattern.
- `web/components/knowledge/KnowledgeHome.tsx`: material/knowledge management surface.

## Reusable Product Surfaces

- Space dashboard: add a TraitTutor generation entry under the personalization group.
- Knowledge Center: use existing materials and knowledge bases as generation inputs.
- Notebook: save generated courseware and flashcards.
- Question Bank: save generated quiz items.
- Chat: continue from generated outputs.
- Settings: reuse existing model/provider configuration.

## Migration Notes

- Rename package identity rather than adding a wrapper beside `traittutor`.
- Update imports, package-data config, pytest paths, CLI help text, server target, API title, frontend package name, metadata, navigation text, and visible product branding.
- Source-project docs may mention TraitTutor; runtime UI should not keep TraitTutor branding.
