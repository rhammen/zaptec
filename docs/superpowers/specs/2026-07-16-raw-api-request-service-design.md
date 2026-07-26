# Design: `raw_api_request` service — unsupported/undocumented API escape hatch

## Problem

Upstream issue [custom-components/zaptec#153](https://github.com/custom-components/zaptec/issues/153)
requests Eco Mode (smart charging) support. The maintainer is inclined to
close it as won't-fix: Eco Mode is only reachable through an undocumented
`PUT /api/installation/{id}` payload (`EnabledFeatures`,
`Feature_PowerManagement_EcoMode_*`), and building it into the integration
means reverse-engineering and maintaining support for API surface Zaptec's
[fair-use policy](https://docs.zaptec.com/docs/api-fair-use-policy#/)
explicitly discourages.

Rather than implement Eco Mode (or any other undocumented feature) directly,
this fork will offer a generic escape hatch: a service that sends a
user-specified HTTP request through the integration's already-authenticated
session, with no validation of the endpoint or payload. Responsibility for
what gets sent sits entirely with the user; the integration only supplies
the authenticated transport.

## Non-goals

- No modeling of Eco Mode or any other specific undocumented feature.
- No endpoint allowlist/denylist, payload schema, or syntax safeguards
  beyond what's listed below. Malformed `path`/`data` is the user's problem.
- No new HA entities. This is a service only.

## Service: `zaptec.raw_api_request`

Registered in `services.py` alongside the existing services, following the
same `(name, schema, handler)` tuple pattern in `async_setup_services`.

### Schema

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

- `method` is restricted to `GET`/`POST`/`PUT` (matches the methods Zaptec's
  API actually uses for reads/`*/update`/the Eco Mode `PUT`). `DELETE` is
  deliberately excluded so this can't be used to delete a charger or
  installation and trigger integration-side cleanup.
- `data` has no shape validation — passed through verbatim as the JSON body.
- `path` has no shape validation either (no leading-slash normalization, no
  check that it isn't a full URL). If the user passes something malformed,
  the request fails or does the wrong thing; that's on them.

### Target resolution

`path` may contain a literal `{id}` placeholder, replaced via plain
`path.replace("{id}", obj.id)` with the resolved target's own id — no
templating engine, no error if `{id}` is absent or malformed.

`iter_objects()` currently takes a single `mustbe: type[T]` (`Charger` or
`Installation`) to decide which legacy id-field (`charger_id` vs.
`installation_id`) to also honor. Generalize it to accept
`mustbe: type[T] | tuple[type, ...]`:

```python
def iter_objects[T](
    service_call: ServiceCall, mustbe: type[T] | tuple[type, ...]
) -> Generator[tuple[ZaptecUpdateCoordinator, T]]:
    ...
    types = mustbe if isinstance(mustbe, tuple) else (mustbe,)
    field_by_type = {Charger: "charger_id", Installation: "installation_id"}
    for t in types:
        if field := field_by_type.get(t):
            uids.update(_get_as_set(service_call, field))
    ...
    if not isinstance(zaptec_object, mustbe):  # isinstance already accepts a tuple
        ...
```

`raw_api_request` calls `iter_objects(service_call, mustbe=(Charger, Installation))`
so it can target either object type through the one code path. Existing
callers (`mustbe=Charger` / `mustbe=Installation`) are unaffected — a single
type still works exactly as today.

### Handler

```python
async def service_handle_raw_api_request(service_call: ServiceCall) -> ServiceResponse:
    """Handle the raw_api_request service call."""
    path = service_call.data["path"]
    method = service_call.data["method"]
    data = service_call.data.get("data")

    _LOGGER.warning(
        "Called raw_api_request: %s %s (this targets an unsupported/"
        "undocumented API surface; the integration does not validate "
        "the endpoint or payload)",
        method,
        path,
    )

    results = []
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

Registered with `supports_response=SupportsResponse.OPTIONAL` (new import:
`homeassistant.core.SupportsResponse`, `ServiceResponse`). `services` in
`async_setup_services` becomes a 4-tuple
`(name, schema, handler, supports_response)`; every existing entry gets
`SupportsResponse.NONE` appended (no behavior change for them), and
`raw_api_request` is the only one passing `SupportsResponse.OPTIONAL`.
Non-`GET` calls trigger a coordinator poll afterward, matching
every other mutating service (`stop_charging`, `limit_current`, etc.), so
the resulting state change (if any) shows up in HA promptly.

`obj.zaptec.request()` already provides auth (bearer token, refresh-on-401)
and retry/error handling identical to every typed API call — this service
gets that transport behavior for free, it just skips response validation
(`validate.py` already no-ops with a warning log for URLs it doesn't
recognize, so this doesn't need any change there).

### Documentation (`services.yaml`)

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

### Documentation (`DEVELOPMENT.md`)

Add a new section alongside the existing API-quirks notes (two-step
resume-charging flow, `DeAuthorizeAndStop`'s 500, OCMF format, etc.) with
two worked examples, both usable directly as an automation `action:` block:

1. **Eco Mode** — the motivating case from
   [issue #153](https://github.com/custom-components/zaptec/issues/153),
   showing the (unconfirmed/undocumented) payload from that thread:

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

   Noted inline as unverified — nobody in the upstream thread confirmed
   whether the three `Feature_PowerManagement_EcoMode_*` fields are
   required alongside `EnabledFeatures`, or what value disables it.

2. **Read-only GET**, to show the simplest possible call and that
   `response_variable` is how you get the result back in an automation:

   ```yaml
   action: zaptec.raw_api_request
   data:
     device_id: <installation device>
     path: "installation/{id}"
     method: GET
   response_variable: raw_installation
   ```

## Tests (`tests/test_services.py`)

Follow the existing per-service groups (see `send_command`'s tests as the
closest analog):

- Schema: `path` and `method` required; `method` rejects anything outside
  `GET`/`POST`/`PUT` (case-insensitive input normalized to upper); target
  (`charger_id`/`installation_id`/`device_id`/`entity_id`) required — reuse
  `test_no_ids_specified_raises_with_missing_field`-style coverage.
- `{id}` substitution: path containing `{id}` gets the resolved object's id
  spliced in before being passed to `obj.zaptec.request`.
- Targets either a `Charger` or an `Installation` mock (covers the
  `iter_objects` generalization to a tuple `mustbe`).
- Non-GET call triggers `coordinator.trigger_poll()`; GET does not.
- Failure from `obj.zaptec.request` wraps in `HomeAssistantError`.
- Multiple resolved targets (list `device_id`) each produce one entry in
  the returned `results` list.
- `test_services_yaml_keys_match_registered_service_names` picks this up
  automatically once `raw_api_request` is added to both files.

## Out of scope

- `iter_objects`'s generalization to a tuple `mustbe` is the only change to
  existing service code; no other service's behavior changes.
- No changes to `validate.py`, `zconst.py`, or entity platforms.
