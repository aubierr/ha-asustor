"""Config, reconfigure, reauth and options flows for ASUSTOR NAS (SNMP v2c / v3)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    AUTO,
    CONF_AUTH_KEY,
    CONF_AUTH_PROTOCOL,
    CONF_COMMUNITY,
    CONF_INTERFACES,
    CONF_PRIV_KEY,
    CONF_PRIV_PROTOCOL,
    CONF_SCAN_INTERVAL,
    CONF_SNMP_VERSION,
    CONF_USERNAME,
    DEFAULT_COMMUNITY,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    OID_SYS_DESCR,
    OID_SYS_NAME,
    SNMP_V2C,
    SNMP_V3,
)
from .snmp import (
    AUTH_PROTOCOLS,
    PRIV_PROTOCOLS,
    AsustorSnmp,
    SnmpAuthError,
    build_auth,
    create_engine,
    to_str,
)

ADVANCED = "advanced"  # collapsed section: protocols + encryption password
MIN_KEY_LENGTH = 8  # RFC 3414: authentication / privacy passphrases are >= 8 characters


class CannotConnect(Exception):
    """The NAS did not answer over SNMP."""


class InvalidAuth(Exception):
    """SNMPv3 credentials were rejected."""


async def _validate(
    hass: HomeAssistant, conn: Mapping[str, Any], timeout: float = 5, retries: int = 1
) -> str:
    """Query the NAS once; return its SNMP system name."""
    engine = await hass.async_add_executor_job(create_engine)
    try:
        client = AsustorSnmp(
            engine, conn[CONF_HOST], conn[CONF_PORT], build_auth(conn), timeout=timeout, retries=retries
        )
        res = await client.get([OID_SYS_DESCR, OID_SYS_NAME])
    except SnmpAuthError as err:
        raise InvalidAuth from err
    except Exception as err:  # noqa: BLE001
        raise CannotConnect from err
    finally:
        engine.close_dispatcher()
    if not res:
        raise CannotConnect
    return to_str(res[OID_SYS_NAME]) if OID_SYS_NAME in res else conn[CONF_HOST]


# ADM's SNMP v3 form only asks for a user name and ONE password, so the protocols are unknown.
# Candidates are tried from most to least likely; the first that the NAS accepts is stored.
AUTO_AUTH = ["sha", "md5"]
AUTO_PRIV = ["aes", "des", "none"]
PROBE_TIMEOUT = 2.5  # seconds per candidate while probing (worst case ~15 s when the NAS is unreachable)


async def _resolve_v3(hass: HomeAssistant, conn: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Return (connection with concrete protocols, NAS name). Raises InvalidAuth / CannotConnect.

    Not every agent answers a mismatching combination with an SNMP report: some stay silent. A timeout
    therefore only rules out that candidate. At the end, if at least one candidate was explicitly
    rejected the NAS is reachable and the credentials are wrong (InvalidAuth); if every candidate timed
    out the NAS is unreachable or SNMP v3 is off (CannotConnect).
    """
    auths = AUTO_AUTH if conn[CONF_AUTH_PROTOCOL] == AUTO else [conn[CONF_AUTH_PROTOCOL]]
    privs = AUTO_PRIV if conn[CONF_PRIV_PROTOCOL] == AUTO else [conn[CONF_PRIV_PROTOCOL]]
    candidates = [(a, p) for a in auths for p in privs if not (p != "none" and a == "none")]
    probing = len(candidates) > 1
    timeout, retries = (PROBE_TIMEOUT, 0) if probing else (5, 1)
    reachable = False
    for auth, priv in candidates:
        candidate = {
            **conn,
            CONF_AUTH_PROTOCOL: auth,
            CONF_PRIV_PROTOCOL: priv,
            CONF_AUTH_KEY: conn[CONF_AUTH_KEY] if auth != "none" else "",
            CONF_PRIV_KEY: conn[CONF_PRIV_KEY] if priv != "none" else "",
        }
        try:
            return candidate, await _validate(hass, candidate, timeout, retries)
        except InvalidAuth:
            reachable = True
        except CannotConnect:
            continue
    raise InvalidAuth if reachable else CannotConnect


def _protocol_selector(options: list[str], translation_key: str) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=options, mode=SelectSelectorMode.DROPDOWN, translation_key=translation_key
        )
    )


def _secret() -> TextSelector:
    return TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


class AsustorConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._conn: dict[str, Any] = {}
        self._name: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> AsustorOptionsFlow:
        return AsustorOptionsFlow()

    # --- step 1: where is the NAS and which SNMP version ---------------------------
    def _address_schema(self, defaults: Mapping[str, Any], with_name: bool) -> vol.Schema:
        fields: dict[Any, Any] = {vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str}
        if with_name:
            fields[vol.Optional(CONF_NAME)] = str
        fields[vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT))] = int
        fields[
            vol.Required(CONF_SNMP_VERSION, default=defaults.get(CONF_SNMP_VERSION, SNMP_V2C))
        ] = SelectSelector(
            SelectSelectorConfig(
                options=[SNMP_V2C, SNMP_V3],
                mode=SelectSelectorMode.LIST,
                translation_key="snmp_version",
            )
        )
        return vol.Schema(fields)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_HOST])
            self._abort_if_unique_id_configured()
            self._name = user_input.get(CONF_NAME)
            self._conn = {k: user_input[k] for k in (CONF_HOST, CONF_PORT, CONF_SNMP_VERSION)}
            return await self._async_next_credentials()
        return self.async_show_form(
            step_id="user", data_schema=self._address_schema(user_input or {}, with_name=True)
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change host, port, SNMP version or credentials without removing the integration."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            if host != entry.data[CONF_HOST] and any(
                e.unique_id == host for e in self._async_current_entries(include_ignore=False)
            ):
                errors["base"] = "already_configured"
            else:
                self._conn = {k: user_input[k] for k in (CONF_HOST, CONF_PORT, CONF_SNMP_VERSION)}
                return await self._async_next_credentials()
        defaults = user_input or {**entry.data, CONF_SNMP_VERSION: entry.data.get(CONF_SNMP_VERSION, SNMP_V2C)}
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._address_schema(defaults, with_name=False),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """SNMPv3 credentials were rejected."""
        self._conn = dict(entry_data)
        return await self._async_next_credentials()

    async def _async_next_credentials(self) -> ConfigFlowResult:
        if self._conn.get(CONF_SNMP_VERSION, SNMP_V2C) == SNMP_V3:
            return await self.async_step_v3()
        return await self.async_step_v2c()

    # --- step 2a: SNMP v2c community -------------------------------------------------
    async def async_step_v2c(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            conn = {**self._conn, CONF_COMMUNITY: user_input[CONF_COMMUNITY]}
            conn.pop(CONF_USERNAME, None)
            try:
                name = await _validate(self.hass, conn)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            else:
                self._conn = conn
                return self._async_finish(name)
        default = (user_input or self._conn).get(CONF_COMMUNITY, DEFAULT_COMMUNITY)
        return self.async_show_form(
            step_id="v2c",
            data_schema=vol.Schema({vol.Required(CONF_COMMUNITY, default=default): str}),
            errors=errors,
        )

    # --- step 2b: SNMP v3 user ---------------------------------------------------------
    async def async_step_v3(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """User name + password (what ADM asks for); protocols are an advanced, collapsed option."""
        errors: dict[str, str] = {}
        if user_input is not None:
            advanced = user_input.get(ADVANCED) or {}
            auth = advanced.get(CONF_AUTH_PROTOCOL, AUTO)
            priv = advanced.get(CONF_PRIV_PROTOCOL, AUTO)
            password = user_input.get(CONF_AUTH_KEY) or ""
            # ADM has a single password field: encryption reuses it unless another one is given.
            priv_key = advanced.get(CONF_PRIV_KEY) or password
            if auth == "none" and priv not in ("none", AUTO):
                errors["base"] = "priv_requires_auth"
            elif (auth != "none" and len(password) < MIN_KEY_LENGTH) or (
                priv not in ("none",) and auth != "none" and len(priv_key) < MIN_KEY_LENGTH
            ):
                errors["base"] = "key_too_short"
            else:
                conn = {
                    **self._conn,
                    CONF_USERNAME: user_input[CONF_USERNAME],
                    CONF_AUTH_PROTOCOL: auth,
                    CONF_AUTH_KEY: password,
                    CONF_PRIV_PROTOCOL: priv,
                    CONF_PRIV_KEY: priv_key,
                }
                conn.pop(CONF_COMMUNITY, None)
                try:
                    conn, name = await _resolve_v3(self.hass, conn)
                except InvalidAuth:
                    errors["base"] = "invalid_auth"
                except CannotConnect:
                    errors["base"] = "cannot_connect"
                else:
                    self._conn = conn
                    return self._async_finish(name)

        d = self._conn if user_input is None else {
            **user_input, **(user_input.get(ADVANCED) or {})
        }
        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME, default=d.get(CONF_USERNAME, "")): str,
                vol.Required(CONF_AUTH_KEY, default=d.get(CONF_AUTH_KEY, "")): _secret(),
                vol.Required(ADVANCED): section(
                    vol.Schema(
                        {
                            vol.Required(
                                CONF_AUTH_PROTOCOL, default=d.get(CONF_AUTH_PROTOCOL, AUTO)
                            ): _protocol_selector([AUTO, *AUTH_PROTOCOLS], "auth_protocol"),
                            vol.Required(
                                CONF_PRIV_PROTOCOL, default=d.get(CONF_PRIV_PROTOCOL, AUTO)
                            ): _protocol_selector([AUTO, *PRIV_PROTOCOLS], "priv_protocol"),
                            vol.Optional(CONF_PRIV_KEY, default=d.get(CONF_PRIV_KEY, "")): _secret(),
                        }
                    ),
                    {"collapsed": True},
                ),
            }
        )
        return self.async_show_form(step_id="v3", data_schema=schema, errors=errors)

    # --- done ---------------------------------------------------------------------------
    def _async_finish(self, nas_name: str) -> ConfigFlowResult:
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data=self._conn)
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(), unique_id=self._conn[CONF_HOST], data=self._conn
            )
        return self.async_create_entry(title=self._name or nas_name, data=self._conn)


class AsustorOptionsFlow(OptionsFlowWithReload):
    """Polling interval and tracked network interfaces (reloads the entry on save)."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_INTERFACES: user_input[CONF_INTERFACES],
                }
            )

        coordinator = getattr(self.config_entry, "runtime_data", None)
        selected = self.config_entry.options.get(CONF_INTERFACES)
        available: list[str] = []
        if coordinator is not None:
            available = await coordinator.async_list_interfaces()
            if selected is None:  # currently using the default rule
                selected = [i["name"] for i in coordinator.data["interfaces"].values()]
        selected = selected or []
        choices = sorted(set(available) | set(selected))

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_INTERFACES, default=selected): SelectSelector(
                    SelectSelectorConfig(options=choices, multiple=True, mode=SelectSelectorMode.DROPDOWN)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
