# Windy Fly — Onboarding Guide for New Builders

## First: Read the DNA Strand

Before writing a single line of code, read the DNA Strand. All four parts.

1. [Part 1: Foundation + Phase 0](WINDY_FLY_DNA_STRAND_PART1.md)
2. [Part 2: Phases 1–2](WINDY_FLY_DNA_STRAND_PART2.md)
3. [Part 3: Phases 3–4](WINDY_FLY_DNA_STRAND_PART3.md)
4. [Part 4: Phase 5 + Ecosystem Map](WINDY_FLY_DNA_STRAND_PART4.md)

## The DNA Strand Rule

> Every task is atomic. One task = one action. A 1M-parameter model reads one task, executes it, verifies it, moves to the next. No ambiguity. No judgment calls. If a task requires a decision, the decision is already made in the DNA Strand.

## Starting Phase 0

Phase 0 builds the core Python agent loop. The `windy-agent/` repo is your working directory.

**Prerequisites:**
- Python 3.12+
- `uv` installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- An OpenAI or Anthropic API key

**First command:**
```bash
git clone https://github.com/sneakyfree/windy-agent.git
cd windy-agent
uv sync
cp .env.example .env
# Edit .env: add OPENAI_API_KEY or ANTHROPIC_API_KEY
```

**Then follow the codons in order:**
- Step 0.1: Initialize Repository (repo already exists — skip `git init`, start at 0.1.2)
- Step 0.2: Config System
- Step 0.3: Database Layer
- ...

## Verification Pattern

Every step has a verification command. Run it. If it fails, fix it before moving on. Never skip verification.

Example:
```bash
# Verify Step 0.3
python -c "from windyfly.memory.database import Database; db = Database('data/test.db'); print('OK'); db.close()"
# Expected output: OK
```

## The Codon Checklist

Part 4 has the full codon checklist (59 codons total). After each phase, check every codon for that phase. No unchecked codons before proceeding to the next phase.

## Architecture Quick Reference

```
windy-agent/
├── SOUL.md                     # Personality definition (read this first)
├── windyfly.toml               # Runtime config
├── pyproject.toml              # Python dependencies (uv)
├── src/windyfly/
│   ├── main.py                 # Entry point
│   ├── config.py               # Config loader
│   ├── agent/                  # LLM loop + prompt assembly
│   ├── memory/                 # SQLite + CRUD
│   ├── personality/            # SOUL.md engine
│   ├── channels/               # CLI + Matrix bot
│   └── tools/                  # Tool registry + Windy API
├── tests/                      # pytest test suite
├── data/                       # windyfly.db (created at runtime)
├── docs/                       # DNA Strand + research
└── gateway/                    # Bun gateway (Phase 4+)
```

## Key Design Decisions (Already Made — Don't Re-Litigate)

| Decision | Choice | Why |
|---|---|---|
| Language | Python 3.12+ | ML ecosystem, LLM libraries |
| Package manager | uv | Fast, modern, production-ready |
| Database | SQLite + sqlite-vec + FTS5 | One file, zero deps, offline-first |
| Config format | TOML | Human-readable, no ambiguity |
| Matrix client | matrix-nio | Python-native, production-grade |
| Gateway (Phase 4) | Bun + TypeScript | Best async I/O + chat SDKs |
| IPC bridge | Unix Domain Socket | Fast, local, no deps |

## Questions?

All architectural decisions are documented in:
- [`docs/architecture/synthesized_architecture.md`](architecture/synthesized_architecture.md) — The canonical master plan
- [`docs/research/`](research/) — The research that informed the decisions
