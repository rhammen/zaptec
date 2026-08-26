# Design: "Authorized by" sensors (issue #200)

> **Superseded in part (2026-08-26):** the self-authorization recognition
> described here was replaced by token-prefix classification. See
> [2026-08-26-authorized-by-prefix-design.md](2026-08-26-authorized-by-prefix-design.md).


## Problem

[Issue #200](https://github.com/custom-components/zaptec/issues/200) asks to
expose which RFID/NFC tag (or BLE/app authorization) started a charging
session, so users can split energy use per tag. The value already flows
through the integration's normal state poll but isn't exposed as an entity —
today it's only reachable by digging through the `active` binary sensor's
`extra_state_attributes` dump, per a workaround `sveinse` posted in the
thread.

Two Zaptec fields carry this information:

- `ChargerCurrentUserUuid` (observation id **722**, confirmed live against
  `api.zaptec.com/api/constants` — not to be confused with the deprecated
  `ChargerCurrentUserUuidDeprecated`, id 713). Live value, updates as soon as
  a session is authorized. `to_under()` converts it to
  `charger_current_user_uuid`.
- `AuthenticationCode`, nested inside the `CompletedSession` JSON blob (parsed
  by `ZCONST.type_completed_session`). Only populates once a session ends,
  but per `nelgmo`'s report in the issue thread, it's more reliable than 722
  on Zaptec Go. Reachable today via the same dotted-key mechanism already
  used for the existing `completed_session.Energy` sensor
  ([sensor.py:300-308](../../../custom_components/zaptec/sensor.py#L300-L308)).

Both values are opaque tokens (`nfc-<id>`, `ble-<uuid>`) — Zaptec's docs
portal (`docs/state-observation-reference.md`) doesn't document either field
(confirmed: neither appears in its curated ~25-row table), so value semantics
here come from the issue thread's maintainer comments, not official docs.

A richer, human-readable alternative exists (`/api/chargehistory` and
`/api/session/{id}`, confirmed via `swagger/v1/swagger.json`: fields
`tokenName`, `userFullName`, `userEmail`, `userId`), but that's a separate
paginated REST surface requiring a new poller — explicitly out of scope here,
tracked as a future follow-up.

## Scope decision

Cover both fields (live + post-session) rather than just one, since they
target different charger firmware behaviors (Pro/Home vs. Go) and are cheap
to add in parallel — same sensor pattern, no new API calls.

## Design

### 1. Two new sensor descriptions

In `CHARGER_ENTITIES` ([sensor.py](../../../custom_components/zaptec/sensor.py)):

| key | translation_key | icon |
|---|---|---|
| `charger_current_user_uuid` | `authorized_by` | `mdi:card-account-details-outline` |
| `completed_session.AuthenticationCode` | `completed_session_authorized_by` | `mdi:card-account-details` |

Both: `cls=ZaptecAuthorizedBySensor`, no `device_class`/`state_class` (opaque
identifier string, not numeric/enum), no `entity_category` (primary, not
diagnostic — users want this front-and-center for automations), enabled by
default (no precedent for `entity_registry_enabled_default=False` elsewhere
in this codebase).

### 2. `ZaptecAuthorizedBySensor` (new class in `sensor.py`)

```python
class ZaptecAuthorizedBySensor(ZaptecSensor):
    """Sensor for who/what authorized a charging session.

    Maps an empty string to None (HA shows "Unknown"), and relabels a
    recognized self-authorization (see Charger.is_self_authorized) as
    "Home Assistant" while keeping the raw token available as an attribute.
    """

    @callback
    def _update_from_zaptec(self) -> None:
        raw = self._get_zaptec_value() or None
        if raw is not None and self.zaptec_obj.is_self_authorized(raw):
            self._attr_native_value = "Home Assistant"
        else:
            self._attr_native_value = raw
        self._attr_extra_state_attributes = {"id": raw}
        self._attr_available = True
```

`_get_zaptec_value()` already supports both a plain key
(`charger_current_user_uuid`) and a dotted nested key
(`completed_session.AuthenticationCode`), so one class serves both
descriptions.

### 3. Self-authorization recognition, on `Charger` (`zaptec/api.py`)

When a charge is authorized *from within this integration* — via the
"Authorize charging" button ([button.py:29-42](../../../custom_components/zaptec/button.py#L29-L42))
or the `authorize_charging` service
([services.py:228-234](../../../custom_components/zaptec/services.py#L228-L234)) —
both call paths funnel through the single choke point
`Charger.authorize_charge()` and both immediately call `trigger_poll()`
afterward, forcing a fresh state fetch within roughly one request
round-trip. `authorize_charge()`'s POST response carries no usable body (the
endpoint is undocumented, confirmed via swagger — no assigned-UUID readable
from the response), so the assigned identifier can only be observed
indirectly, via the next state poll.

Add to `Charger`:

- `self._pending_self_auth_at: float | None = None` — set to
  `time.monotonic()` at the top of `authorize_charge()`, before the request.
- `self._self_authorized_ids: set[str] = set()` — accumulates recognized
  values, for the lifetime of the `Charger` instance (in-memory only).
- In `set_attributes()`, when `charger_current_user_uuid` changes to a new
  non-empty value: if `_pending_self_auth_at` is set and
  `time.monotonic() - _pending_self_auth_at < 30`, add the new value to
  `_self_authorized_ids` and clear `_pending_self_auth_at`.
- `def is_self_authorized(self, value: str) -> bool: return value in
  self._self_authorized_ids`.

The 30s window is generous relative to `trigger_poll()`'s immediate
out-of-band refresh — it covers retry/backoff delay without needing to be
tight. Once a value is learned, recognition applies to **both** sensors by
plain value equality, with **no time limit** on reuse — this is what lets
`completed_session.AuthenticationCode` (which may only populate hours later,
when the session ends) still get relabeled correctly, reusing the value
learned earlier from the live `charger_current_user_uuid` observation.

### 4. Explicit assumptions and limitations

- **Assumption (unverified):** Zaptec assigns the *same* synthetic UUID for
  every integration-triggered authorization on a given account, per
  `sveinse`'s comment in the issue thread — not confirmed against a live
  account or official docs (no test credentials available in this
  environment). If false, the correlation is simply inert: the "Home
  Assistant" label never appears and the sensor falls back to showing the
  raw token, which is only a UX regression, not a functional bug — no
  automation depends on this label existing.
- **Limitation (accepted, YAGNI):** `_self_authorized_ids` is in-memory only
  and resets on HA restart. It re-learns automatically the next time the
  integration itself triggers an authorization while running. No persistent
  storage is added for this.
- **Raw value preserved:** `charger_current_user_uuid` and
  `completed_session.AuthenticationCode` remain readable as ordinary Zaptec
  attributes (unaffected by the relabeling), and each new sensor also
  exposes the raw token as an `id` extra-state-attribute — so relabeling
  never hides the underlying value from automations.

### 5. Translations

Add `authorized_by` / `completed_session_authorized_by` entries under
`entity.sensor` in `translations/en.json`. Other locale files are
community-maintained and can lag.

## Testing (TDD)

`tests/test_sensor.py`, following the existing `make_charger()` pattern:

- `ZaptecAuthorizedBySensor` passes a real token string through unchanged.
- `ZaptecAuthorizedBySensor` maps `""` → `None` (native_value is `None`).
- `ZaptecAuthorizedBySensor` reads the dotted key
  (`completed_session.AuthenticationCode`) correctly, mirroring
  `test_energy_sensor_uses_session_value_when_larger`'s nested-data setup.
- `id` extra_state_attribute always reflects the raw token, even when the
  displayed state is relabeled.

`tests/zaptec/test_api.py` (or wherever `Charger` unit tests live):

- `is_self_authorized()` returns `True` for a value seen within the 30s
  window after `authorize_charge()`, `False` for a value seen outside that
  window.
- Once learned, `is_self_authorized()` keeps returning `True` for that value
  indefinitely (no re-expiry), and applies to a value found under
  `completed_session.AuthenticationCode`, not just
  `charger_current_user_uuid`.
- A value that never followed an `authorize_charge()` call (a real RFID tap)
  is never recognized.

## Out of scope

- Resolving human-readable tag/user names via `/api/chargehistory` or
  `/api/session/{id}` (`tokenName`, `userFullName`, `userEmail`) — a
  separate, larger follow-up requiring a new poller.
- Persisting `_self_authorized_ids` across HA restarts.
- Any change to `authorize_charge()`'s request (no way to pass a custom
  identifier — confirmed via swagger that neither `authorizecharge` nor
  `SendCommand/{commandId}` accept a request body).
