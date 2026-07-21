# Phase 03 Plan 02 Summary — RunManager + structured event runner

**Status:** Complete
**Tests:** 10 (all green)
**Commit:** `435a4c2`

## Delivered
- `src/web/services/runner_subprocess.py` — Pipeline subprocess entry point emitting structured JSON events. Every exit path emits terminal `done` (`completed` / `interrupted` / `failed`).
- `src/web/services/run_manager.py` — `RunManager` class:
  - `start_run(config) -> run_id` (inserts DB row before spawn; extras enqueue with `queued` event)
  - `cancel_run(run_id) -> bool` (SIGTERM)
  - `active_run_ids() -> list`
  - `_pump` daemon thread reads stdout, republishes to bus, persists lifecycle to DB
  - Belt-and-suspenders finalization (always emits terminal `done` even if child crashes)

## Design notes
- Subprocess (not thread) per run for isolation + cancellability
- `max_concurrent=1` default; `_pending` queue drains via `_drain_pending()` from pump's finally
- All `_active`/`_pending` mutation under `threading.Lock`; `event_bus.publish` called OUTSIDE lock

## Testing approach
- Zero real subprocesses. `unittest.mock.patch` on `subprocess.Popen` returns `_FakePopen` with scripted stdout iterator.
- Pump-thread synchronization: wait on `threading.enumerate()` for `pump-{run_id}` name to exit — guarantees `finalize_run()` has committed to DB before assertions.
