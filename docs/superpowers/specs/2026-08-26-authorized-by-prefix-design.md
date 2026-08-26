# Design: token-prefix classification for the "authorized by" sensors (issue #200)

**Status:** implemented, 2026-08-26.
**Supersedes:** sections 2 and 3 of
[2026-07-11-authorized-by-sensor-design.md](2026-07-11-authorized-by-sensor-design.md)
(`ZaptecAuthorizedBySensor` and "Self-authorization recognition"). Sections 1,
4 and 5 (entity descriptions, translations, scope) still hold.

## What changed

The July design recognized a remote authorization by *timing*: `authorize_charge()`
recorded a monotonic timestamp, `Charger.set_attributes()` watched for a change of
`charger_current_user_uuid` within `SELF_AUTHORIZATION_WINDOW = 30` seconds, and
learned tokens went into a per-instance set `_self_authorized_ids` that
`is_self_authorized()` tested against.

That machinery is deleted. The sensor now classifies on the token string itself:

```python
REMOTE_AUTHORIZATION_PREFIX: Final = "ble-"

raw = self._get_zaptec_value() or None
if raw is not None and str(raw).startswith(REMOTE_AUTHORIZATION_PREFIX):
    self._attr_native_value = "HA/Zaptec App"
else:
    self._attr_native_value = raw
self._attr_extra_state_attributes = {"id": raw}
```

The label also changed from "Home Assistant" to **"HA/Zaptec App"**.

## Why

### The evidence

Captured 2026-08-26 from a live Zaptec Go 2 (firmware 3.3.0.0), by correlating
Home Assistant recorder history for `sensor.<charger>_geautoriseerd_door` against
`GET /api/sessions/archived` for the same charger:

| authorization | live observation 722 | archived `tokenName` |
|---|---|---|
| "Authorize charging" button in HA | `ble-07c3031f-4b08-45d4-a1c8-f7ed0ee92ee5` | `null` |
| RFID card tap | `nfc-049CC6DA682091` | `Tag sleutelbos Remco` |

The uuid after `ble-` is byte-identical to `authorizedUser.id` on the archived
session — i.e. the account's Zaptec user id, not anything HA-specific. Zaptec
encodes the *authorization method* in the token prefix.

The July spec already documented the `nfc-<id>` / `ble-<uuid>` shapes in its
Problem section, but built recognition on timing anyway; nothing in the thread
had confirmed the prefix was reliable enough to branch on. The capture above is
that confirmation.

### Why the old design was wrong, not merely complicated

1. **The label was wrong by construction.** `_self_authorized_ids` only ever
   learned tokens that arrived after *this* HA instance called
   `authorize_charge()`. An authorization made from the Zaptec app was never in
   the set, yet the label claimed "Home Assistant" for the set's members only —
   so the one case the label named was the only case it could not distinguish
   from the app. The prefix rule labels both, and the new label says so.
2. **It lost state across restarts.** The set was in-memory, so a
   `completed_session.AuthenticationCode` read after a restart mid-session
   silently stopped being recognized.
3. **It could mis-attribute a real card tap.** An RFID tap landing inside the
   30-second window after an HA-initiated authorize was learned as "ours".

The prefix rule is stateless, survives restarts, needs no correlation window,
and cannot mis-attribute a tap.

## Known limitations

- **"HA/Zaptec App" is a hardcoded English state.** HA can only translate states
  via `device_class: enum` plus a fixed `options` list, which is impossible when
  the other states are unbounded raw tokens. Accepted; alternative would be to
  move the classification into an attribute and leave the state raw.
- **The rule is an allowlist of exactly one prefix.** OCPP idTags, webhook /
  third-party authorizations, and any future Zaptec prefix fall through to being
  shown raw. That is the safe direction (no false labeling), but a new remote
  prefix would silently stop being relabeled.
- **Matching is case-sensitive.** Only lowercase `ble-` has been observed.
- **`completed_session.AuthenticationCode` is empty for remote authorizations.**
  Confirmed on live hardware: the "Last session authorized by" sensor reads
  *Unknown* after every app/HA-authorized session, and carries a token only for
  RFID sessions. The `ble-` branch on that sensor is therefore pinned by tests
  but not expected to fire in practice.

## Dependency: the clearing fix

These sensors are only correct together with the separate fix to
`ZaptecBase.state_to_attrs`
([zaptec/api.py](../../../custom_components/zaptec/zaptec/api.py)): Zaptec ends a
session by sending observations **721** (`SessionIdentifier`) and **722**
(`ChargerCurrentUserUuid`) with *no value field at all*, which the client used to
drop, leaving the previous session's token in `_attrs` indefinitely.
`CLEARABLE_OBSERVATIONS` maps those to `""`, which the sensor turns into `None`
(HA shows "Unknown"). Verified live: both observations go null within seconds of
session end.

## Future improvement: human-readable names

`GET /api/sessions/archived` returns, per session, `tokenName` (the RFID card's
friendly name as set in the Zaptec Portal) and `authorizedUser` (id, email,
fullName). Classification from those three fields:

| tokenName | authorizedUser | externalId | meaning |
|---|---|---|---|
| set | set | null | RFID card |
| null | set | null | remote — HA or Zaptec app |
| null | null | set | third party (OCPP / webhook) |
| null | null | null | not authorized |

The archived record does **not** carry the raw token id, so there is no
`token id -> card name` lookup; the join is live observation 723
`CompletedSession.SessionId` == `ArchivedSession.id` (verified identical).
Sourcing "Last session authorized by" from this feed would give card name for
RFID, user name for remote, and Unknown for anonymous — strictly better than
`AuthenticationCode` — but it needs the `get_archived_sessions` fetch that lives
on the issue #300 (energy statistics) work.

Two call gotchas, neither documented: `Order` must be the integer 0/1 (a string
returns HTTP 400 with an empty body), and the response key is `sessions`
(lowercase) alongside `cursor`/`hasMore`, not `Data` as on the other list
endpoints.

## Testing

`tests/test_sensor.py` (six tests, on the pytest-homeassistant harness from
PR #414) pins: raw `nfc-` token shown as-is, `ble-` token relabeled, empty value
to Unknown, and all three again for the dotted
`completed_session.AuthenticationCode` key. The dotted-key cases were added
after a review pass showed a mutation that scoped the relabel to the live key
alone survived the original four tests.

The seven `tests/zaptec/test_api.py` tests covering the timing window were
deleted with the mechanism.

## Branch layout

Two upstream PRs, in order:

1. `fix/issue-200-clear-session-observations` — the `state_to_attrs` clearing fix.
2. `feat/issue-200-authorized-by-sensor` — the sensors, on top of that fix.

`prep/issue-200-on-414` carries the same sensor work rebased on PR #414's test
harness, which is where `tests/test_sensor.py` lives.
