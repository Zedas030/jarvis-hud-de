"""Config flow: URL, key, optional Tailscale IP; "Neu konfigurieren" swaps the key."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .client import JarvisAuthError, JarvisClient, JarvisUnreachable
from .const import CONF_IP, CONF_KEY, CONF_URL, DOMAIN, PING_TIMEOUT_S


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema({
        vol.Required(CONF_URL, default=defaults.get(CONF_URL, "https://")): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)),
        vol.Required(CONF_KEY): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
        vol.Optional(CONF_IP, description={"suggested_value": defaults.get(CONF_IP, "")}): str,
    })


async def _check(data: dict[str, Any]) -> str | None:
    client = JarvisClient(data[CONF_URL], data[CONF_KEY], data.get(CONF_IP))
    try:
        await client.ping(timeout=PING_TIMEOUT_S)
    except JarvisAuthError:
        return "invalid_auth"
    except JarvisUnreachable:
        return "cannot_connect"
    finally:
        await client.close()
    return None


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    return {CONF_URL: user_input[CONF_URL].strip().rstrip("/"), CONF_KEY: user_input[CONF_KEY].strip(),
            CONF_IP: (user_input.get(CONF_IP) or "").strip()}


class JarvisConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _clean(user_input)
            await self.async_set_unique_id(data[CONF_URL].lower())
            self._abort_if_unique_id_configured()
            error = await _check(data)
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(title="Jarvis", data=data)
        return self.async_show_form(step_id="user", data_schema=_schema(user_input or {}), errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _clean(user_input)
            error = await _check(data)
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(step_id="reconfigure", data_schema=_schema(dict(entry.data)), errors=errors)
