"""Fixtures for the Dreame/Mova mower tests: a protocol stand-in for the config flow."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

HOST = "192.168.1.60"
TOKEN = "0123456789abcdef0123456789abcdef"
MAC = "AA:BB:CC:DD:EE:FF"
MODEL = "mova.mower.g2405a"


def cloud_device(name: str = "Lawn", did: str = "1001", mac: str = MAC) -> dict:
    """One record of the Dreame/Mova cloud device list."""
    return {
        "did": did,
        "mac": mac,
        "model": MODEL,
        "bindDomain": "10000.mt.eu.iot.dreame.tech:19973",
        "customName": name,
        "deviceInfo": {"displayName": "Mova Mower"},
    }


@pytest.fixture
def protocol() -> Generator[MagicMock]:
    """Replace DreameMowerProtocol in the config flow; one instance serves all calls."""
    mock = MagicMock()
    mock.connect.return_value = {"mac": MAC, "model": MODEL}
    mock.cloud.logged_in = True
    mock.cloud.get_devices.return_value = {"page": {"records": [cloud_device()]}}
    with patch(
        "custom_components.dreame_mower.config_flow.DreameMowerProtocol",
        return_value=mock,
    ):
        yield mock
