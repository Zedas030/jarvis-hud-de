"""Jarvis for Home Assistant's Assist (e.g. the Home Assistant Voice PE): the satellite is only the ear.

  stt.jarvis            what the satellite heard goes to /api/hear and is transcribed by the PC's GPU Whisper;
                        PC off -> the Pi's Whisper (stt.faster_whisper) gets the same audio instead
  conversation.jarvis   the text goes to /api/ask; Jarvis answers (router, Claude, tools) and says it on the
                        room's speaker; PC off -> Home Assistant's own agent

Setup: Settings -> Devices & services -> Add integration -> Jarvis (URL, key from pair.html?schluessel,
optional Tailscale IP). scripts/ha_voice_setup.py then puts both into the pipeline "Jarvis".
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .client import JarvisClient
from .const import CONF_IP, CONF_KEY, CONF_URL, TIMEOUT_S

PLATFORMS = [Platform.CONVERSATION, Platform.STT]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # one client for both platforms: it remembers when the PC was last unreachable, so after a failed
    # transcription the answer does not wait for the same timeout again
    client = JarvisClient(entry.data[CONF_URL], entry.data[CONF_KEY], entry.data.get(CONF_IP), timeout=TIMEOUT_S)
    entry.runtime_data = client
    entry.async_on_unload(client.close)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
