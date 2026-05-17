"""Binary sensor platform for MZKZG Transport — delay alerts."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, PROVIDER_LABELS

DELAY_THRESHOLD_SECONDS = 180  # 3 minutes


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up delay binary sensor from a config entry."""
    coordinators = hass.data[DOMAIN]["_coordinators"][entry.entry_id]
    entities = [MzkzgDelayBinarySensor(coordinator, entry) for coordinator in coordinators]
    async_add_entities(entities)


class MzkzgDelayBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor that turns on when any departure is significantly delayed."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize."""
        super().__init__(coordinator)
        provider = coordinator.provider
        provider_label = PROVIDER_LABELS.get(provider, provider)
        stop = coordinator.stop_id
        stop_label = coordinator.stop_name or stop
        self._attr_unique_id = f"{DOMAIN}_{provider}_{stop}_delay"
        self._attr_name = "Opóźnienie"
        self._attr_icon = "mdi:clock-alert"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, f"{provider}_{stop}")},
            "name": stop_label,
            "manufacturer": provider_label,
            "model": provider,
            "via_device": (DOMAIN, provider),
        }

    @property
    def is_on(self) -> bool | None:
        """Return True if any departure has delay >= threshold."""
        data = self.coordinator.data
        if not data or not data.get("departures"):
            return False
        return any(
            d.get("delay_seconds", 0) >= DELAY_THRESHOLD_SECONDS
            for d in data["departures"]
        )

    @property
    def extra_state_attributes(self) -> dict:
        """Return delayed departures details."""
        data = self.coordinator.data
        if not data:
            return {}
        delayed = [
            {"route": d.get("route", "?"), "headsign": d.get("headsign", "—"), "delay_minutes": round(d.get("delay_seconds", 0) / 60)}
            for d in data.get("departures", [])
            if d.get("delay_seconds", 0) >= DELAY_THRESHOLD_SECONDS
        ]
        return {"delayed_departures": delayed, "threshold_minutes": DELAY_THRESHOLD_SECONDS // 60}
