"""Tests for custom_components.zaptec.__init__."""

from http import HTTPStatus
from unittest.mock import MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zaptec import _config_entry_error
from custom_components.zaptec.const import DOMAIN
from custom_components.zaptec.manager import ZaptecManager
from custom_components.zaptec.zaptec.exceptions import (
    AuthenticationError,
    RequestConnectionError,
    RequestError,
    RequestTimeoutError,
)
from tests.conftest import setup_integration


@pytest.mark.parametrize(
    ("err", "expected"),
    [
        # Bad credentials are non-recoverable -> re-auth flow.
        (AuthenticationError("bad"), ConfigEntryAuthFailed),
        # Connection/timeout are recoverable -> HA retries setup.
        (RequestTimeoutError("slow"), ConfigEntryNotReady),
        (RequestConnectionError("down"), ConfigEntryNotReady),
        # Transient server statuses are recoverable -> HA retries setup.
        (RequestError("unavailable", HTTPStatus.SERVICE_UNAVAILABLE), ConfigEntryNotReady),
        (RequestError("too many", HTTPStatus.TOO_MANY_REQUESTS), ConfigEntryNotReady),
        # Other HTTP errors stay permanent.
        (RequestError("forbidden", HTTPStatus.FORBIDDEN), ConfigEntryError),
        (RequestError("not found", HTTPStatus.NOT_FOUND), ConfigEntryError),
    ],
)
def test_config_entry_error_mapping(err: Exception, expected: type[Exception]) -> None:
    """Setup login errors map to the right Home Assistant config-entry error."""
    assert isinstance(_config_entry_error(err), expected)


async def test_setup_entry_creates_manager_and_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
) -> None:
    """A full setup wires up the manager and registers at least one entity."""
    manager = await setup_integration(hass, mock_config_entry, mock_zaptec)

    assert isinstance(manager, ZaptecManager)
    assert mock_config_entry.runtime_data is manager
    # Matches because mock_zaptec seeds "Mock Charger"/"Mock Home", which HA
    # slugifies into "mock..." entity_ids. Update if that seed naming changes.
    states = [s for s in hass.states.async_all() if s.entity_id.split(".")[1].startswith("mock")]
    assert states, "expected at least one zaptec entity to be created"


async def test_setup_without_devices_shows_username_only_in_ui(
    hass: HomeAssistant,
    mock_zaptec: MagicMock,
    enable_custom_integrations: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No devices fails setup, naming the account in the UI but not in the log."""
    mock_zaptec.__iter__.side_effect = lambda: iter(())
    mock_zaptec.installations = []
    mock_zaptec.chargers = []
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_USERNAME: "old@example.com", CONF_PASSWORD: "pass"}
    )
    entry.add_to_hass(hass)

    with patch("custom_components.zaptec.Zaptec", return_value=mock_zaptec):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert entry.error_reason_translation_key == "no_devices_found"
    assert entry.error_reason_translation_placeholders == {"username": "old@example.com"}
    assert "No Zaptec installations or chargers found" in caplog.text
    assert "old@example.com" not in caplog.text
