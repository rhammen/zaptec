"""Switch platform for Zaptec."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import ZaptecBaseEntity
from .manager import ZaptecConfigEntry, ZaptecEntityDescription
from .zaptec import Charger, Installation

_LOGGER = logging.getLogger(__name__)


class ZaptecSwitch(ZaptecBaseEntity, SwitchEntity):
    """Base class for Zaptec switches."""

    # What to log on entity update
    _log_attribute = "_attr_is_on"

    @callback
    def _update_from_zaptec(self) -> None:
        """Update the entity from Zaptec data."""
        # Called from ZaptecBaseEntity._handle_coordinator_update()
        self._attr_is_on = self._get_zaptec_value()
        self._attr_available = True


class ZaptecChargeSwitch(ZaptecSwitch):
    """Zaptec charge switch entity."""

    zaptec_obj: Charger
    _log_attribute = "_attr_is_on"

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        command = "stop_charging_final" if self._attr_is_on else "resume_charging"
        return super().available and self.zaptec_obj.is_command_valid(command)

    @callback
    def _update_from_zaptec(self) -> None:
        """Update the entity from Zaptec data."""
        # Called from ZaptecBaseEntity._handle_coordinator_update()
        state = self._get_zaptec_value()
        self._attr_is_on = state == "Connected_Charging"
        self._attr_available = True

    async def async_turn_on(self, **kwargs: Any) -> None:  # pylint: disable=unused-argument
        """Turn on the switch."""
        _LOGGER.debug(
            "Turn on %s in %s",
            self.entity_id,
            self.zaptec_obj.qual_id,
        )

        try:
            await self.zaptec_obj.command("resume_charging")
        except Exception as exc:
            raise HomeAssistantError("Resuming charging failed") from exc

        await self.trigger_poll()

    async def async_turn_off(self, **kwargs: Any) -> None:  # pylint: disable=unused-argument
        """Turn off the switch."""
        _LOGGER.debug(
            "Turn off %s in %s",
            self.entity_id,
            self.zaptec_obj.qual_id,
        )

        try:
            await self.zaptec_obj.command("stop_charging_final")
        except Exception as exc:
            raise HomeAssistantError("Stop/pausing charging failed") from exc

        await self.trigger_poll()


class ZaptecCableLockSwitch(ZaptecSwitch):
    """Zaptec cable lock entity."""

    zaptec_obj: Charger

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Unlock the cable lock."""
        _LOGGER.debug(
            "Turn off %s in %s",
            self.entity_id,
            self.zaptec_obj.qual_id,
        )

        try:
            await self.zaptec_obj.set_permanent_cable_lock(False)
        except Exception as exc:
            raise HomeAssistantError("Removing permanent cable lock failed") from exc

        await self.trigger_poll()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Lock the cable lock."""
        _LOGGER.debug(
            "Turn on %s in %s",
            self.entity_id,
            self.zaptec_obj.qual_id,
        )

        try:
            await self.zaptec_obj.set_permanent_cable_lock(True)
        except Exception as exc:
            raise HomeAssistantError("Setting permanent cable lock failed") from exc

        await self.trigger_poll()


class ZaptecChargingBlockSwitch(ZaptecSwitch, RestoreEntity):
    """Switch that blocks charging start by zeroing the installation available current.

    Turning the switch on sets the installation available current to 0 A, which
    prevents charging from starting (see README "Prevent charging auto start").
    Turning it off restores the available current to the last known non-zero
    value. Because the available current is an installation-level setting, this
    affects all chargers in the installation.
    """

    zaptec_obj: Installation

    # The installation available current is polled into the "available_current"
    # attribute; this switch's own key has no matching Zaptec attribute.
    _zaptec_key = "available_current"

    # Last known non-zero available current, restored when unblocking.
    _saved_current: float | None = None

    @callback
    def _update_from_zaptec(self) -> None:
        """Update the entity from Zaptec data."""
        # Called from ZaptecBaseEntity._handle_coordinator_update()
        value = self._get_zaptec_value(key=self._zaptec_key)
        # Remember the last known non-zero current, so we can restore it later.
        if value:
            self._saved_current = float(value)
        # The switch is "on" (blocking) when charging is prevented, i.e. 0 A.
        self._attr_is_on = float(value) == 0
        self._attr_available = True

    async def async_added_to_hass(self) -> None:
        """Restore the saved current when the entity is added."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is None:
            return
        saved = last_state.attributes.get("saved_current")
        if saved is not None:
            try:
                self._saved_current = float(saved)
            except (TypeError, ValueError):
                _LOGGER.debug("Ignoring invalid restored saved_current %r", saved)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the saved current so it survives a restart."""
        return {"saved_current": self._saved_current}

    async def async_turn_on(self, **kwargs: Any) -> None:  # pylint: disable=unused-argument
        """Block charging by setting the available current to 0 A."""
        _LOGGER.debug(
            "Turn on %s in %s",
            self.entity_id,
            self.zaptec_obj.qual_id,
        )

        # Snapshot the current available current before zeroing it, in case the
        # coordinator hasn't observed a recent non-zero value yet.
        current = self._get_zaptec_value(key=self._zaptec_key, default=None)
        if current:
            self._saved_current = float(current)

        try:
            await self.zaptec_obj.set_limit_current(availableCurrent=0)
        except Exception as exc:
            raise HomeAssistantError("Blocking charging failed") from exc

        await self.trigger_poll()

    async def async_turn_off(self, **kwargs: Any) -> None:  # pylint: disable=unused-argument
        """Allow charging by restoring the previous available current."""
        _LOGGER.debug(
            "Turn off %s in %s",
            self.entity_id,
            self.zaptec_obj.qual_id,
        )

        # Restore the last known non-zero current, falling back to the
        # installation max current (then 32 A) if none was ever seen.
        target = self._saved_current
        if not target:
            target = float(self.zaptec_obj.get("max_current", 32))

        try:
            await self.zaptec_obj.set_limit_current(availableCurrent=target)
        except Exception as exc:
            raise HomeAssistantError("Allowing charging failed") from exc

        await self.trigger_poll()


@dataclass(frozen=True, kw_only=True)
class ZapSwitchEntityDescription(ZaptecEntityDescription, SwitchEntityDescription):
    """Class describing Zaptec switch entities."""

    cls: type[SwitchEntity]


INSTALLATION_ENTITIES: list[ZaptecEntityDescription] = [
    ZapSwitchEntityDescription(
        key="prevent_charging",
        translation_key="prevent_charging",
        device_class=SwitchDeviceClass.SWITCH,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:car-off",
        cls=ZaptecChargingBlockSwitch,
    ),
]

CHARGER_ENTITIES: list[ZaptecEntityDescription] = [
    ZapSwitchEntityDescription(
        key="charger_operation_mode",
        translation_key="charger_operation_mode",
        device_class=SwitchDeviceClass.SWITCH,
        cls=ZaptecChargeSwitch,
    ),
    ZapSwitchEntityDescription(
        key="permanent_cable_lock",
        translation_key="permanent_cable_lock",
        entity_category=EntityCategory.CONFIG,
        cls=ZaptecCableLockSwitch,
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZaptecConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Zaptec switches."""
    entities = entry.runtime_data.create_entities_from_zaptec(
        INSTALLATION_ENTITIES,
        CHARGER_ENTITIES,
    )
    async_add_entities(entities, update_before_add=True)
