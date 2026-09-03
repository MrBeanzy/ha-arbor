"""Balance sensors - one per Arbor account discovered on the dashboard."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    known: dict[str, ArborBalanceSensor] = {}

    def _sync() -> None:
        new = []
        for aid, acc in (coordinator.data or {}).items():
            if aid not in known:
                sensor = ArborBalanceSensor(coordinator, aid, acc.get("name") or f"Account {aid}")
                known[aid] = sensor
                new.append(sensor)
        if new:
            async_add_entities(new)

    _sync()
    entry.async_on_unload(coordinator.async_add_listener(_sync))


class ArborBalanceSensor(CoordinatorEntity, SensorEntity):
    """A single Arbor account balance."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_native_unit_of_measurement = "GBP"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_icon = "mdi:silverware-fork-knife"

    def __init__(self, coordinator, account_id: str, name: str) -> None:
        super().__init__(coordinator)
        self._aid = account_id
        self._attr_unique_id = f"arbor_{account_id}"
        self._attr_name = name
        self._attr_device_info = {
            "identifiers": {(DOMAIN, "arbor_accounts")},
            "name": "Arbor",
            "manufacturer": "Arbor",
        }

    @property
    def native_value(self):
        acc = (self.coordinator.data or {}).get(self._aid)
        return acc.get("balance") if acc else None

    @property
    def extra_state_attributes(self) -> dict:
        acc = (self.coordinator.data or {}).get(self._aid) or {}
        return {"account_id": self._aid, "account": acc.get("name")}
