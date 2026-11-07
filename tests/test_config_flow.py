"""Config and options flow of the Dreame/Mova mower integration."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant.common import MockConfigEntry

from custom_components.dreame_mower.config_flow import LOCAL, MOVAHOME
from custom_components.dreame_mower.const import (
    CONF_ACCOUNT_TYPE,
    CONF_COUNTRY,
    CONF_DID,
    CONF_MAC,
    CONF_NOTIFY,
    CONF_TYPE,
    DOMAIN,
    NOTIFICATION,
)

from .conftest import HOST, MAC, MODEL, TOKEN, cloud_device

UNIQUE_ID = "aa:bb:cc:dd:ee:ff"
MOVA_LOGIN = {CONF_USERNAME: "user", CONF_PASSWORD: "secret", CONF_COUNTRY: "eu"}


async def _start(hass: HomeAssistant, config_type: str) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_TYPE: config_type}
    )


async def _local(hass: HomeAssistant, token: str = TOKEN) -> dict[str, Any]:
    result = await _start(hass, LOCAL)
    assert result["step_id"] == "local"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, CONF_TOKEN: token}
    )


async def test_local_flow_creates_entry(
    hass: HomeAssistant, protocol: MagicMock
) -> None:
    result = await _local(hass)
    assert result["step_id"] == "options"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Mower", CONF_NOTIFY: list(NOTIFICATION)}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Mower"
    assert result["result"].unique_id == UNIQUE_ID
    assert result["data"][CONF_HOST] == HOST
    assert result["data"][CONF_TOKEN] == TOKEN
    assert result["data"][CONF_MAC] == MAC
    assert result["data"][CONF_ACCOUNT_TYPE] == "local"
    assert result["options"][CONF_NOTIFY] == list(NOTIFICATION)


async def test_local_token_must_have_32_characters(
    hass: HomeAssistant, protocol: MagicMock
) -> None:
    result = await _local(hass, token="short")

    assert result["step_id"] == "local"
    assert result["errors"] == {"base": "wrong_token"}
    protocol.connect.assert_not_called()


async def test_local_cannot_connect(hass: HomeAssistant, protocol: MagicMock) -> None:
    protocol.connect.side_effect = OSError("timeout")

    result = await _local(hass)

    assert result["step_id"] == "local"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_local_rejects_other_devices(
    hass: HomeAssistant, protocol: MagicMock
) -> None:
    protocol.connect.return_value = {"mac": MAC, "model": "roborock.vacuum.s5"}

    result = await _local(hass)

    assert result["errors"] == {"base": "unsupported"}


async def test_known_mower_updates_its_host(
    hass: HomeAssistant, protocol: MagicMock
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=UNIQUE_ID,
        data={CONF_HOST: "192.168.1.2", CONF_TOKEN: TOKEN, CONF_MAC: MAC},
    )
    entry.add_to_hass(hass)

    result = await _local(hass)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == HOST


async def test_mova_account_with_one_mower(
    hass: HomeAssistant, protocol: MagicMock
) -> None:
    """The only mower in the account is picked without asking."""
    result = await _start(hass, MOVAHOME)
    assert result["step_id"] == "mova"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], MOVA_LOGIN
    )
    assert result["step_id"] == "options"
    # Cloud accounts carry mac and model in the device list: no LAN probe.
    protocol.connect.assert_not_called()

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Lawn", CONF_NOTIFY: []}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ACCOUNT_TYPE] == "mova"
    assert result["data"][CONF_DID] == "1001"
    assert result["data"][CONF_USERNAME] == "user"
    assert result["result"].unique_id == UNIQUE_ID


async def test_mova_account_with_several_mowers(
    hass: HomeAssistant, protocol: MagicMock
) -> None:
    protocol.cloud.get_devices.return_value = {
        "page": {
            "records": [
                cloud_device(),
                cloud_device(name="Back", did="1002", mac="11:22:33:44:55:66"),
            ]
        }
    }
    result = await _start(hass, MOVAHOME)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], MOVA_LOGIN
    )
    assert result["step_id"] == "devices"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"devices": f"Back - {MODEL}"}
    )
    assert result["step_id"] == "options"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NAME: "Back", CONF_NOTIFY: []}
    )
    assert result["data"][CONF_DID] == "1002"


@pytest.mark.parametrize(
    ("logged_in", "devices", "error"),
    [
        (False, None, "login_error"),
        (True, {"page": {"records": []}}, "no_devices"),
    ],
)
async def test_mova_account_errors(
    hass: HomeAssistant,
    protocol: MagicMock,
    logged_in: bool,
    devices: dict | None,
    error: str,
) -> None:
    protocol.cloud.logged_in = logged_in
    protocol.cloud.get_devices.return_value = devices

    result = await _start(hass, MOVAHOME)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], MOVA_LOGIN
    )

    assert result["step_id"] == "mova"
    assert result["errors"] == {"base": error}


async def test_options_flow_of_a_local_entry(hass: HomeAssistant) -> None:
    """Without a cloud account there is no map, so only notifications are offered."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=UNIQUE_ID,
        data={CONF_HOST: HOST, CONF_TOKEN: TOKEN, CONF_USERNAME: None},
        options={CONF_NOTIFY: True},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert list(result["data_schema"].schema) == [CONF_NOTIFY]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_NOTIFY: ["error"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_NOTIFY: ["error"]}
