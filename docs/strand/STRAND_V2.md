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

Built in waves (strand-to-green plan, 2026-10-10): 5a sets the frame plus Part 1 §4 and gene 0.3;
5b-5f fill in Parts 1-5; 5g adds the new genes.

## Part 1: Foundation and Phase 0

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
