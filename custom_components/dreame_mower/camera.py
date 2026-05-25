# mypy: ignore-errors
from __future__ import annotations

import asyncio
import collections
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final

from homeassistant.components.camera import (
    Camera,
    CameraEntityDescription,
    ENTITY_ID_FORMAT,
    TOKEN_CHANGE_INTERVAL,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, LOGGER
from .coordinator import DreameMowerDataUpdateCoordinator
from .entity import DreameMowerEntity, DreameMowerEntityDescription
from .dreame.map_app import fetch_app_map_png

PARALLEL_UPDATES = 0

_APP_MAP_CACHE_TTL: Final = timedelta(seconds=60)
DREAME_TOKEN_CHANGE_INTERVAL: Final = timedelta(minutes=60)
PNG_CONTENT_TYPE: Final = "image/png"
MAP_IMAGE_URL: Final = "/api/camera_proxy/{0}?token={1}&v={2}"
_TOKEN_CHANGE_RATIO: Final = int(
    DREAME_TOKEN_CHANGE_INTERVAL.total_seconds() / TOKEN_CHANGE_INTERVAL.total_seconds()
)


@dataclass
class _AppMapCache:
    """TTL cache for the app-action map path."""

    image: bytes | None = None
    refreshed_at: datetime | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def is_fresh(self) -> bool:
        return (
            self.image is not None
            and self.refreshed_at is not None
            and (datetime.now(UTC) - self.refreshed_at) <= _APP_MAP_CACHE_TTL
        )

    def store(self, image: bytes) -> None:
        self.image = image
        self.refreshed_at = datetime.now(UTC)


@dataclass
class DreameMowerCameraEntityDescription(
    DreameMowerEntityDescription, CameraEntityDescription
):
    """Describes Dreame Mower Camera entity."""


CAMERAS: tuple[CameraEntityDescription, ...] = (
    DreameMowerCameraEntityDescription(key="map", icon="mdi:map"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Dreame Mower Camera based on a config entry."""
    coordinator: DreameMowerDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        DreameMowerCameraEntity(coordinator, description) for description in CAMERAS
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return True


class DreameMowerCameraEntity(DreameMowerEntity, Camera):
    """Dreame Mower camera — renders map via app-action protocol."""

    def __init__(
        self,
        coordinator: DreameMowerDataUpdateCoordinator,
        description: DreameMowerCameraEntityDescription,
    ) -> None:
        self._access_token_update_counter = 0
        self.access_tokens = collections.deque([], 2)
        Camera.__init__(self)
        super().__init__(coordinator, description)
        self._generate_entity_id(ENTITY_ID_FORMAT)
        self.content_type = PNG_CONTENT_TYPE
        self.stream = None
        self.async_update_token()
        self._rtsp_to_webrtc = False
        self._attr_is_streaming = True
        self._app_map_cache = _AppMapCache()
        self._state = STATE_UNAVAILABLE
        self._attr_unique_id = f"{self.device.mac}_map_{description.key}"
        self._attr_name = "Map"

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.device.available and self._state == STATE_UNAVAILABLE:
            self._state = datetime.now()
            self.async_write_ha_state()
        elif not self.device.available and self._state != STATE_UNAVAILABLE:
            self._state = STATE_UNAVAILABLE
            self.async_write_ha_state()

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        image = await self._async_get_app_map_image()
        return image if image is not None else self._app_map_cache.image

    async def _async_get_app_map_image(self) -> bytes | None:
        if self._app_map_cache.is_fresh():
            return self._app_map_cache.image
        async with self._app_map_cache._lock:
            if self._app_map_cache.is_fresh():
                return self._app_map_cache.image
            try:
                image = await self.hass.async_add_executor_job(
                    fetch_app_map_png, self.device.call_app_action
                )
                self._app_map_cache.store(image)
                if self._state == STATE_UNAVAILABLE:
                    self._state = datetime.now()
                    self.async_write_ha_state()
                return image
            except Exception as err:
                LOGGER.debug("App-action map fetch failed: %s", err)
                return None

    @callback
    def async_update_token(self) -> None:
        if self._access_token_update_counter:
            self._access_token_update_counter += 1
        if (
            not self._access_token_update_counter
            or self._access_token_update_counter > _TOKEN_CHANGE_RATIO
        ):
            self._access_token_update_counter = 1
            super().async_update_token()

    @property
    def frame_interval(self) -> float:
        return 0.25

    @property
    def state(self) -> str:
        return self._state

    @property
    def available(self) -> bool:
        return True

    @property
    def entity_picture(self) -> str:
        return MAP_IMAGE_URL.format(self.entity_id, self.access_tokens[-1], 0)
