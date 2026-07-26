# Authorized-By Sensor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose which RFID/NFC/BLE token (or, when triggered from Home Assistant itself, a recognizable "Home Assistant" label) authorized a charging session, via two new sensor entities on the charger device.

**Architecture:** Two new `ZapSensorEntityDescription` entries in `CHARGER_ENTITIES` (`sensor.py`) read `charger_current_user_uuid` (live observation) and `completed_session.AuthenticationCode` (populated once a session ends) through a new `ZaptecAuthorizedBySensor` class. That class defers to `Charger.is_self_authorized()` (new method on `zaptec/api.py`'s `Charger`), which recognizes tokens the integration assigned itself: `Charger.authorize_charge()` records a timestamp, and the next `charger_current_user_uuid` change within a 30s window is remembered in `Charger._self_authorized_ids` — a value learned once is then recognized indefinitely, so it still applies later when `completed_session.AuthenticationCode` populates (which can be hours after the live observation was learned).

**Tech Stack:** pytest, pytest-asyncio (`asyncio_mode = "auto"`, already configured), `unittest.mock`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-11-authorized-by-sensor-design.md`

## Global Constraints

- **Branch:** work happens on `feature/issue-200-authorized-by-sensor` (already created, based on `test/platform-entity-coverage`). Do not create another branch.
- Test command: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests -q`
- Lint gate: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format custom_components tests --diff` and `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check custom_components tests` must stay clean for every file this plan touches. The repo's `.ruff.toml` uses `select = ["ALL"]`; `E501` (line-length) is ignored, but `SLF001` (private-member-access) and `PLR2004` (magic-value comparison) are **not** — private-attribute test assertions (`entity._attr_native_value`, `charger._pending_self_auth_at`, etc.) need `# noqa: SLF001` (and `# noqa: PLR2004` if a bare numeric literal appears in a comparison), matching the precedent already in `tests/test_sensor.py`. Run `ruff check` after writing each file and add `# noqa` comments to whatever it flags — don't pre-guess every one, fix from actual output.
- Never commit without explicit user approval (per repo CLAUDE.md) — stop before each commit step and wait for approval, or if running unattended per the user's chosen execution mode, treat "commit" steps as the point to pause for review.
- Do not modify other locale translation files (`nb.json`, `nl.json`, `nn.json`, `pl.json`, `sv.json`) — community-maintained, out of scope.
- Do not modify `INSTALLATION_ENTITIES` or `async_setup_entry()` in `sensor.py` — untouched by this feature.
- Do not implement the `/api/chargehistory`/`/api/session/{id}` human-readable-name lookup — explicitly out of scope per the spec, a separate future feature.

---

## File Structure

- **Modify: `custom_components/zaptec/zaptec/const.py`** — add `SELF_AUTHORIZATION_WINDOW`.
- **Modify: `custom_components/zaptec/zaptec/api.py`** — `Charger` gains self-authorization tracking: `_pending_self_auth_at`, `_self_authorized_ids`, an overridden `set_attributes()`, a timestamp write in `authorize_charge()`, and `is_self_authorized()`.
- **Modify: `custom_components/zaptec/sensor.py`** — new `ZaptecAuthorizedBySensor` class and two new `CHARGER_ENTITIES` descriptions.
- **Modify: `custom_components/zaptec/translations/en.json`** — `authorized_by` / `completed_session_authorized_by` entity names.
- **Modify: `tests/zaptec/test_api.py`** — unit tests for the new `Charger` self-authorization behavior (this file currently only has one live-credential test; these new tests are fully offline).
- **Modify: `tests/test_sensor.py`** — unit tests for `ZaptecAuthorizedBySensor`.

---

### Task 1: Self-authorization tracking on `Charger`

**Files:**
- Modify: `custom_components/zaptec/zaptec/const.py:48-51` (insert new constant)
- Modify: `custom_components/zaptec/zaptec/api.py:24-40` (import), `:623-629` (`__init__`), insert new `set_attributes` override after `:629`, `:753-757` (`authorize_charge`), insert new `is_self_authorized` after `:787`
- Test: `tests/zaptec/test_api.py`

**Interfaces:**
- Consumes: nothing new (uses `time` and `TDict`, already imported in `api.py`).
- Produces: `Charger.is_self_authorized(value: str) -> bool`, `Charger._pending_self_auth_at: float | None`, `Charger._self_authorized_ids: set[str]`, `SELF_AUTHORIZATION_WINDOW: int` (from `const.py`) — consumed by Task 2's `ZaptecAuthorizedBySensor` via `self.zaptec_obj.is_self_authorized(raw)`.

- [ ] **Step 1: Write the failing tests**

`tests/zaptec/test_api.py` currently starts with:

```python
"""Tests for zaptec/api.py."""

import logging

import pytest

from custom_components.zaptec.zaptec.api import Zaptec

_LOGGER = logging.getLogger(__name__)
```

Change the import block to add the new imports (keep `_LOGGER` and the existing `test_api` function as-is):

```python
"""Tests for zaptec/api.py."""

import logging
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.zaptec.zaptec import Charger
from custom_components.zaptec.zaptec.api import Zaptec
from custom_components.zaptec.zaptec.const import SELF_AUTHORIZATION_WINDOW

_LOGGER = logging.getLogger(__name__)
```

Then append these functions to the end of the file (after the existing `test_api` function), matching the `make_charger(data: dict[str, Any])` helper style already used in `tests/test_sensor.py`:

```python
def make_charger(data: dict[str, Any] | None = None) -> Charger:
    """Create a Charger backed by a mock Zaptec account (no network access)."""
    zaptec = MagicMock()
    zaptec.redact = MagicMock(side_effect=lambda v, **kw: v)
    zaptec.show_all_updates = False
    zaptec.request = AsyncMock(return_value=b"")
    return Charger(data or {}, zaptec)


@pytest.mark.asyncio
async def test_authorize_charge_records_pending_timestamp() -> None:
    """authorize_charge() marks the moment it was called, for later correlation."""
    charger = make_charger()
    assert charger._pending_self_auth_at is None  # noqa: SLF001

    before = time.monotonic()
    await charger.authorize_charge()
    after = time.monotonic()

    assert charger._pending_self_auth_at is not None  # noqa: SLF001
    assert before <= charger._pending_self_auth_at <= after  # noqa: SLF001


def test_set_attributes_learns_self_authorized_id_within_window() -> None:
    """A charger_current_user_uuid change shortly after authorize_charge() is learned."""
    charger = make_charger()
    charger._pending_self_auth_at = time.monotonic()  # noqa: SLF001

    charger.set_attributes({"ChargerCurrentUserUuid": "ble-abc"})

    assert charger.is_self_authorized("ble-abc") is True
    assert charger._pending_self_auth_at is None  # noqa: SLF001


def test_set_attributes_does_not_learn_outside_window() -> None:
    """A charger_current_user_uuid change outside the correlation window is not learned."""
    charger = make_charger()
    charger._pending_self_auth_at = (  # noqa: SLF001
        time.monotonic() - SELF_AUTHORIZATION_WINDOW - 5
    )

    charger.set_attributes({"ChargerCurrentUserUuid": "ble-xyz"})

    assert charger.is_self_authorized("ble-xyz") is False
    assert charger._pending_self_auth_at is None  # noqa: SLF001


def test_set_attributes_ignores_unrelated_changes_while_pending() -> None:
    """Updates that don't touch charger_current_user_uuid leave the pending marker intact."""
    charger = make_charger()
    charger._pending_self_auth_at = time.monotonic()  # noqa: SLF001

    charger.set_attributes({"Name": "New name"})

    assert charger._pending_self_auth_at is not None  # noqa: SLF001


def test_set_attributes_ignores_empty_uuid_while_pending() -> None:
    """An empty-string uuid update doesn't consume the pending window."""
    charger = make_charger()
    charger._pending_self_auth_at = time.monotonic()  # noqa: SLF001

    charger.set_attributes({"ChargerCurrentUserUuid": ""})

    assert charger._pending_self_auth_at is not None  # noqa: SLF001
    assert charger.is_self_authorized("") is False


def test_real_tap_without_pending_authorization_is_not_learned() -> None:
    """A charger_current_user_uuid change with no prior authorize_charge() call is a real tap."""
    charger = make_charger()

    charger.set_attributes({"ChargerCurrentUserUuid": "nfc-real-tag"})

    assert charger.is_self_authorized("nfc-real-tag") is False


def test_learned_id_is_recognized_indefinitely() -> None:
    """Recognition doesn't expire once learned - needed since completed_session.AuthenticationCode
    may only populate long after the live observation was learned."""
    charger = make_charger()
    charger._pending_self_auth_at = time.monotonic()  # noqa: SLF001
    charger.set_attributes({"ChargerCurrentUserUuid": "ble-abc"})

    # A later, unrelated update should not forget the learned id.
    charger.set_attributes({"TotalChargePower": 1500})

    assert charger.is_self_authorized("ble-abc") is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/zaptec/test_api.py -v`
Expected: FAIL — `ImportError: cannot import name 'SELF_AUTHORIZATION_WINDOW'` (or, once that's stubbed, `AttributeError: 'Charger' object has no attribute '_pending_self_auth_at'` / `has no attribute 'is_self_authorized'`).

- [ ] **Step 3: Add `SELF_AUTHORIZATION_WINDOW` to `const.py`**

In `custom_components/zaptec/zaptec/const.py`, insert after line 49 (`"""The default max_current to use if max_current is missing or invalid."""`), before the `TRUTHY = [...]` line:

```python
SELF_AUTHORIZATION_WINDOW = 30
"""Seconds after Charger.authorize_charge() during which a new
charger_current_user_uuid value is attributed to this integration itself,
rather than to a physical RFID/NFC/BLE tag tap."""

```

- [ ] **Step 4: Wire the import into `api.py`**

In `custom_components/zaptec/zaptec/api.py`, the `from .const import (...)` block (lines 24-40) currently reads:

```python
from .const import (
    API_RATELIMIT_MAX_REQUEST_RATE,
    API_RATELIMIT_PERIOD,
    API_RETRIES,
    API_RETRY_FACTOR,
    API_RETRY_INIT_DELAY,
    API_RETRY_JITTER,
    API_RETRY_MAXTIME,
    API_TIMEOUT,
    API_URL,
    CHARGER_EXCLUDES,
    DEFAULT_MAX_CURRENT,
    MAX_DEBUG_TEXT_LEN_ON_500,
    MISSING,
    TOKEN_URL,
    TRUTHY,
)
```

Change it to insert `SELF_AUTHORIZATION_WINDOW,` between `MISSING,` and `TOKEN_URL,`:

```python
from .const import (
    API_RATELIMIT_MAX_REQUEST_RATE,
    API_RATELIMIT_PERIOD,
    API_RETRIES,
    API_RETRY_FACTOR,
    API_RETRY_INIT_DELAY,
    API_RETRY_JITTER,
    API_RETRY_MAXTIME,
    API_TIMEOUT,
    API_URL,
    CHARGER_EXCLUDES,
    DEFAULT_MAX_CURRENT,
    MAX_DEBUG_TEXT_LEN_ON_500,
    MISSING,
    SELF_AUTHORIZATION_WINDOW,
    TOKEN_URL,
    TRUTHY,
)
```

- [ ] **Step 5: Initialize tracking state in `Charger.__init__`**

In `custom_components/zaptec/zaptec/api.py`, `Charger.__init__` (lines 623-629) currently reads:

```python
    def __init__(
        self, data: TDict, zaptec: Zaptec, installation: Installation | None = None
    ) -> None:
        """Initialize the Charger object."""
        super().__init__(data, zaptec)

        self.installation = installation
```

Change to initialize the new tracking attributes **before** calling `super().__init__()`, since that call triggers `set_attributes()` (overridden below), which reads them:

```python
    def __init__(
        self, data: TDict, zaptec: Zaptec, installation: Installation | None = None
    ) -> None:
        """Initialize the Charger object."""
        self._pending_self_auth_at: float | None = None
        self._self_authorized_ids: set[str] = set()
        super().__init__(data, zaptec)

        self.installation = installation
```

- [ ] **Step 6: Add the `set_attributes` override**

Immediately after the `__init__` method (right before `async def poll_info`), insert:

```python
    def set_attributes(self, data: TDict) -> None:
        """Set the class attributes, tracking self-triggered authorizations.

        If `charger_current_user_uuid` changes to a new value shortly after
        this integration called `authorize_charge()`, the new value is
        remembered so `is_self_authorized()` can recognize it later - even
        much later, e.g. in a `completed_session.AuthenticationCode` that
        only appears once the session ends.
        """
        old_uuid = self._attrs.get("charger_current_user_uuid")
        super().set_attributes(data)
        new_uuid = self._attrs.get("charger_current_user_uuid")

        if new_uuid and new_uuid != old_uuid and self._pending_self_auth_at is not None:
            elapsed = time.monotonic() - self._pending_self_auth_at
            self._pending_self_auth_at = None
            if elapsed < SELF_AUTHORIZATION_WINDOW:
                self._self_authorized_ids.add(str(new_uuid))
```

- [ ] **Step 7: Record the timestamp in `authorize_charge`**

`authorize_charge` (lines 753-757) currently reads:

```python
    async def authorize_charge(self) -> Any:
        """Authorize the charger to charge."""
        _LOGGER.debug("Authorize charge")
        # NOTE: Undocumented API call
        return await self.zaptec.request(f"chargers/{self.id}/authorizecharge", method="post")
```

Change to:

```python
    async def authorize_charge(self) -> Any:
        """Authorize the charger to charge."""
        _LOGGER.debug("Authorize charge")
        self._pending_self_auth_at = time.monotonic()
        # NOTE: Undocumented API call
        return await self.zaptec.request(f"chargers/{self.id}/authorizecharge", method="post")
```

- [ ] **Step 8: Add `is_self_authorized`**

Immediately after `is_charging` (lines 785-787, right before the `model_prefix` property), insert:

```python
    def is_self_authorized(self, value: str) -> bool:
        """Check whether `value` is a token this integration itself assigned via authorize_charge()."""
        return value in self._self_authorized_ids
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/zaptec/test_api.py -v`
Expected: PASS (7 new tests, plus the existing `test_api` skipped/passed as before).

- [ ] **Step 10: Lint**

Run:
```bash
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format custom_components/zaptec/zaptec/const.py custom_components/zaptec/zaptec/api.py tests/zaptec/test_api.py
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check custom_components/zaptec/zaptec/const.py custom_components/zaptec/zaptec/api.py tests/zaptec/test_api.py --fix
```
Expected: clean, or only pre-existing `api.py` findings unrelated to this change (see repo CLAUDE.md's note on the ~76 pre-existing `ruff check` errors). Add `# noqa` comments to any new finding in the lines this task added.

- [ ] **Step 11: Commit** (pause for explicit user approval first)

```bash
git add custom_components/zaptec/zaptec/const.py custom_components/zaptec/zaptec/api.py tests/zaptec/test_api.py
git commit -m "feat: track self-authorized charge sessions on Charger

Charger.authorize_charge() now records when it was called, and the
next charger_current_user_uuid change within SELF_AUTHORIZATION_WINDOW
is remembered as self-authorized. Charger.is_self_authorized() exposes
this for the upcoming authorized-by sensors (issue #200)."
```

---

### Task 2: `ZaptecAuthorizedBySensor` and entity descriptions

**Files:**
- Modify: `custom_components/zaptec/sensor.py:129` (insert new class after `ZaptecEnengySensor`), `:347` (append two new entries to `CHARGER_ENTITIES`)
- Modify: `custom_components/zaptec/translations/en.json:99-101` (insert `authorized_by`), `:122-125` (insert `completed_session_authorized_by`)
- Test: `tests/test_sensor.py`

**Interfaces:**
- Consumes: `Charger.is_self_authorized(value: str) -> bool` (Task 1).
- Produces: `ZaptecAuthorizedBySensor` class; `CHARGER_ENTITIES` gains keys `charger_current_user_uuid` (translation_key `authorized_by`) and `completed_session.AuthenticationCode` (translation_key `completed_session_authorized_by`). Nothing later depends on this.

- [ ] **Step 1: Write the failing tests**

In `tests/test_sensor.py`, change the import block:

```python
from custom_components.zaptec.sensor import (
    ZapSensorEntityDescription,
    ZaptecChargeSensor,
    ZaptecEnengySensor,
    ZaptecSensor,
    ZaptecSensorTranslate,
)
```

to:

```python
from custom_components.zaptec.sensor import (
    ZapSensorEntityDescription,
    ZaptecAuthorizedBySensor,
    ZaptecChargeSensor,
    ZaptecEnengySensor,
    ZaptecSensor,
    ZaptecSensorTranslate,
)
```

Then append these tests to the end of the file:

```python
def test_authorized_by_sensor_passes_through_raw_token(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecAuthorizedBySensor shows the raw token when not self-authorized."""
    charger = make_charger({"charger_current_user_uuid": "nfc-1234"})
    charger.is_self_authorized.return_value = False
    description = ZapSensorEntityDescription(
        key="charger_current_user_uuid", cls=ZaptecAuthorizedBySensor
    )
    entity = ZaptecAuthorizedBySensor(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()  # noqa: SLF001

    assert entity._attr_native_value == "nfc-1234"  # noqa: SLF001
    assert entity._attr_extra_state_attributes == {"id": "nfc-1234"}  # noqa: SLF001
    charger.is_self_authorized.assert_called_once_with("nfc-1234")


def test_authorized_by_sensor_maps_empty_string_to_none(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecAuthorizedBySensor shows None (HA "Unknown") for an empty token."""
    charger = make_charger({"charger_current_user_uuid": ""})
    description = ZapSensorEntityDescription(
        key="charger_current_user_uuid", cls=ZaptecAuthorizedBySensor
    )
    entity = ZaptecAuthorizedBySensor(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()  # noqa: SLF001

    assert entity._attr_native_value is None  # noqa: SLF001
    assert entity._attr_extra_state_attributes == {"id": None}  # noqa: SLF001
    charger.is_self_authorized.assert_not_called()


def test_authorized_by_sensor_relabels_self_authorized_value(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecAuthorizedBySensor shows "Home Assistant" for a recognized token."""
    charger = make_charger({"charger_current_user_uuid": "ble-abc"})
    charger.is_self_authorized.return_value = True
    description = ZapSensorEntityDescription(
        key="charger_current_user_uuid", cls=ZaptecAuthorizedBySensor
    )
    entity = ZaptecAuthorizedBySensor(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()  # noqa: SLF001

    assert entity._attr_native_value == "Home Assistant"  # noqa: SLF001
    assert entity._attr_extra_state_attributes == {"id": "ble-abc"}  # noqa: SLF001


def test_authorized_by_sensor_reads_dotted_completed_session_key(
    coordinator: ZaptecUpdateCoordinator,
) -> None:
    """ZaptecAuthorizedBySensor reads a dotted key, e.g. completed_session.AuthenticationCode."""
    charger = make_charger({"completed_session": {"AuthenticationCode": "ble-abc"}})
    charger.is_self_authorized.return_value = False
    description = ZapSensorEntityDescription(
        key="completed_session.AuthenticationCode", cls=ZaptecAuthorizedBySensor
    )
    entity = ZaptecAuthorizedBySensor(coordinator, charger, description, DeviceInfo())

    entity._update_from_zaptec()  # noqa: SLF001

    assert entity._attr_native_value == "ble-abc"  # noqa: SLF001
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_sensor.py -v`
Expected: FAIL — `ImportError: cannot import name 'ZaptecAuthorizedBySensor'`.

- [ ] **Step 3: Add `ZaptecAuthorizedBySensor` to `sensor.py`**

In `custom_components/zaptec/sensor.py`, immediately after the end of `ZaptecEnengySensor` (line 129, the blank line before `@dataclass(frozen=True, kw_only=True)` / `class ZapSensorEntityDescription`), insert:

```python
class ZaptecAuthorizedBySensor(ZaptecSensor):
    """Sensor for who/what authorized a charging session.

    Maps an empty string to None (HA shows "Unknown"), and relabels a
    recognized self-authorization (see Charger.is_self_authorized) as
    "Home Assistant" while always exposing the raw token as the "id"
    extra state attribute.
    """

    _log_attribute = "_attr_native_value"

    @callback
    def _update_from_zaptec(self) -> None:
        """Update the entity from Zaptec data."""
        # Called from ZaptecBaseEntity._handle_coordinator_update()
        raw = self._get_zaptec_value() or None
        if raw is not None and self.zaptec_obj.is_self_authorized(raw):
            self._attr_native_value = "Home Assistant"
        else:
            self._attr_native_value = raw
        self._attr_extra_state_attributes = {"id": raw}
        self._attr_available = True


```

- [ ] **Step 4: Add the two entity descriptions**

In `custom_components/zaptec/sensor.py`, `CHARGER_ENTITIES` currently ends with the `device_type` entry (closing at line 347) followed by `]` on line 348:

```python
    ZapSensorEntityDescription(
        key="device_type",
        translation_key="device_type",
        device_class=SensorDeviceClass.ENUM,
        entity_category=const.EntityCategory.DIAGNOSTIC,
        options=ZCONST.device_types_list,
        icon="mdi:shape-outline",
        cls=ZaptecSensor,
        # No state class as its not a numeric value
    ),
]
```

Change to append two entries before the closing `]`:

```python
    ZapSensorEntityDescription(
        key="device_type",
        translation_key="device_type",
        device_class=SensorDeviceClass.ENUM,
        entity_category=const.EntityCategory.DIAGNOSTIC,
        options=ZCONST.device_types_list,
        icon="mdi:shape-outline",
        cls=ZaptecSensor,
        # No state class as its not a numeric value
    ),
    ZapSensorEntityDescription(
        key="charger_current_user_uuid",
        translation_key="authorized_by",
        icon="mdi:card-account-details-outline",
        cls=ZaptecAuthorizedBySensor,
        # No state/device class: opaque identifier string, not numeric or enum
    ),
    ZapSensorEntityDescription(
        key="completed_session.AuthenticationCode",
        translation_key="completed_session_authorized_by",
        icon="mdi:card-account-details",
        cls=ZaptecAuthorizedBySensor,
        # No state/device class: opaque identifier string, not numeric or enum
    ),
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_sensor.py -v`
Expected: PASS (all sensor tests, including the 4 new ones).

- [ ] **Step 6: Add translations**

In `custom_components/zaptec/translations/en.json`, the `entity.sensor` section currently has, around line 93-101:

```json
            "authentication_type": {
                "name": "Authentication type",
                "state": {
                    "native": "Native",
                    "ocpp": "OCPP",
                    "webhooks": "Web Hooks"
                }
            },
            "available_current_phase1": {
                "name": "Available current phase 1"
            },
```

Insert `authorized_by` between them:

```json
            "authentication_type": {
                "name": "Authentication type",
                "state": {
                    "native": "Native",
                    "ocpp": "OCPP",
                    "webhooks": "Web Hooks"
                }
            },
            "authorized_by": {
                "name": "Authorized by"
            },
            "available_current_phase1": {
                "name": "Available current phase 1"
            },
```

And around line 113-125:

```json
            "charger_operation_mode": {
                "name": "Charger mode",
                "state": {
                    "connected_charging": "Charging",
                    "connected_finished": "Charge done",
                    "connected_requesting": "Waiting",
                    "disconnected": "Disconnected",
                    "unknown": "Unknown"
                }
            },
            "completed_session_energy": {
                "name": "Completed session energy"
            },
```

Insert `completed_session_authorized_by` between them:

```json
            "charger_operation_mode": {
                "name": "Charger mode",
                "state": {
                    "connected_charging": "Charging",
                    "connected_finished": "Charge done",
                    "connected_requesting": "Waiting",
                    "disconnected": "Disconnected",
                    "unknown": "Unknown"
                }
            },
            "completed_session_authorized_by": {
                "name": "Last session authorized by"
            },
            "completed_session_energy": {
                "name": "Completed session energy"
            },
```

- [ ] **Step 7: Lint**

Run:
```bash
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format custom_components/zaptec/sensor.py tests/test_sensor.py
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check custom_components/zaptec/sensor.py tests/test_sensor.py --fix
```
Expected: clean. Add `# noqa` comments to any new finding in the lines this task added.

Validate the JSON is well-formed:
```bash
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -c "import json; json.load(open('custom_components/zaptec/translations/en.json', encoding='utf-8'))"
```
Expected: no output (no exception).

- [ ] **Step 8: Commit** (pause for explicit user approval first)

```bash
git add custom_components/zaptec/sensor.py custom_components/zaptec/translations/en.json tests/test_sensor.py
git commit -m "feat: add authorized-by sensors (issue #200)

Adds charger_current_user_uuid and completed_session.AuthenticationCode
as sensors, so users can see (and split energy per) which RFID/NFC/BLE
token authorized a charge. Sessions authorized from within this
integration are labeled \"Home Assistant\" via Charger.is_self_authorized()."
```

---

### Task 3: Full suite verification

**Files:** none (verification only).

**Interfaces:**
- Consumes: everything from Tasks 1-2.
- Produces: nothing (terminal task).

- [ ] **Step 1: Run the full test suite**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests -q`
Expected: all pass, except `tests/zaptec/test_zconst.py` and `tests/zaptec/test_redact.py`, which fail in this dev environment regardless of this change (documented DNS-resolution gap in the repo's `CLAUDE.md` — not a regression).

- [ ] **Step 2: Run the full lint gate**

Run:
```bash
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format custom_components tests --diff
"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check custom_components tests
```
Expected: `ruff format --diff` prints nothing (clean). `ruff check` may report the ~76 pre-existing findings noted in the repo's `CLAUDE.md` (mostly in `zaptec/api.py`) — confirm no *new* findings appear in the lines this plan's tasks touched.

- [ ] **Step 3: Manual sanity check of the design's key assumption**

This plan's self-authorization recognition rests on an unverified assumption (documented in the spec): that Zaptec assigns the same synthetic UUID for every API-triggered authorization on a given account. This can't be verified without live credentials in this dev environment. Note in the PR description that this should be manually confirmed against a real Zaptec account before merge — trigger "Authorize charging" from HA and confirm the `Authorized by` sensor shows "Home Assistant".
