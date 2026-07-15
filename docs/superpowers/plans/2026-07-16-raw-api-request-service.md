# Raw API request service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `zaptec.raw_api_request` HA service that sends a user-specified HTTP request through the integration's authenticated Zaptec session, with no validation of the endpoint or payload, as an escape hatch for undocumented API surface (e.g. Eco Mode, [upstream issue #153](https://github.com/custom-components/zaptec/issues/153)).

**Architecture:** New service registered in `custom_components/zaptec/services.py` alongside the existing ones, reusing the existing `iter_objects()` device/entity-id resolution (generalized to accept either a `Charger` or an `Installation` target) and the already-authenticated `obj.zaptec.request()` transport. Returns the API response via HA's service-response-data feature so it's usable from automations (`response_variable`).

**Tech Stack:** Python 3.14, Home Assistant custom component, `voluptuous` for service schemas, `pytest`/`pytest-asyncio` for tests.

## Global Constraints

- Full design spec: `docs/superpowers/specs/2026-07-16-raw-api-request-service-design.md` — this plan implements it as written; consult it for the "why" behind any choice below.
- Work happens on branch `feature/raw-api-request-service` (already created, stacked on `fix/issue-253-refactor-services`). Do not create another branch.
- **Never run `git commit` without the user's explicit go-ahead first** (this repo's `CLAUDE.md`). Each task ends with a commit step below — pause and ask before running it; don't auto-commit.
- `method` is restricted to `GET`/`POST`/`PUT` only — no `DELETE` (per spec, to rule out accidentally deleting a charger/installation through this path).
- `{id}` substitution in `path` is a plain `str.replace("{id}", obj.id)` — no templating engine, no error if `{id}` is absent or malformed. `path` and `data` get no other validation. This is intentional per the spec's "responsibility on the user" scope — do not add extra safeguards.
- Python for all commands: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe"` (forward slashes — see `CLAUDE.md` for why backslash paths break the permission allowlist here).
- Test command: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests -q`
- Lint gate that must stay clean before any task is considered done: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check --exclude custom_components/zaptec/zaptec/api.py` and `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format custom_components tests --diff`.

---

## File Structure

- **Modify** `custom_components/zaptec/services.py` — generalize `iter_objects()`'s `mustbe` parameter to accept a tuple of types (Task 1); add `RAW_API_REQUEST_SCHEMA` (Task 2); add `service_handle_raw_api_request` and register it in `async_setup_services` with `supports_response` (Task 3).
- **Modify** `custom_components/zaptec/services.yaml` — document the new service (Task 4).
- **Modify** `DEVELOPMENT.md` — add a usage section with two worked examples (Task 5).
- **Modify** `tests/test_services.py` — tests for all of the above.

No new files.

---

### Task 1: Generalize `iter_objects()` to accept a tuple of types

**Files:**
- Modify: `custom_components/zaptec/services.py` (the `iter_objects` function)
- Test: `tests/test_services.py`

**Interfaces:**
- Consumes: existing `_get_as_set`, `_iter_managers`, `DOMAIN`, `Charger`, `Installation` (all already imported/defined in `services.py`; unchanged).
- Produces: `iter_objects[T](service_call: ServiceCall, mustbe: type[T] | tuple[type, ...]) -> Generator[tuple[ZaptecUpdateCoordinator, T]]`. Existing callers (`mustbe=Charger`, `mustbe=Installation`) keep working unchanged. Task 3 will call it with `mustbe=(Charger, Installation)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_services.py`, directly after `test_unloaded_config_entry_without_runtime_data_is_skipped` (i.e. still inside the "iter_objects resolution / error paths" section, before the `# Individual service handlers` comment header):

```python
async def test_iter_objects_tuple_mustbe_resolves_installation_id(
    hass: MagicMock, manager: MagicMock, add_installation: Any
) -> None:
    """iter_objects accepts a tuple mustbe and resolves installation_id through it."""
    installation, coordinator = add_installation("install1")

    call = make_call(hass, {"installation_id": "install1"})
    results = list(services_module.iter_objects(call, mustbe=(Charger, Installation)))

    assert results == [(coordinator, installation)]


async def test_iter_objects_tuple_mustbe_resolves_charger_id(
    hass: MagicMock, manager: MagicMock, add_charger: Any
) -> None:
    """iter_objects accepts a tuple mustbe and resolves charger_id through it."""
    charger, coordinator = add_charger("charger1")

    call = make_call(hass, {"charger_id": "charger1"})
    results = list(services_module.iter_objects(call, mustbe=(Charger, Installation)))

    assert results == [(coordinator, charger)]


async def test_iter_objects_tuple_mustbe_wrong_type_raises_with_both_names(
    hass: MagicMock, manager: MagicMock
) -> None:
    """A resolved object that is neither type in the tuple names both types in the error."""
    manager.zaptec["thing1"] = MagicMock(spec=[])
    manager.device_coordinators["thing1"] = MagicMock()

    call = make_call(hass, {"charger_id": "thing1"})
    with pytest.raises(HomeAssistantError, match="is not a Charger or Installation"):
        list(services_module.iter_objects(call, mustbe=(Charger, Installation)))


async def test_iter_objects_tuple_mustbe_missing_field_names_both(
    hass: MagicMock, manager: MagicMock
) -> None:
    """No ids specified with a tuple mustbe names both legacy id fields in the error."""
    call = make_call(hass, {})
    with pytest.raises(
        HomeAssistantError, match="Missing field 'charger_id' or 'installation_id'"
    ):
        list(services_module.iter_objects(call, mustbe=(Charger, Installation)))
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_services.py -k tuple_mustbe -v`

Expected: all four FAIL. `resolves_installation_id` and `resolves_charger_id` fail with `HomeAssistantError: No zaptec devices specified` (the legacy id field isn't recognized yet because `mustbe` is a tuple, not `Charger`/`Installation` directly). `wrong_type_raises_with_both_names` fails the same way (uids never gets populated, so it never reaches the isinstance check). `missing_field_names_both` currently already passes with the *old* message shape by coincidence of both being empty — check its actual output; if it unexpectedly passes, that's fine, it'll still be correct after Step 3.

- [ ] **Step 3: Generalize `iter_objects`**

Replace the whole `iter_objects` function body in `custom_components/zaptec/services.py` with:

```python
def iter_objects[T](
    service_call: ServiceCall, mustbe: type[T] | tuple[type, ...]
) -> Generator[tuple[ZaptecUpdateCoordinator, T]]:
    """Resolve the devices/entities targeted by a service call to zaptec objects.

    Devices are looked up across every loaded zaptec config entry, not just
    one, so a service call still resolves correctly when multiple Zaptec
    accounts are configured. `mustbe` may be a single type or a tuple of
    types, to support services that can target either a Charger or an
    Installation.
    """
    hass = service_call.hass
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    device_ids = _get_as_set(service_call, "device_id")
    lookup: dict[str, str] = {}

    # Parse all entities and find their device ids which is appended to the
    # list of devices.
    for entity_id in _get_as_set(service_call, "entity_id"):
        entity_entry = ent_reg.async_get(entity_id)
        if entity_entry is None:
            raise HomeAssistantError(f"Unable to find entity '{entity_id}'")
        if not entity_entry.device_id:
            raise HomeAssistantError(f"Entity '{entity_id}' doesn't have a device")
        device_ids.add(entity_entry.device_id)
        lookup[entity_entry.device_id] = f"entity '{entity_id}'"

    # Parse all device ids and find the uid for each device
    uids: set[str] = set()
    for device_id in device_ids:
        device_entry = dev_reg.async_get(device_id)
        err_device = lookup.get(device_id, f"device '{device_id}'")
        if device_entry is None:
            raise HomeAssistantError(f"Unable to find device {err_device}")
        err_device = lookup.get(device_id, f"device {device_entry.name}")
        if not device_entry.identifiers:
            raise HomeAssistantError(f"Unable to find identifiers for {err_device}")
        for domain, uid in device_entry.identifiers:
            if domain != DOMAIN:
                raise HomeAssistantError(f"Non-zaptec device specified {err_device}")
            uids.add(uid)
            lookup[uid] = err_device

    # Append any legacy charger_id or installation_id that might be specified.
    # Check the legacy field for every type present in mustbe.
    types = mustbe if isinstance(mustbe, tuple) else (mustbe,)
    field_by_type = {Charger: "charger_id", Installation: "installation_id"}
    fields = [field_by_type[t] for t in types if t in field_by_type]
    for field in fields:
        uids.update(_get_as_set(service_call, field))

    # Any uid specified at all?
    if not uids:
        if fields:
            joined = " or ".join(f"'{f}'" for f in fields)
            suffix = f". Missing field {joined}"
        else:
            suffix = ""
        raise HomeAssistantError(f"No zaptec devices specified{suffix}")

    managers = list(_iter_managers(hass))

    # Loop through every uid and find the object, searching every manager
    for uid in uids:
        # Set the human readable identifier for the error message
        err_device = f"{lookup[uid]} ({uid})" if uid in lookup else f"id {uid}"

        zaptec_object = None
        coordinator = None
        for manager in managers:
            zaptec_object = manager.zaptec.get(uid)
            if zaptec_object is not None:
                coordinator = manager.device_coordinators.get(uid)
                break

        if zaptec_object is None:
            raise HomeAssistantError(f"Unable to find zaptec object for {err_device}")
        if not isinstance(zaptec_object, mustbe):
            type_names = " or ".join(t.__name__ for t in types)
            raise HomeAssistantError(f"{err_device} is not a {type_names}")
        if coordinator is None:
            raise HomeAssistantError(f"{err_device} is not available")

        yield coordinator, zaptec_object
```

This is a drop-in replacement for the existing function — only the "legacy id field" block, the "no uids" error message, and the "wrong type" error message change to handle a tuple `mustbe`. Single-type callers (`mustbe=Charger`, `mustbe=Installation`) produce byte-for-byte the same error strings as before.

- [ ] **Step 4: Run the full test file to verify everything passes**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_services.py -v`

Expected: PASS — all 4 new tests plus every pre-existing test in the file (in particular `test_no_ids_specified_raises_with_missing_field` and `test_wrong_object_type_raises`, which exercise the single-type path and must still show the old message text).

- [ ] **Step 5: Commit**

Ask the user for explicit approval before running this (see Global Constraints).

```bash
git add custom_components/zaptec/services.py tests/test_services.py
git commit -m "refactor: let iter_objects target a tuple of zaptec object types"
```

---

### Task 2: Add `RAW_API_REQUEST_SCHEMA`

**Files:**
- Modify: `custom_components/zaptec/services.py` (add the schema after `SEND_COMMAND_SCHEMA`, before the `_get_as_set` helper)
- Test: `tests/test_services.py`

**Interfaces:**
- Consumes: `vol`, `cv` (already imported in `services.py`).
- Produces: `RAW_API_REQUEST_SCHEMA: vol.Schema` — a service-call schema requiring one of `charger_id`/`installation_id`/`device_id`/`entity_id`, plus required `path: str` and `method` (coerced upper-case, restricted to `GET`/`POST`/`PUT`), plus optional `data: dict`. Task 3 registers this schema against the new service.

- [ ] **Step 1: Write the failing tests**

First, add `RAW_API_REQUEST_SCHEMA` to the existing import block in `tests/test_services.py`:

```python
from custom_components.zaptec.services import (
    CHARGER_ID_SCHEMA,
    LIMIT_CURRENT_SCHEMA,
    RAW_API_REQUEST_SCHEMA,
    SEND_COMMAND_SCHEMA,
    async_setup_services,
)
```

Then add these tests, directly after `test_send_command_schema_requires_command` (i.e. still inside the "Schema validation" section, before the `# services.yaml consistency` comment header):

```python
def test_raw_api_request_schema_requires_path_and_method() -> None:
    """RAW_API_REQUEST_SCHEMA rejects data missing path or method."""
    with pytest.raises(vol.Invalid, match="required key not provided"):
        RAW_API_REQUEST_SCHEMA({"charger_id": "x", "method": "GET"})
    with pytest.raises(vol.Invalid, match="required key not provided"):
        RAW_API_REQUEST_SCHEMA({"charger_id": "x", "path": "chargers"})


def test_raw_api_request_schema_requires_a_target() -> None:
    """RAW_API_REQUEST_SCHEMA rejects data with none of the id fields."""
    with pytest.raises(vol.Invalid, match="At least one of"):
        RAW_API_REQUEST_SCHEMA({"path": "chargers", "method": "GET"})


def test_raw_api_request_schema_normalizes_method_case() -> None:
    """RAW_API_REQUEST_SCHEMA upper-cases a lowercase method."""
    result = RAW_API_REQUEST_SCHEMA({"charger_id": "x", "path": "chargers", "method": "get"})
    assert result["method"] == "GET"


def test_raw_api_request_schema_rejects_delete() -> None:
    """RAW_API_REQUEST_SCHEMA rejects DELETE (and anything outside GET/POST/PUT)."""
    with pytest.raises(vol.Invalid, match="value must be one of"):
        RAW_API_REQUEST_SCHEMA({"charger_id": "x", "path": "chargers", "method": "DELETE"})


def test_raw_api_request_schema_accepts_optional_data() -> None:
    """RAW_API_REQUEST_SCHEMA accepts an arbitrary data payload."""
    result = RAW_API_REQUEST_SCHEMA(
        {
            "installation_id": "x",
            "path": "installation/{id}/update",
            "method": "put",
            "data": {"EnabledFeatures": 12},
        }
    )
    assert result["data"] == {"EnabledFeatures": 12}
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_services.py -k raw_api_request_schema -v`

Expected: FAIL with `ImportError: cannot import name 'RAW_API_REQUEST_SCHEMA'` (it doesn't exist yet).

- [ ] **Step 3: Add the schema**

In `custom_components/zaptec/services.py`, add directly after the `SEND_COMMAND_SCHEMA` definition (before `def _get_as_set`):

```python
RAW_API_REQUEST_SCHEMA = vol.Schema(
    vol.All(
        vol.Schema(
            {
                vol.Required(
                    vol.Any("charger_id", "installation_id", "device_id", "entity_id"),
                    msg=(
                        "At least one of 'charger_id', 'installation_id', "
                        "'device_id' or 'entity_id' must be specified"
                    ),
                ): object,
            },
            extra=vol.ALLOW_EXTRA,
        ),
        vol.Schema(
            {
                vol.Optional("charger_id"): str,
                vol.Optional("installation_id"): str,
                vol.Optional("device_id"): vol.All(cv.ensure_list, [str]),
                vol.Optional("entity_id"): vol.All(cv.ensure_list, [str]),
                vol.Required("path"): str,
                vol.Required("method"): vol.All(vol.Upper, vol.In(["GET", "POST", "PUT"])),
                vol.Optional("data"): dict,
            }
        ),
    )
)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_services.py -k raw_api_request_schema -v`

Expected: PASS, all 5 tests.

- [ ] **Step 5: Commit**

Ask the user for explicit approval before running this.

```bash
git add custom_components/zaptec/services.py tests/test_services.py
git commit -m "feat: add RAW_API_REQUEST_SCHEMA for the upcoming raw_api_request service"
```

---

### Task 3: Add the `raw_api_request` handler and register the service

**Files:**
- Modify: `custom_components/zaptec/services.py` (imports, `TServiceHandler` alias, new handler function, `async_setup_services`)
- Test: `tests/test_services.py`

**Interfaces:**
- Consumes: `iter_objects(service_call, mustbe=(Charger, Installation))` from Task 1; `RAW_API_REQUEST_SCHEMA` from Task 2; `obj.zaptec.request(url, *, method, data)` (existing, from `custom_components/zaptec/zaptec/api.py`); `obj.id`, `obj.zaptec` (existing attributes on `Charger`/`Installation`/`ZaptecBase`).
- Produces: `service_handle_raw_api_request(service_call: ServiceCall) -> ServiceResponse`, registered under `hass.services.async_register(DOMAIN, "raw_api_request", ..., supports_response=SupportsResponse.OPTIONAL)`. Response shape: `{"results": [{"target": str, "path": str, "result": Any}, ...]}`.

- [ ] **Step 1: Write the failing tests**

Add a small helper and the new tests to `tests/test_services.py`, directly after `test_send_command_wraps_failure` (i.e. still inside the "Individual service handlers" section, before the `# Schema validation` comment header):

```python
def _attach_zaptec_client(obj: MagicMock, request_return: Any = None) -> AsyncMock:
    """Attach a mock .zaptec.request() to a charger/installation mock; return the AsyncMock."""
    obj.zaptec = MagicMock()
    obj.zaptec.request = AsyncMock(return_value=request_return)
    return obj.zaptec.request


async def test_raw_api_request_substitutes_id_and_returns_result(
    hass: MagicMock, manager: MagicMock, add_installation: Any, handlers: dict[str, Any]
) -> None:
    """{id} in path is replaced with the resolved object's id; the API result is returned."""
    installation, coordinator = add_installation("install1")
    request = _attach_zaptec_client(installation, request_return={"Id": "install1"})

    result = await handlers["raw_api_request"](
        make_call(
            hass,
            {
                "installation_id": "install1",
                "path": "installation/{id}/update",
                "method": "PUT",
                "data": {"EnabledFeatures": 12},
            },
        )
    )

    request.assert_awaited_once_with(
        "installation/install1/update", method="put", data={"EnabledFeatures": 12}
    )
    assert result == {
        "results": [
            {
                "target": "install1",
                "path": "installation/install1/update",
                "result": {"Id": "install1"},
            }
        ]
    }
    coordinator.trigger_poll.assert_awaited_once()


async def test_raw_api_request_get_does_not_trigger_poll(
    hass: MagicMock, manager: MagicMock, add_charger: Any, handlers: dict[str, Any]
) -> None:
    """A GET request does not trigger a coordinator poll."""
    charger, coordinator = add_charger("charger1")
    _attach_zaptec_client(charger, request_return={"Id": "charger1"})

    await handlers["raw_api_request"](
        make_call(hass, {"charger_id": "charger1", "path": "chargers/{id}", "method": "GET"})
    )

    coordinator.trigger_poll.assert_not_awaited()


async def test_raw_api_request_decodes_bytes_result(
    hass: MagicMock, manager: MagicMock, add_charger: Any, handlers: dict[str, Any]
) -> None:
    """A raw bytes response (e.g. from a 204) is decoded to a string for the response data."""
    charger, _coordinator = add_charger("charger1")
    _attach_zaptec_client(charger, request_return=b"")

    result = await handlers["raw_api_request"](
        make_call(
            hass, {"charger_id": "charger1", "path": "chargers/{id}/update", "method": "POST"}
        )
    )

    assert result["results"][0]["result"] == ""


async def test_raw_api_request_wraps_failure(
    hass: MagicMock, manager: MagicMock, add_charger: Any, handlers: dict[str, Any]
) -> None:
    """A request failure is wrapped in HomeAssistantError and skips the poll."""
    charger, coordinator = add_charger("charger1")
    request = _attach_zaptec_client(charger)
    request.side_effect = Exception("boom")

    with pytest.raises(HomeAssistantError, match="Raw request 'GET chargers/charger1' failed"):
        await handlers["raw_api_request"](
            make_call(hass, {"charger_id": "charger1", "path": "chargers/{id}", "method": "GET"})
        )

    coordinator.trigger_poll.assert_not_awaited()


async def test_raw_api_request_targets_multiple_devices(
    hass: MagicMock,
    manager: MagicMock,
    add_charger: Any,
    add_installation: Any,
    handlers: dict[str, Any],
) -> None:
    """A single call can target both a charger and an installation; both are processed."""
    charger, _charger_coordinator = add_charger("charger1")
    installation, _install_coordinator = add_installation("install1")
    _attach_zaptec_client(charger, request_return="charger-result")
    _attach_zaptec_client(installation, request_return="installation-result")

    result = await handlers["raw_api_request"](
        make_call(
            hass,
            {
                "charger_id": "charger1",
                "installation_id": "install1",
                "path": "{id}",
                "method": "GET",
            },
        )
    )

    targets = {entry["target"]: entry["result"] for entry in result["results"]}
    assert targets == {"charger1": "charger-result", "install1": "installation-result"}


async def test_raw_api_request_logs_a_warning(
    hass: MagicMock,
    manager: MagicMock,
    add_charger: Any,
    handlers: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every call logs a warning that this hits an unsupported/undocumented surface."""
    charger, _coordinator = add_charger("charger1")
    _attach_zaptec_client(charger, request_return={})

    with caplog.at_level("WARNING"):
        await handlers["raw_api_request"](
            make_call(hass, {"charger_id": "charger1", "path": "chargers/{id}", "method": "GET"})
        )

    assert any(
        record.levelname == "WARNING" and "raw_api_request" in record.getMessage()
        for record in caplog.records
    )
```

Also update the existing registration test — it currently only expects 8 services. In `test_async_setup_services_registers_all_services`, change the docstring from `"""All eight zaptec services get registered under the zaptec domain."""` to `"""All nine zaptec services get registered under the zaptec domain."""`, and add `"raw_api_request"` to the expected set:

```python
    assert registered == {
        "stop_charging",
        "resume_charging",
        "authorize_charging",
        "deauthorize_charging",
        "restart_charger",
        "upgrade_firmware",
        "limit_current",
        "send_command",
        "raw_api_request",
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_services.py -k "raw_api_request or registers_all_services" -v`

Expected: FAIL — `handlers["raw_api_request"]` raises `KeyError` (service not registered yet), and `registers_all_services` fails because `"raw_api_request"` is missing from the actual registered set.

- [ ] **Step 3: Add the handler and register the service**

In `custom_components/zaptec/services.py`, change the `homeassistant.core` import line from:

```python
from homeassistant.core import HomeAssistant, ServiceCall
```

to:

```python
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
```

Change the `typing` import line from:

```python
from typing import TYPE_CHECKING
```

to:

```python
from typing import TYPE_CHECKING, Any
```

Widen the `TServiceHandler` alias from:

```python
TServiceHandler = Callable[[ServiceCall], Awaitable[None]]
```

to:

```python
TServiceHandler = Callable[[ServiceCall], Awaitable[ServiceResponse | None]]
```

Add the new handler directly after `service_handle_send_command` (before `async def async_setup_services`):

```python
async def service_handle_raw_api_request(service_call: ServiceCall) -> ServiceResponse:
    """Handle the raw_api_request service call."""
    path = service_call.data["path"]
    method = service_call.data["method"]
    data = service_call.data.get("data")

    _LOGGER.warning(
        "Called raw_api_request: %s %s (this targets an unsupported/undocumented "
        "API surface; the endpoint and payload are not validated by this integration)",
        method,
        path,
    )

    results: list[dict[str, Any]] = []
    for coordinator, obj in iter_objects(service_call, mustbe=(Charger, Installation)):
        resolved_path = path.replace("{id}", obj.id)
        _LOGGER.debug("  >> %s %s to %s", method, resolved_path, obj.id)
        try:
            result = await obj.zaptec.request(resolved_path, method=method.lower(), data=data)
        except Exception as exc:
            raise HomeAssistantError(
                f"Raw request '{method} {resolved_path}' failed: {exc}"
            ) from exc
        if method != "GET":
            await coordinator.trigger_poll()
        if isinstance(result, bytes):
            result = result.decode("utf-8", errors="replace")
        results.append({"target": obj.id, "path": resolved_path, "result": result})

    return {"results": results}
```

Replace the whole `async_setup_services` function with:

```python
async def async_setup_services(hass: HomeAssistant) -> None:
    """Set up services for zaptec."""
    services: list[tuple[str, vol.Schema, TServiceHandler, SupportsResponse]] = [
        (
            "stop_charging",
            CHARGER_ID_SCHEMA,
            service_handle_stop_charging,
            SupportsResponse.NONE,
        ),
        (
            "resume_charging",
            CHARGER_ID_SCHEMA,
            service_handle_resume_charging,
            SupportsResponse.NONE,
        ),
        (
            "authorize_charging",
            CHARGER_ID_SCHEMA,
            service_handle_authorize_charging,
            SupportsResponse.NONE,
        ),
        (
            "deauthorize_charging",
            CHARGER_ID_SCHEMA,
            service_handle_deauthorize_charging,
            SupportsResponse.NONE,
        ),
        (
            "restart_charger",
            CHARGER_ID_SCHEMA,
            service_handle_restart_charger,
            SupportsResponse.NONE,
        ),
        (
            "upgrade_firmware",
            CHARGER_ID_SCHEMA,
            service_handle_upgrade_firmware,
            SupportsResponse.NONE,
        ),
        (
            "limit_current",
            LIMIT_CURRENT_SCHEMA,
            service_handle_limit_current,
            SupportsResponse.NONE,
        ),
        (
            "send_command",
            SEND_COMMAND_SCHEMA,
            service_handle_send_command,
            SupportsResponse.NONE,
        ),
        (
            "raw_api_request",
            RAW_API_REQUEST_SCHEMA,
            service_handle_raw_api_request,
            SupportsResponse.OPTIONAL,
        ),
    ]

    # Register the services
    for name, schema, handler, supports_response in services:
        if not hass.services.has_service(DOMAIN, name):
            hass.services.async_register(
                DOMAIN, name, handler, schema=schema, supports_response=supports_response
            )
```

- [ ] **Step 4: Run the full test file to verify everything passes**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_services.py -v`

Expected: PASS — every test in the file, including all pre-existing ones (registration/skip tests, iter_objects resolution tests, every other handler's tests, and the schema tests from Task 2). `test_services_yaml_keys_match_registered_service_names` is expected to now FAIL (it will until Task 4 documents `raw_api_request` in `services.yaml`) — confirm that's the *only* failure before moving on.

- [ ] **Step 5: Commit**

Ask the user for explicit approval before running this.

```bash
git add custom_components/zaptec/services.py tests/test_services.py
git commit -m "feat: add zaptec.raw_api_request service"
```

---

### Task 4: Document the service in `services.yaml`

**Files:**
- Modify: `custom_components/zaptec/services.yaml`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing consumed by later tasks — but `test_services_yaml_keys_match_registered_service_names` (from Task 3, already written) depends on this file listing exactly `raw_api_request` as a top-level key.

- [ ] **Step 1: Add the `raw_api_request` entry**

Append to the end of `custom_components/zaptec/services.yaml`:

```yaml

raw_api_request:
  name: Raw API request
  description: >-
    Advanced/unsupported: only use this if you know exactly what you're
    doing. This sends a request straight to the Zaptec API through this
    integration's authenticated session, with NO validation of the
    endpoint, method, or payload — including whether the device you select
    (installation vs. charger) actually matches what the path targets. You
    are fully responsible for every setting on this call. It may hit
    undocumented API surface that can change or break without warning, and
    that Zaptec's API fair-use policy discourages relying on. See
    DEVELOPMENT.md for example calls.
  fields:
    device_id:
      description: >-
        Select charger or installation device. Must match the device type
        the chosen path targets (e.g. an installation device for
        installation/{id}/... paths) — this is not checked for you.
      selector:
        device:
          integration: zaptec
    charger_id:
      description: Charger identifier
      example: 00000000-1111-2222-3333-444444444444
    installation_id:
      description: Installation identifier
      example: 00000000-1111-2222-3333-444444444444
    path:
      description: >-
        Relative API path. A literal {id} is replaced with the resolved
        charger/installation's own id.
      example: "installation/{id}/update"
    method:
      description: HTTP method.
      example: "PUT"
      selector:
        select:
          options:
            - "GET"
            - "POST"
            - "PUT"
    data:
      description: JSON request body (optional, e.g. for POST/PUT).
      example: '{"EnabledFeatures": 12}'
```

- [ ] **Step 2: Run the full test suite to verify the yaml/registration consistency check passes**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests/test_services.py -v`

Expected: PASS — all tests, including `test_services_yaml_keys_match_registered_service_names` (the failure noted at the end of Task 3 is now resolved).

- [ ] **Step 3: Commit**

Ask the user for explicit approval before running this.

```bash
git add custom_components/zaptec/services.yaml
git commit -m "docs: document zaptec.raw_api_request in services.yaml"
```

---

### Task 5: Add usage examples to `DEVELOPMENT.md`

**Files:**
- Modify: `DEVELOPMENT.md`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing consumed by other tasks — pure documentation, referenced by the `services.yaml` description added in Task 4 ("See DEVELOPMENT.md for example calls").

- [ ] **Step 1: Append the new section**

Add to the end of `DEVELOPMENT.md` (after the existing `## SignedMeterValue` section):

```markdown

## `raw_api_request` service

`raw_api_request` is an escape hatch for hitting Zaptec API endpoints this
integration doesn't otherwise model — see [upstream issue #153](
https://github.com/custom-components/zaptec/issues/153) for the motivating
case (Eco Mode). It sends the given `path`/`method`/`data` through the
integration's authenticated session with no validation; you are responsible
for the endpoint, method, and payload being correct, including picking a
`device_id`/`charger_id`/`installation_id` whose type actually matches what
the path targets.

### Example: Eco Mode (undocumented, unverified)

```yaml
action: zaptec.raw_api_request
data:
  device_id: <installation device>
  path: "installation/{id}/update"
  method: PUT
  data:
    EnabledFeatures: 12
    Feature_PowerManagement_EcoMode_DepartureTime: 360
    Feature_PowerManagement_EcoMode_MinEnergy: 10
    Feature_PowerManagement_EcoMode_DeliveryArea: 8
```

This payload comes from the discussion on issue #153 and was never
confirmed against the live API: it's unknown whether the three
`Feature_PowerManagement_EcoMode_*` fields are required alongside
`EnabledFeatures`, or what value disables Eco Mode again.

### Example: read-only GET

```yaml
action: zaptec.raw_api_request
data:
  device_id: <installation device>
  path: "installation/{id}"
  method: GET
response_variable: raw_installation
```

`response_variable` (or `response_data` in a script/scene) is how an
automation gets the API's response back — every `raw_api_request` call
returns `{"results": [{"target": ..., "path": ..., "result": ...}, ...]}`,
one entry per resolved target.
```

- [ ] **Step 2: Proofread**

Re-read the new section in place. Confirm both fenced `yaml` code blocks are valid YAML (no tab characters, consistent indentation) and that the issue #153 link resolves to `https://github.com/custom-components/zaptec/issues/153`.

- [ ] **Step 3: Commit**

Ask the user for explicit approval before running this.

```bash
git add DEVELOPMENT.md
git commit -m "docs: add raw_api_request usage examples to DEVELOPMENT.md"
```

---

### Task 6: Final verification

**Files:** none (verification only).

**Interfaces:** none.

- [ ] **Step 1: Run ruff format check**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff format custom_components tests --diff`

Expected: no diff output (clean). If there's a diff, run without `--diff` to apply formatting, then re-stage and amend the relevant task's changes (ask before committing).

- [ ] **Step 2: Run the CI-gating ruff check**

Run: `"C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m ruff check --exclude custom_components/zaptec/zaptec/api.py`

Expected: no new errors introduced by this plan's changes. (Pre-existing errors elsewhere in the repo, if any outside `api.py`, are out of scope — only fix things this plan's diff touches.)

- [ ] **Step 3: Run the full test suite**

Run: `SKIP_ZAPTEC_API_TEST=true "C:/Users/rhamm/anaconda3/envs/py314/python.exe" -m pytest tests -q`

Expected: all tests pass, including `tests/test_services.py` in full and everything else unaffected by this change.

- [ ] **Step 4: Report status**

Summarize to the user: lint/format clean, full test suite passing, all 6 tasks committed (list the commit messages). No further action needed from this plan — ready for the user to decide on PR/merge via `superpowers:finishing-a-development-branch` if they want it.

---

## Self-Review Notes

- **Spec coverage:** schema (Task 2) ✅, `iter_objects` tuple generalization (Task 1) ✅, `{id}` substitution + `SupportsResponse.OPTIONAL` + coordinator poll on non-GET + bytes-decoding (Task 3) ✅, `services.yaml` warning text (Task 4) ✅, `DEVELOPMENT.md` examples (Task 5) ✅, lint/test gate (Task 6) ✅. No spec section without a task.
- **Placeholder scan:** no TBD/TODO; every step has literal code or exact commands.
- **Type consistency:** `service_handle_raw_api_request`, `RAW_API_REQUEST_SCHEMA`, `iter_objects(..., mustbe=(Charger, Installation))` are named identically everywhere they're referenced across Tasks 1, 2, 3, and the response shape (`{"results": [{"target", "path", "result"}]}`) matches between the handler code (Task 3 Step 3) and the tests that assert against it (Task 3 Step 1).
