# Enforce known current-value constraints on number entities (upstream #289)

## Background

Upstream issue [custom-components/zaptec#289](https://github.com/custom-components/zaptec/issues/289) reports that the Zaptec API allows setting `ChargerMinCurrent` to `0`, which then causes a validation error in the Zaptec Portal when changing another setting. A Zaptec staff member (bonfaceOchieng) confirmed in the comments:

- `ChargerMinCurrent` should be enforced with a minimum of 6A (IEC minimum for EV charging).
- `ChargerMaxCurrent` must always be >= `ChargerMinCurrent`, and within the charger's configured max capacity.
- `AvailableCurrent` should be >= 6A, and not higher than the physical max.
- `ThreeToOnePhaseSwitchCurrent` must also be >= 6A.

However, a maintainer (sveinse) pointed out that Zaptec's own docs describe setting `ThreeToOnePhaseSwitchCurrent` to `0` intentionally, to force 3-phase charging. This directly contradicts the blanket "must be >= 6A" recommendation for that one field, and the discrepancy is explicitly unresolved (deferred to upstream #192, which asks Zaptec for a canonical list of integrator-side validation rules).

`AvailableCurrent = 0` is a second such case. It is not a charging current at all but a regularly used way to hold the charger off — setting the installation's available current to 0 and back up again is a common pattern for solar-surplus and load-balancing automations. A 6A floor there would reject those calls at the `number.set_value` service layer and break existing user automations, so the blanket ">= 6A" advice does not hold for this field either.

This design implements only what's actually confirmed and non-contradictory, leaving the disputed fields untouched.

## Scope

In scope:
- Raise `native_min_value` from `0` to `6` for `charger_min_current`.
- Add a runtime check that `charger_max_current >= charger_min_current` when either is set via `number.set_value`.

Out of scope (explicitly deferred, not silently dropped):
- `three_to_one_phase_switch_current` keeps `native_min_value=0` — the "0 forces 3-phase" behavior is documented by Zaptec and would break if we imposed a 6A floor here.
- `available_current` keeps `native_min_value=0` — see above; 0 is a regularly used way to hold the charger off. Rejecting 1-5A there while still allowing 0 was considered and dropped: it would need custom validation in `async_set_native_value` (the static floor cannot express "0 or >= 6"), for a range the Portal itself does not clearly reject, and the field is written by automations far more often than by hand.
- `charger_max_current` does not get its own static 6A floor — its constraint is relational (>= min), not absolute. The relational check keeps `charger_max_current` >= whatever `charger_min_current` currently *is*; note that raising the floor changes the writable range, not the stored value, so a charger already reporting a min of 0 (the #289 scenario) still accepts a max below 6A until the user raises its min. Once min has been set through HA (now >= 6A), max is transitively floored at 6A too.

## Implementation

All changes are in `custom_components/zaptec/number.py`.

### 1. Floor bump

Add a module-level constant:

```python
MIN_CHARGE_CURRENT = 6
```

Use it as `native_min_value` for the `charger_min_current` (`CHARGER_ENTITIES`) description, replacing the current `native_min_value=0`.

This is sufficient on its own: Home Assistant's `number.set_value` service handler rejects out-of-range values with `ServiceValidationError` before `async_set_native_value` is ever called (confirmed by reading `homeassistant/components/number/__init__.py`), and the frontend slider is bounded by the same value. No custom validation code is needed for this part.

### 2. Relational check (max >= min)

`ZaptecSettingNumber.async_set_native_value` (in `custom_components/zaptec/number.py`) currently just calls `self.zaptec_obj.set_settings({self.entity_description.setting: value})`. Add a check before that call, keyed off `entity_description.setting`:

- If `setting == "minChargeCurrent"`: read `self.zaptec_obj.get("charger_max_current")`. If it is not `None` and `value > max_current`, raise `HomeAssistantError` (matching the existing error-handling style in this file) instead of calling `set_settings`.
- If `setting == "maxChargeCurrent"`: read `self.zaptec_obj.get("charger_min_current")`. If it is not `None` and `value < min_current`, raise `HomeAssistantError`.
- If the sibling value is `None` (not yet reported), skip the check and proceed — don't block on missing data.

No new `ZapNumberEntityDescription` fields are needed since there are only two cases; the check is a small conditional inside the existing method.

### 3. Tests

Add `tests/test_number.py` (no tests currently exist for `number.py`), covering the declared floors only: `charger_min_current` declares `native_min_value == MIN_CHARGE_CURRENT`, while `available_current`, `three_to_one_phase_switch_current` and `charger_max_current` still declare `native_min_value == 0`. One `pytest.mark.parametrize` with named `pytest.param` ids, per `AGENTS.md`.

**The relational check is deliberately shipped without tests on this branch.** Testing it means driving `async_set_native_value`, and master has no test harness: `tests/` holds no entity-level tests and no `mock_zaptec` fixture, so the only option here is to instantiate the entity directly against hand-rolled fakes. That is the style sveinse asked to move away from in his review of #394 ("what testing facilities exist in HA that can assist us?"), which #414 exists to replace. Basing this branch on #414 instead would block an independent bugfix behind the largest open PR, and several branches already queue behind it.

So the entity-level cases below are deferred to a follow-up once #414 merges, written against `setup_integration`/`mock_zaptec` and asserting through `hass.services.async_call` + `pytest.raises(HomeAssistantError)` rather than a mocked `set_settings`:
- min above the current max, and max below the current min, are both rejected and never reach the API.
- `min == max` is accepted (the constraint is `>=`, not `>`).
- A sibling that is missing, or left as a raw non-numeric value by a failed type conversion, skips the check rather than blocking or raising `TypeError`.

## Non-goals

- No change to `ThreeToOnePhaseSwitchCurrent` or `AvailableCurrent` behavior.
- No attempt to resolve the broader validation-rules request tracked in upstream #192.
- No change to `ZaptecAvailableCurrentNumber` (its max is already bounded by the installation's reported `MaxCurrent`).
