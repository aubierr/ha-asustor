"""Config flow for ASUSTOR NAS."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT

from .const import (
    CONF_COMMUNITY,
    DEFAULT_COMMUNITY,
    DEFAULT_PORT,
    DOMAIN,
    OID_SYS_DESCR,
    OID_SYS_NAME,
)
from .snmp import AsustorSnmp, create_engine, to_str


class AsustorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_HOST])
            self._abort_if_unique_id_configured()
            engine = await self.hass.async_add_executor_job(create_engine)
            res: dict[str, Any] = {}
            try:
                client = AsustorSnmp(
                    engine,
                    user_input[CONF_HOST],
                    user_input[CONF_PORT],
                    user_input[CONF_COMMUNITY],
                )
                res = await client.get([OID_SYS_DESCR, OID_SYS_NAME])
                if not res:
                    errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_connect"
            finally:
                engine.close_dispatcher()
            if not errors:
                title = (
                    user_input.get(CONF_NAME)
                    or (to_str(res[OID_SYS_NAME]) if OID_SYS_NAME in res else "")
                    or user_input[CONF_HOST]
                )
                data = {k: user_input[k] for k in (CONF_HOST, CONF_PORT, CONF_COMMUNITY)}
                return self.async_create_entry(title=title, data=data)

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Optional(CONF_NAME): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Required(CONF_COMMUNITY, default=DEFAULT_COMMUNITY): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
