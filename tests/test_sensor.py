"""Behavior tests for the authorized-by sensors, driven through the real harness."""

from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zaptec.const import DOMAIN
from custom_components.zaptec.zaptec import MISSING
from tests.conftest import setup_integration

CHARGER_ID = "chg-mock-1"


def _seed(mock_zaptec: MagicMock, extra: dict[str, Any]) -> None:
    """Back the mock charger's `.get()` with the fixture data plus `extra`."""
    data = {
        "id": CHARGER_ID,
        "name": "Mock Charger",
        "operating_mode": "Connected",
        "charger_operation_mode": "Connected",
        **extra,
    }
    mock_zaptec.chargers[0].get.side_effect = lambda key, default=MISSING: data.get(key, default)


def _entity_id(hass: HomeAssistant, key: str) -> str:
    """Return the entity_id of the charger sensor for `key`."""
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{CHARGER_ID}_{key}")
    assert entity_id is not None
    return entity_id


async def test_authorized_by_shows_raw_token(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """An RFID card token is shown as-is."""
    _seed(mock_zaptec, {"charger_current_user_uuid": "nfc-1234"})

    await setup_integration(hass, mock_config_entry, mock_zaptec)

    state = hass.states.get(_entity_id(hass, "charger_current_user_uuid"))
    assert state.state == "nfc-1234"
    assert state.attributes["id"] == "nfc-1234"


async def test_authorized_by_is_unknown_when_no_session(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """The cleared value Zaptec sends between sessions reads as unknown."""
    _seed(mock_zaptec, {"charger_current_user_uuid": ""})

    await setup_integration(hass, mock_config_entry, mock_zaptec)

    state = hass.states.get(_entity_id(hass, "charger_current_user_uuid"))
    assert state.state == "unknown"
    assert state.attributes["id"] is None


async def test_authorized_by_relabels_remote_token(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """A "ble-" token, which Zaptec uses for remote authorization, is relabeled."""
    _seed(mock_zaptec, {"charger_current_user_uuid": "ble-07c3031f-4b08-45d4-a1c8-f7ed0ee92ee5"})

    await setup_integration(hass, mock_config_entry, mock_zaptec)

    state = hass.states.get(_entity_id(hass, "charger_current_user_uuid"))
    assert state.state == "HA/Zaptec App"
    assert state.attributes["id"] == "ble-07c3031f-4b08-45d4-a1c8-f7ed0ee92ee5"


async def test_last_session_authorized_by_reads_nested_key(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """The completed-session sensor reads AuthenticationCode out of the blob."""
    _seed(mock_zaptec, {"completed_session": {"AuthenticationCode": "nfc-c35"}})

    await setup_integration(hass, mock_config_entry, mock_zaptec)

    state = hass.states.get(_entity_id(hass, "completed_session.AuthenticationCode"))
    assert state.state == "nfc-c35"


async def test_last_session_authorized_by_relabels_remote_token(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """The same prefix rule applies to the completed-session key."""
    _seed(mock_zaptec, {"completed_session": {"AuthenticationCode": "ble-07c3031f"}})

    await setup_integration(hass, mock_config_entry, mock_zaptec)

    state = hass.states.get(_entity_id(hass, "completed_session.AuthenticationCode"))
    assert state.state == "HA/Zaptec App"
    assert state.attributes["id"] == "ble-07c3031f"


async def test_last_session_authorized_by_is_unknown_when_empty(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """Zaptec leaves AuthenticationCode empty for remotely authorized sessions."""
    _seed(mock_zaptec, {"completed_session": {"AuthenticationCode": ""}})

    await setup_integration(hass, mock_config_entry, mock_zaptec)

    state = hass.states.get(_entity_id(hass, "completed_session.AuthenticationCode"))
    assert state.state == "unknown"
    assert state.attributes["id"] is None
