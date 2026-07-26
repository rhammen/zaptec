# Design: offline constants snapshot for the zaptec_constants fixture (supersedes #398)

**Date:** 2026-07-26
**Status:** Approved (brainstorming complete)
**Branch:** `fix/constants-snapshot-fixture` (off `master`) — standalone PR.

## Background & motivation

`tests/conftest.py`'s session-scoped `zaptec_constants` fixture makes a **live
HTTPS call** to `https://api.zaptec.com/api/constants` on every run (the
constants endpoint needs no login). It powers `tests/zaptec/test_zconst.py` and
`tests/zaptec/test_redact.py`, both of which do `ZCONST.update(zaptec_constants)`
to populate the numeric→string lookup tables they test.

This live dependency causes three problems:

1. **Local dev (native Windows):** the async resolver can't reach DNS, so the
   fixture errors — the subject of open PR **#398** (which forces the OS
   resolver). Maintainer feedback on #398: prefer the devcontainer over
   accommodating native Windows.
2. **The pytest-hacc test-harness migration** (separate branch): pytest-hacc
   blocks network sockets, so the live call raises `SocketBlockedError` — 22
   errors in that branch's CI.
3. It is a network dependency in what should be offline unit tests.

The maintainers' **promoted devcontainer workflow must keep working**, and that
takes deliberate care. Today the live call works there (DNS is available in the
container), and that is the environment maintainers are steered toward (see
steinmn's #398 comment and `DEVELOPMENT.md`). But note: once the pytest-hacc
migration lands, the devcontainer will **also** run under pytest-hacc's socket
blocking, and its default (live) path would then hit the same
`SocketBlockedError` as CI — the devcontainer is not automatically safe. This
design keeps the live call as the **default** and adds an offline fallback for
CI / native-Windows local dev; preserving the devcontainer's default-live
experience under the harness is delivered specifically by the socket guard in
§3, not by luck.

## Goal

Make the constants available **offline** from a committed snapshot when live
testing is disabled, while **preserving the existing default behavior** (live
call) so the maintainers' devcontainer workflow is unchanged. Reuse the repo's
existing live-test gating convention. Supersede and close #398.

## Non-goals

- Do NOT change the default behavior: with no flags set, the fixture still makes
  the live call (as today).
- Do NOT add a new gating flag — reuse `SKIP_ZAPTEC_API_TEST` / `GITHUB_ACTIONS`.
- Do NOT add a separate live-vs-snapshot comparison test — the existing
  `test_zconst.py` / `test_redact.py` provide drift detection for free when run
  against live constants.
- The pytest-hacc migration itself is out of scope (separate branch/PR); this
  change only needs to *compose* cleanly when that branch rebases onto it.

## Design

### 1. Committed snapshot

- `tests/fixtures/constants.json` — the full constants payload (~37 KB),
  captured via `curl https://api.zaptec.com/api/constants` (verified reachable
  via the OS resolver in the dev environment). Pretty-printed JSON for readable
  diffs on refresh.

### 2. `zaptec_constants` fixture — source selection by the existing convention

Rewrite the fixture to choose its source from the **same env vars** the skip
mechanism already reads (it currently ignores them):

- If `SKIP_ZAPTEC_API_TEST == "true"` **or** `GITHUB_ACTIONS == "true"` →
  load and return `tests/fixtures/constants.json` (offline, plain `json.load`).
- Otherwise → make the **live** call, exactly as today (default behavior
  unchanged).

The live branch keeps the current async fetch and its event-loop save/restore
logic (that logic exists for the async call and is only reached here). The
offline branch is a simple synchronous file read — no event loop involved.

Per-environment outcome:

| Environment | Source | Result |
|---|---|---|
| CI (`GITHUB_ACTIONS=true`) | snapshot | offline; no network, no socket block |
| Devcontainer (maintainers) | live (default) | real constants + drift detection |
| Native Windows, `SKIP_ZAPTEC_API_TEST=true` | snapshot | offline |
| Native Windows, default | live | DNS-fails (unsupported path; use SKIP or devcontainer) |

### 3. Forward-compatible socket guard (for the pytest-hacc migration)

On `master` there is no pytest-hacc, so the live call is not socket-blocked and
needs no special handling. But when the migration branch (which adds pytest-hacc)
rebases onto this, its socket blocking would reject the live fetch. To let the
two compose without re-editing the fixture, guard the live branch defensively:

```python
try:
    import pytest_socket
    pytest_socket.enable_socket()
except ImportError:
    pass
```

This is a **no-op on master** (`pytest_socket` isn't installed → `ImportError`)
and re-enables sockets for the live fetch when running under the harness. It is
**required, not speculative**: without it, once the migration adds pytest-hacc,
the devcontainer's default (live) path — and any local run without
`SKIP_ZAPTEC_API_TEST` — would raise `SocketBlockedError` exactly like CI. The
guard is what keeps the maintainers' devcontainer workflow working post-migration.

Implementation note: `zaptec_constants` is session-scoped and performs the fetch
during its own setup, so it must `enable_socket()` immediately before the live
request (and may restore the harness's blocked state afterward). Confirm during
implementation that enabling within the session fixture holds for the fetch.

### 4. Drift detection

No dedicated test. When live is enabled (devcontainer / local without SKIP),
`test_zconst.py` / `test_redact.py` run against the **live** constants and will
fail if Zaptec changes the constants structure in a way that breaks parsing —
the same coverage the live call provides today. When offline, they run against
the snapshot.

### 5. Refresh mechanism

Update the existing snippet in `DEVELOPMENT.md` (§ "Zaptec constants") to write
to `tests/fixtures/constants.json`, so refreshing the snapshot is a documented
one-liner. (Optionally a small `scripts/` helper, but the documented snippet is
sufficient.)

### 6. Supersede #398

This removes the need for #398's native-Windows DNS-resolver fix: native Windows
can run offline via `SKIP_ZAPTEC_API_TEST=true` (snapshot), and live still works
in the devcontainer. Close #398 with that rationale when this lands.

## Coordination with the pytest-hacc migration

- This is a standalone PR off `master`; it is independently valuable (fixes local
  DNS + gives an offline path) and does not depend on the migration.
- The migration branch's 22 `SocketBlockedError`s disappear once this lands and
  the migration rebases onto it (CI sets `GITHUB_ACTIONS=true` → snapshot path).
- The socket guard (§3) is what makes them compose without a fixture re-edit.

## Success criteria

- `SKIP_ZAPTEC_API_TEST=true pytest tests/zaptec/test_zconst.py tests/zaptec/test_redact.py`
  passes fully **offline** (no network) using the snapshot.
- Default (no flags) still makes the live call — behavior unchanged for the
  devcontainer.
- `tests/fixtures/constants.json` is committed and loads cleanly.
- Ruff clean; no production `custom_components/**` changes.

## Open items for planning

- Confirm exact keys/structure the snapshot must contain for `ZCONST.update` +
  the redactor to work (capture a real payload).
- Decide snapshot path resolution in the fixture (relative to the test file via
  `Path(__file__)`).
