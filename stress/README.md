# Stress harnesses

The harnesses that Windy 0's scheduled health jobs run:

| file | used by |
|---|---|
| `stress_v10_organ_harmony.py` | `scripts/windy-redalarm.sh` (Wed/Fri), `scripts/windy-weekly-brief.sh` (Sun) |
| `stress_v13_qa_battery.py`, `stress_v14_extended.py` | `scripts/windy-qa-battery.sh` (Sun) |
| `heart.py` | v10's heart organ (pure; unit-tested) |

They used to live only in `~/.windy-stress`, outside version control. v10's mocked
LLM stopped matching `call_llm`'s signature on 2026-05-20, and the break went
unnoticed until it made the 2026-09-23 red alarm false. Now CI runs v10 in its
mocked mode (`tests/test_stress_harness_smoke.py`), so a signature change fails a PR.

Runtime state stays outside the repo in `$WINDY_STRESS_HOME` (default
`~/.windy-stress`): `config.toml` (copy `config.example.toml`), scratch DBs in
`data/`, `logs/`, and health scorecards in `health/`.

Useful env:
- `REAL_LLM=1` (v10 uses the real model; costs money)
- `V10_TURNS=smoke` or `V10_TURNS=0,1,7` (a turn subset)
- `WINDY_STRESS_NO_EMBEDDINGS=1` (skip the embedding model; used in CI)
- `WINDY_ENV_FILE` (the bot env to load; set it to a missing path to load nothing)
