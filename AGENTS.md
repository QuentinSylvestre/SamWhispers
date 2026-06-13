# AGENTS.md

## Doc & Test Guidelines

- Update existing documentation files (README.md, docs/) when implementing user-visible changes.
- Do not create new documentation files unless the user requests them.
- All new modules must have corresponding test files in `tests/`.
- Tests use pytest; run with `python -m pytest tests/ -v`.
- Lint with `ruff check src/ tests/`; type-check with `mypy src/`.
