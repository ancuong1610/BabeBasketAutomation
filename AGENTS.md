# Notes for coding agents

- Captions and any customer-facing Vietnamese text: follow `docs/content-guide.md` (owner's writing guide) and
  the hard rules in `.claude/skills/babe-basket-content/SKILL.md`. The caption prompt is `babebasket/llm.py`.
- Tests: `.venv/bin/python -m unittest` (no network). Secrets live in `.env`; never read or print it.
- `check` runs every minute from a LaunchAgent in the GUI session (`scripts/install_check_agent.sh`); Gemini in
  Chrome (`babebasket/gemini_web.py`) must never be started over SSH.
