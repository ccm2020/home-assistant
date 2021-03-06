"""The Search integration."""
from collections import defaultdict

import voluptuous as vol

from homeassistant.components import group, websocket_api
from homeassistant.components.homeassistant import scene
from homeassistant.core import HomeAssistant, callback, split_entity_id
from homeassistant.helpers import device_registry, entity_registry

DOMAIN = "search"


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Search component."""
    websocket_api.async_register_command(hass, websocket_search_related)
    return True


@websocket_api.async_response
@websocket_api.websocket_command(
    {
        vol.Required("type"): "search/related",
        vol.Required("item_type"): vol.In(
            (
                "area",
                "automation",
                "config_entry",
                "device",
                "entity",
                "group",
                "scene",
                "script",
            )
        ),
        vol.Required("item_id"): str,
    }
)
async def websocket_search_related(hass, connection, msg):
    """Handle search."""
    searcher = Searcher(
        hass,
        await device_registry.async_get_registry(hass),
        await entity_registry.async_get_registry(hass),
    )
    connection.send_result(
        msg["id"], searcher.async_search(msg["item_type"], msg["item_id"])
    )


class Searcher:
    """Find related things.

    Few rules:
    Scenes, scripts, automations and config entries will only be expanded if they are
    the entry point. They won't be expanded if we process them. This is because they
    turn the results into garbage.
    """

    # These types won't be further explored. Config entries + Output types.
    DONT_RESOLVE = {"scene", "automation", "script", "group", "config_entry", "area"}

    def __init__(
        self,
        hass: HomeAssistant,
        device_reg: device_registry.DeviceRegistry,
        entity_reg: entity_registry.EntityRegistry,
    ):
        """Search results."""
        self.hass = hass
        self._device_reg = device_reg
        self._entity_reg = entity_reg
        self.results = defaultdict(set)
        self._to_resolve = set()

    @callback
    def async_search(self, item_type, item_id):
        """Find results."""
        self.results[item_type].add(item_id)
        self._to_resolve.add((item_type, item_id))

        while self._to_resolve:
            search_type, search_id = self._to_resolve.pop()
            getattr(self, f"_resolve_{search_type}")(search_id)

        # Clean up entity_id items, from the general "entity" type result,
        # that are also found in the specific entity domain type.
        self.results["entity"] -= self.results["script"]
        self.results["entity"] -= self.results["scene"]
        self.results["entity"] -= self.results["automation"]
        self.results["entity"] -= self.results["group"]

        # Remove entry into graph from search results.
        self.results[item_type].remove(item_id)

        # Filter out empty sets.
        return {key: val for key, val in self.results.items() if val}

    @callback
    def _add_or_resolve(self, item_type, item_id):
        """Add an item to explore."""
        if item_id in self.results[item_type]:
            return

        self.results[item_type].add(item_id)

        if item_type not in self.DONT_RESOLVE:
            self._to_resolve.add((item_type, item_id))

    @callback
    def _resolve_area(self, area_id) -> None:
        """Resolve an area."""
        for device in device_registry.async_entries_for_area(self._device_reg, area_id):
            self._add_or_resolve("device", device.id)

    @callback
    def _resolve_device(self, device_id) -> None:
        """Resolve a device."""
        device_entry = self._device_reg.async_get(device_id)
        # Unlikely entry doesn't exist, but let's guard for bad data.
        if device_entry is not None:
            if device_entry.area_id:
                self._add_or_resolve("area", device_entry.area_id)

            for config_entry_id in device_entry.config_entries:
                self._add_or_resolve("config_entry", config_entry_id)

            # We do not resolve device_entry.via_device_id because that
            # device is not related data-wise inside HA.

        for entity_entry in entity_registry.async_entries_for_device(
            self._entity_reg, device_id
        ):
            self._add_or_resolve("entity", entity_entry.entity_id)

        # Extra: Find automations that reference this device

    @callback
    def _resolve_entity(self, entity_id) -> None:
        """Resolve an entity."""
        # Extra: Find automations and scripts that reference this entity.

        for entity in scene.scenes_with_entity(self.hass, entity_id):
            self._add_or_resolve("entity", entity)

        for entity in group.groups_with_entity(self.hass, entity_id):
            self._add_or_resolve("entity", entity)

        # Find devices
        entity_entry = self._entity_reg.async_get(entity_id)
        if entity_entry is not None:
            if entity_entry.device_id:
                self._add_or_resolve("device", entity_entry.device_id)

            if entity_entry.config_entry_id is not None:
                self._add_or_resolve("config_entry", entity_entry.config_entry_id)

        domain = split_entity_id(entity_id)[0]

        if domain in ("scene", "automation", "script", "group"):
            self._add_or_resolve(domain, entity_id)

    @callback
    def _resolve_automation(self, automation_entity_id) -> None:
        """Resolve an automation.

        Will only be called if automation is an entry point.
        """
        # Extra: Check with automation integration what entities/devices they reference

    @callback
    def _resolve_script(self, script_entity_id) -> None:
        """Resolve a script.

        Will only be called if script is an entry point.
        """
        # Extra: Check with script integration what entities/devices they reference

    @callback
    def _resolve_group(self, group_entity_id) -> None:
        """Resolve a group.

        Will only be called if group is an entry point.
        """
        for entity_id in group.get_entity_ids(self.hass, group_entity_id):
            self._add_or_resolve("entity", entity_id)

    @callback
    def _resolve_scene(self, scene_entity_id) -> None:
        """Resolve a scene.

        Will only be called if scene is an entry point.
        """
        for entity in scene.entities_in_scene(self.hass, scene_entity_id):
            self._add_or_resolve("entity", entity)

    @callback
    def _resolve_config_entry(self, config_entry_id) -> None:
        """Resolve a config entry.

        Will only be called if config entry is an entry point.
        """
        for device_entry in device_registry.async_entries_for_config_entry(
            self._device_reg, config_entry_id
        ):
            self._add_or_resolve("device", device_entry.id)

        for entity_entry in entity_registry.async_entries_for_config_entry(
            self._entity_reg, config_entry_id
        ):
            self._add_or_resolve("entity", entity_entry.entity_id)
