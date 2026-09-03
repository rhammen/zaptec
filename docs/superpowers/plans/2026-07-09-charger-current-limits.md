# Charger Current-Limit Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce the current-value constraints on Zaptec number entities that upstream issue [custom-components/zaptec#289](https://github.com/custom-components/zaptec/issues/289) confirmed as valid, without touching the one field where Zaptec's own docs contradict the blanket recommendation.

**Architecture:** Two independent, additive changes to `custom_components/zaptec/number.py`: (1) raise the declared `native_min_value` on the `charger_min_current` entity description from `0` to `6`, which is enough for Home Assistant's own `number.set_value` service to reject out-of-range values before `async_set_native_value` is even called; (2) add a small relational check inside `ZaptecSettingNumber.async_set_native_value` so that `charger_max_current` can never be set below the charger's current `charger_min_current`, and vice versa.

**Tech Stack:** Python 3.14, Home Assistant custom component, pytest + pytest-asyncio (`asyncio_mode = "auto"`, no `@pytest.mark.asyncio` needed).

> **Revision (2026-09-03):** Task 1 was narrowed after implementation. `available_current` no longer gets the 6A floor — `0` there is a regularly used way to hold the charger off (it is what the author's own solar-surplus automation writes to stop charging), and a floor would make Home Assistant reject those `number.set_value` calls with `ServiceValidationError`. Only `charger_min_current` keeps the floor. See the spec's Background and Scope sections.

## Global Constraints

- `available_current` keeps `native_min_value=0` — see the revision note above.
- `three_to_one_phase_switch_current` keeps `native_min_value=0` — do not add a floor to this field (Zaptec's own docs describe `0` as a valid special value to force 3-phase charging; this is spec §Scope, out of scope).
- `charger_max_current` does not get a static floor — only the relational (`>= charger_min_current`) check applies to it.
- No new `ZapNumberEntityDescription` fields — the relational check is a plain conditional inside the existing method, keyed off `entity_description.setting` (only two cases exist: `"minChargeCurrent"`, `"maxChargeCurrent"`).
- Skip the relational check (allow the set to proceed) when the sibling value is unavailable (`None`) — never block on missing data.
- Full spec: `docs/superpowers/specs/2026-07-09-charger-current-limits-design.md`.

---

### Task 1: Raise the minimum-current floor on `charger_min_current`

**Files:**
- Modify: `custom_components/zaptec/number.py:23` (add constant), `:150-171` (`INSTALLATION_ENTITIES`), `:173-206` (`CHARGER_ENTITIES`)
- Test: `tests/test_number.py` (new file)

**Interfaces:**
- Produces: `MIN_CHARGE_CURRENT: int = 6` module-level constant in `custom_components/zaptec/number.py`, used as `native_min_value` on the `charger_min_current` `ZapNumberEntityDescription` entry only. Task 2 does not depend on this constant, but both tasks touch the same file.

- [ ] **Step 1: Write the failing test**

Create `tests/test_number.py`:

```python
"""Tests for number.py."""

from custom_components.zaptec.number import (
    CHARGER_ENTITIES,
    INSTALLATION_ENTITIES,
    MIN_CHARGE_CURRENT,
)


def _find(descriptions: list, key: str):
    return next(d for d in descriptions if d.key == key)


def test_available_current_keeps_zero_floor() -> None:
    # 0 is a regularly used way to hold the charger off, so this field
    # must not get the 6A floor.
    description = _find(INSTALLATION_ENTITIES, "available_current")
    assert description.native_min_value == 0


def test_charger_min_current_has_min_charge_current_floor() -> None:
    description = _find(CHARGER_ENTITIES, "charger_min_current")
    assert description.native_min_value == MIN_CHARGE_CURRENT


def test_charger_max_current_has_no_static_floor() -> None:
    # Only the relational check (Task 2) constrains this field's minimum.
    description = _find(CHARGER_ENTITIES, "charger_max_current")
    assert description.native_min_value == 0


def test_three_to_one_phase_switch_current_keeps_zero_floor() -> None:
    # Zaptec's docs describe 0 as a valid special value to force 3-phase
    # charging, so this field must not get the 6A floor.
    description = _find(INSTALLATION_ENTITIES, "three_to_one_phase_switch_current")
    assert description.native_min_value == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m pytest tests/test_number.py -v`
Expected: `ImportError: cannot import name 'MIN_CHARGE_CURRENT'` (the constant doesn't exist yet), or `FAIL` on the `charger_min_current` floor assertion once the import is fixed manually — either way, it must not pass as-is.

- [ ] **Step 3: Add the constant and bump the two floors**

In `custom_components/zaptec/number.py`, add the constant right after the logger declaration (currently line 23):

```python
_LOGGER = logging.getLogger(__name__)

MIN_CHARGE_CURRENT = 6  # IEC 61851 minimum current for EV charging (amps)
```

Leave `INSTALLATION_ENTITIES` entirely unchanged — both the `available_current` entry and the `three_to_one_phase_switch_current` entry directly below it keep `native_min_value=0`. See the revision note at the top of this plan.

In `CHARGER_ENTITIES`, change the `charger_min_current` entry's `native_min_value` from `0` to `MIN_CHARGE_CURRENT`:

```python
    ZapNumberEntityDescription(
        key="charger_min_current",
        translation_key="charger_min_current",
        device_class=NumberDeviceClass.CURRENT,
        entity_category=EntityCategory.CONFIG,
        native_min_value=MIN_CHARGE_CURRENT,
        native_max_value=32,
        icon="mdi:current-ac",
        native_unit_of_measurement=const.UnitOfElectricCurrent.AMPERE,
        cls=ZaptecSettingNumber,
        setting="minChargeCurrent",
    ),
```

Leave the `charger_max_current` entry directly below it unchanged (`native_min_value=0`).

- [ ] **Step 4: Run test to verify it passes**

Run: `"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m pytest tests/test_number.py -v`
Expected: 4 passed

- [ ] **Step 5: Lint**

Run:
```bash
"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m ruff format custom_components/zaptec/number.py tests/test_number.py
"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m ruff check custom_components/zaptec/number.py tests/test_number.py --fix
```
Expected: `ruff format` reports 0 or 2 files reformatted (fine either way); `ruff check` reports no remaining errors in these two files.

- [ ] **Step 6: Commit**

```bash
git add custom_components/zaptec/number.py tests/test_number.py
git commit -m "Enforce a 6A minimum on charger_min_current"
```

---

### Task 2: Enforce `charger_max_current >= charger_min_current`

**Files:**
- Modify: `custom_components/zaptec/number.py:104-116` (`ZaptecSettingNumber.async_set_native_value`)
- Test: `tests/test_number.py` (append)

**Interfaces:**
- Consumes: `MIN_CHARGE_CURRENT`, `CHARGER_ENTITIES` from Task 1 (only used to look up the two entity descriptions by key in tests; the production code change does not depend on the Task 1 constant).
- Produces: `ZaptecSettingNumber.async_set_native_value(value: float) -> None` now raises `HomeAssistantError` (imported from `homeassistant.exceptions`, already used in this file) instead of calling `self.zaptec_obj.set_settings(...)` when the relational constraint is violated. No new public interface — behavior change only.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_number.py`:

```python
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.zaptec.number import ZaptecSettingNumber


class FakeZaptecObj:
    """Minimal Mapping-like stand-in for a Charger, for entity-level unit tests."""

    def __init__(self, id: str = "test-id", qual_id: str = "test-qual-id", **attrs) -> None:
        self.id = id
        self.qual_id = qual_id
        self._attrs = attrs
        self.set_settings = AsyncMock()

    def get(self, key: str, default=None):
        return self._attrs.get(key, default)


class FakeCoordinator:
    def __init__(self) -> None:
        self.trigger_poll = AsyncMock()


def _make_entity(setting: str, **charger_attrs) -> ZaptecSettingNumber:
    key = "charger_min_current" if setting == "minChargeCurrent" else "charger_max_current"
    description = _find(CHARGER_ENTITIES, key)
    return ZaptecSettingNumber(
        coordinator=FakeCoordinator(),
        zaptec_object=FakeZaptecObj(**charger_attrs),
        description=description,
        device_info={},
    )


async def test_min_current_above_current_max_is_rejected() -> None:
    entity = _make_entity("minChargeCurrent", charger_max_current=10)
    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(12)
    entity.zaptec_obj.set_settings.assert_not_called()


async def test_max_current_below_current_min_is_rejected() -> None:
    entity = _make_entity("maxChargeCurrent", charger_min_current=10)
    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(5)
    entity.zaptec_obj.set_settings.assert_not_called()


async def test_min_current_within_current_max_is_accepted() -> None:
    entity = _make_entity("minChargeCurrent", charger_max_current=10)
    await entity.async_set_native_value(6)
    entity.zaptec_obj.set_settings.assert_awaited_once_with({"minChargeCurrent": 6})


async def test_max_current_above_current_min_is_accepted() -> None:
    entity = _make_entity("maxChargeCurrent", charger_min_current=6)
    await entity.async_set_native_value(10)
    entity.zaptec_obj.set_settings.assert_awaited_once_with({"maxChargeCurrent": 10})


async def test_min_current_check_skipped_when_max_unavailable() -> None:
    entity = _make_entity("minChargeCurrent")  # no charger_max_current attr at all
    await entity.async_set_native_value(6)
    entity.zaptec_obj.set_settings.assert_awaited_once_with({"minChargeCurrent": 6})


async def test_max_current_check_skipped_when_min_unavailable() -> None:
    entity = _make_entity("maxChargeCurrent")  # no charger_min_current attr at all
    await entity.async_set_native_value(10)
    entity.zaptec_obj.set_settings.assert_awaited_once_with({"maxChargeCurrent": 10})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m pytest tests/test_number.py -v -k "min_current or max_current"`
Expected: `test_min_current_above_current_max_is_rejected` and `test_max_current_below_current_min_is_rejected` FAIL (no `HomeAssistantError` raised — `set_settings` gets called unconditionally today). The other four should already pass against the current implementation, since it always calls `set_settings`.

- [ ] **Step 3: Implement the relational check**

Replace `async_set_native_value` in `ZaptecSettingNumber` (`custom_components/zaptec/number.py:104-116`):

```python
    async def async_set_native_value(self, value: float) -> None:
        """Update to Zaptec."""
        self._log_number(value)
        setting = self.entity_description.setting
        if not setting:
            raise HomeAssistantError(f"No setting for {self.__class__.__qualname__}.{self.key}")

        if setting == "minChargeCurrent":
            max_current = self.zaptec_obj.get("charger_max_current")
            if max_current is not None and value > max_current:
                raise HomeAssistantError(
                    f"Min current {value} cannot be higher than max current {max_current}"
                )
        elif setting == "maxChargeCurrent":
            min_current = self.zaptec_obj.get("charger_min_current")
            if min_current is not None and value < min_current:
                raise HomeAssistantError(
                    f"Max current {value} cannot be lower than min current {min_current}"
                )

        try:
            await self.zaptec_obj.set_settings({setting: value})
        except Exception as exc:
            raise HomeAssistantError(f"Setting {setting} to {value} failed") from exc

        await self.trigger_poll()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m pytest tests/test_number.py -v`
Expected: 10 passed (4 from Task 1 + 6 from this task)

- [ ] **Step 5: Full test suite + lint**

Run:
```bash
SKIP_ZAPTEC_API_TEST=true "C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m pytest tests -q
"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m ruff format custom_components/zaptec/number.py tests/test_number.py
"C:\Users\rhamm\anaconda3\envs\py314\python.exe" -m ruff check custom_components/zaptec/number.py tests/test_number.py --fix
```
Expected: only the known `test_zconst.py` / `test_redact.py` DNS-resolver failures (per `CLAUDE.md`'s documented environment gap), no new failures; no lint errors in these two files.

- [ ] **Step 6: Commit**

```bash
git add custom_components/zaptec/number.py tests/test_number.py
git commit -m "Enforce charger_max_current >= charger_min_current"
```
