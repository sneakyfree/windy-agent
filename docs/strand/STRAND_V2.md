# Windy Fly strand v2: the living spec

The DNA strand (docs/WINDY_FLY_DNA_STRAND_PART1-5.md) is the original plan; those files are kept
as history. **This file is what Windy Fly promises today**, one row per codon, and it is
executable: `tests/strand/test_scorecard.py` fails the build if a row and the code disagree.

**Status words**
- `live`: the promise holds, and the named test proves it. The test must exist and must not be
  skippable (no skip/xfail decorator, module mark or call). A skipped test never counts as green.
- `retired: <decision> (#PR)`: deliberately removed. The PR that removed it carries the decision.
- `planned: <owner>: <what>`: not true today. It has an owner and a next step; it is not hidden.

Codon ids are `P<part>.<gene>.<n>` for the original strand's parts and `N<area>.<n>` for genes the
original plan did not have (Wave 5g: Windy Mind, Eternitas, teams, Mail, Calendar, Cloud, phone
tools, Vault). A row owned by another lane counts only after that lane confirms it (Hub).

Built in waves (strand-to-green plan, 2026-10-10): 5a set the frame plus Part 1 §4 and gene 0.3;
5b1 adds Part 1 §1-§3 and genes 0.1-0.2; 5b2-5f fill in the rest; 5g adds the new genes.

## Part 1: Foundation and Phase 0

### P1.1 Product identity (Part 1 §1)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P1.1.1 | The product is Windy Fly: "Your AI. Your Rules. Your Ecosystem." | live | `tests/strand/test_p1_foundation.py::test_p1_1_name_and_tagline` |
| P1.1.2 | Part of the Windy ecosystem; chat on chat.windychat.ai, provisioned at hatch | live | `tests/strand/test_p1_foundation.py::test_p1_1_part_of_the_windy_ecosystem` |
| P1.1.3 | One shared Matrix bot id (@windyfly) | retired: every agent is its own @agent_<passport>, minted at hatch; the shared id was removed (#505) | — |
| P1.1.4 | Licence: proprietary | planned: Grant (via Boss): choose and state the licence; there is no LICENSE file and no licence field while the package is published to PyPI | — |
| P1.1.5 | Runs on Python 3.12 or newer | live | `tests/strand/test_p1_foundation.py::test_p1_1_python_312_or_newer` |
| P1.1.6 | TypeScript/Bun gateway, tested in CI | live | `tests/strand/test_p1_foundation.py::test_p1_1_bun_gateway_is_tested_in_ci` |
| P1.1.7 | SQLite with FTS5 full-text search | live | `tests/strand/test_p1_memory.py::test_p1_4_episodes_fts_finds_saved_text` |
| P1.1.8 | sqlite-vec vector search | retired: never loaded; dependency and claims removed (#505) | — |
| P1.1.9 | matrix-nio is the chat protocol library | live | `tests/strand/test_p1_foundation.py::test_p1_1_matrix_nio_is_the_chat_protocol` |
| P1.1.10 | TOML config | live | `tests/test_config_resilience.py::test_valid_config_loads_normally` |
| P1.1.11 | Dependencies locked with uv and bun | live | `tests/strand/test_p1_foundation.py::test_p1_1_locked_with_uv_and_bun` |

### P1.2 Architecture (Part 1 §2)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P1.2.1 | A ReAct loop: the model calls tools, sees results, then answers | live | `tests/test_agent_loop.py::TestToolExecution::test_tool_reloop_executes_tools` |
| P1.2.2 | A memory manager over SQLite | live | `tests/test_memory.py::TestEpisodes::test_save_and_retrieve` |
| P1.2.3 | A personality engine that reads SOUL.md | live | `tests/test_personality.py::TestLoadSoul::test_loads_existing_file` |
| P1.2.4 | An in-process write queue | live | `tests/strand/test_p1_memory.py::test_p1_03_write_queue_runs_on_a_daemon_thread` |
| P1.2.5 | Windy Pro tools when Windy Pro is configured and answers (else not offered) | live | `tests/test_tools.py::TestWindyToolsOnlyWhenConfigured::test_answering_pro_registers_four` |
| P1.2.6 | No Windy Pro tools without a working Windy Pro | live | `tests/test_tools.py::TestWindyToolsOnlyWhenConfigured::test_unreachable_no_tools` |

### P1.3 Repo layout (Part 1 §3, gene 0.1)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P1.3.1 | Root files: pyproject, windyfly.toml, SOUL.md, README, .gitignore, .env.example | live | `tests/strand/test_p1_foundation.py::test_p1_3_layout[root]` |
| P1.3.2 | Core modules: package, main, config | live | `tests/strand/test_p1_foundation.py::test_p1_3_layout[core]` |
| P1.3.3 | agent/: loop, prompt, models | live | `tests/strand/test_p1_foundation.py::test_p1_3_layout[agent]` |
| P1.3.4 | memory/: the 8 modules | live | `tests/strand/test_p1_foundation.py::test_p1_3_layout[memory]` |
| P1.3.5 | personality/: engine | live | `tests/strand/test_p1_foundation.py::test_p1_3_layout[personality]` |
| P1.3.6 | personality/mode.py (mode switching) | retired: did nothing; removed (#499) | — |
| P1.3.7 | channels/: Matrix bot, CLI | live | `tests/strand/test_p1_foundation.py::test_p1_3_layout[channels]` |
| P1.3.8 | tools/: registry, Windy Pro, web search | live | `tests/strand/test_p1_foundation.py::test_p1_3_layout[tools]` |
| P1.3.9 | tests/ with the core test files | live | `tests/strand/test_p1_foundation.py::test_p1_3_layout[tests]` |
| P1.3.10 | data/ is tracked empty (data/.gitkeep) | live | `tests/strand/test_p1_foundation.py::test_p1_3_layout[data]` |

### P1.01 Repo setup (gene 0.1)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P1.01.1 | git init and an initial commit | retired: a one-time build step, not a runtime promise (#526) | — |
| P1.01.2 | .gitignore keeps secrets, databases, provider files and caches out of git | live | `tests/strand/test_p1_foundation.py::test_p1_01_gitignore_keeps_secrets_and_state_out` |
| P1.01.3 | .env.example names every key once | live | `tests/strand/test_p1_foundation.py::test_p1_01_env_example_names_each_key_once` |
| P1.01.4 | The package is "windyfly" and declares its runtime dependencies | live | `tests/strand/test_p1_foundation.py::test_p1_01_package_named_windyfly_with_its_runtime_deps` |

### P1.02 Config (gene 0.2)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P1.02.1 | One default model | planned: Windy Agent: four defaults disagree (windyfly.toml, DEFAULT_CONFIG, .env.example, the loop's fallback); pick one, the others read it | — |
| P1.02.2 | [agent] temperature / max_response_tokens / max_context_tokens are honoured | planned: Windy Agent: never read (the creativity and response-length sliders decide); honour them or drop them from the file | — |
| P1.02.3 | [memory] db_path and max_nodes_per_context come from the file | live | `tests/strand/test_p1_foundation.py::test_p1_02_memory_settings_come_from_the_file` |
| P1.02.4 | [memory] max_episodes_per_context is honoured | planned: Windy Agent: never read (5 + 5 x context_window slider decides); honour it or drop it | — |
| P1.02.5 | [personality] sliders default from the file | live | `tests/strand/test_p1_foundation.py::test_p1_02_personality_sliders_default_from_the_file` |
| P1.02.6 | [costs] daily budget is enforced | live | `tests/test_cost_tracker.py::TestBudgetCheck::test_exceeds_budget` |
| P1.02.7 | [matrix] settings come from the file | live | `tests/strand/test_p1_foundation.py::test_p1_02_matrix_settings_come_from_the_file` |
| P1.02.8 | load_config reads windyfly.toml into a dict (safe defaults on a corrupt file) | live | `tests/test_config_resilience.py::test_valid_config_loads_normally` |
| P1.02.9 | Secrets in the environment override the file | live | `tests/test_config_resilience.py::test_env_overrides_still_apply_on_corrupt_config` |

### P1.4 Database schema (Part 1 §4)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P1.4.1 | Every file database opens in WAL mode | live | `tests/strand/test_p1_memory.py::test_p1_4_journal_mode_wal` |
| P1.4.2 | synchronous=NORMAL | live | `tests/strand/test_p1_memory.py::test_p1_4_synchronous_normal` |
| P1.4.3 | foreign_keys=ON | live | `tests/strand/test_p1_memory.py::test_p1_4_foreign_keys_on` |
| P1.4.4 | busy_timeout=5000 ms | live | `tests/strand/test_p1_memory.py::test_p1_4_busy_timeout_5000` |
| P1.4.5 | `nodes` keeps all 16 spec columns | live | `tests/strand/test_p1_memory.py::test_p1_4_spec_columns[nodes]` |
| P1.4.6 | `episodes` keeps all 14 spec columns | live | `tests/strand/test_p1_memory.py::test_p1_4_spec_columns[episodes]` |
| P1.4.7 | `soul` keeps all 8 spec columns | live | `tests/strand/test_p1_memory.py::test_p1_4_spec_columns[soul]` |
| P1.4.8 | `skills` keeps all 17 spec columns | live | `tests/strand/test_p1_memory.py::test_p1_4_spec_columns[skills]` |
| P1.4.9 | `failures` keeps all 9 spec columns | live | `tests/strand/test_p1_memory.py::test_p1_4_spec_columns[failures]` |
| P1.4.10 | `cost_ledger` keeps all 7 spec columns | live | `tests/strand/test_p1_memory.py::test_p1_4_spec_columns[cost_ledger]` |
| P1.4.11 | `episodes_fts` (FTS5) finds saved episode text | live | `tests/strand/test_p1_memory.py::test_p1_4_episodes_fts_finds_saved_text` |
| P1.4.12 | `schema_version` has row 1 and records every migration | live | `tests/strand/test_p1_memory.py::test_p1_4_schema_version_tracks_every_migration` |

### P1.03 Database layer (gene 0.3)

| Codon | Promise | Status | Test |
|---|---|---|---|
| P1.03.1 | Opening a database creates its directory | live | `tests/strand/test_p1_memory.py::test_p1_03_creates_the_data_dir` |
| P1.03.2 | Declared types are parsed (TIMESTAMP comes back as a datetime) | live | `tests/strand/test_p1_memory.py::test_p1_03_parse_decltypes` |
| P1.03.3 | Reopening runs no migration twice and keeps the data | live | `tests/strand/test_p1_memory.py::test_p1_03_reopen_does_not_rerun_migrations` |
| P1.03.4 | Each migration is applied atomically | planned: Windy Agent: SQL-string migrations run through executescript, which commits each statement; make them atomic, or record that only callable migrations are | — |
| P1.03.5 | execute / executemany / fetchone / fetchall give rows keyed by column name | live | `tests/strand/test_p1_memory.py::test_p1_03_rows_are_addressed_by_column_name` |
| P1.03.6 | save_episode stores an episode that can be read back | live | `tests/test_memory.py::TestEpisodes::test_save_and_retrieve` |
| P1.03.7 | Recent episodes come newest first | live | `tests/test_memory.py::TestEpisodes::test_multiple_episodes_ordered` |
| P1.03.8 | Recent episodes can be filtered by session | live | `tests/test_memory.py::TestEpisodes::test_session_filter` |
| P1.03.9 | upsert_node creates a node that get_node returns | live | `tests/test_memory.py::TestNodes::test_upsert_new` |
| P1.03.10 | upsert_node on the same (type, name, scope) updates it | live | `tests/test_memory.py::TestNodes::test_upsert_existing_updates` |
| P1.03.11 | Nodes can be listed by type | live | `tests/test_memory.py::TestNodes::test_get_by_type` |
| P1.03.12 | Nodes can be searched by name | live | `tests/test_memory.py::TestNodes::test_search_by_name` |
| P1.03.13 | Soul keys are stored and read back | live | `tests/test_memory.py::TestSoul::test_upsert_and_get` |
| P1.03.14 | Cost rows are logged and read back | live | `tests/test_memory.py::TestCostLedger::test_log_and_retrieve` |
| P1.03.15 | Failures are logged and read back | live | `tests/test_memory.py::TestFailures::test_log_and_retrieve` |
| P1.03.16 | The six core tables exist on a new database | live | `tests/test_memory.py::TestDatabaseCreation::test_creates_all_tables` |
| P1.03.17 | The write queue runs writes on a daemon thread | live | `tests/strand/test_p1_memory.py::test_p1_03_write_queue_runs_on_a_daemon_thread` |
| P1.03.18 | HIGH writes run before MEDIUM, MEDIUM before LOW | live | `tests/test_strand_wave4_write_queue.py::test_C0_11_strict_priority_order_with_several_items_each` |
| P1.03.19 | A failing write does not stop the queue | live | `tests/test_chaos.py::TestWriteQueueCrashRecovery::test_worker_survives_failing_callback` |
| P1.03.20 | stop() finishes the writes already queued | live | `tests/test_chaos.py::TestWriteQueueCrashRecovery::test_stop_flushes_pending_items` |
| P1.03.21 | MEDIUM writes run as one transaction per batch of up to 50 | planned: Windy Agent: the queue batches up to 50 but each write commits itself; share one transaction, or restate the promise as "batched for fairness" | — |
| P1.03.22 | Timestamps keep parsing on future Python versions | planned: Windy Agent: P1.03.2 relies on sqlite3's default timestamp converter, deprecated since Python 3.12 (warning in the strand run); register explicit adapters/converters | — |
