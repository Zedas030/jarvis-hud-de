"""The Jarvis conversation agent."""
from __future__ import annotations

import dataclasses
import logging
from typing import Literal

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import intent
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import ulid

from .client import JarvisAuthError, JarvisClient, JarvisUnreachable
from .const import DOMAIN, FALLBACK_AGENT, SATELLITE_SILENT

_LOGGER = logging.getLogger(__name__)

OFFLINE = "Jarvis ist gerade nicht erreichbar, der PC ist wohl aus."
BAD_KEY = "Jarvis lehnt den Schlüssel ab. Bitte in der Jarvis-Integration einen neuen eintragen."


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([JarvisAgent(entry, entry.runtime_data)])


class JarvisAgent(conversation.ConversationEntity):
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, entry: ConfigEntry, client: JarvisClient) -> None:
        self._client = client
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Jarvis",
                                            manufacturer="Jarvis", model="Sprachassistent auf dem PC",
                                            entry_type=DeviceEntryType.SERVICE)

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        return ["de"]

    def _room(self, device_id: str | None) -> str:
        """The satellite's area ("Schlafzimmer"), so "Licht an" means the room the speaker stands in."""
        if not device_id:
            return ""
        dev = dr.async_get(self.hass).async_get(device_id)
        if not dev or not dev.area_id:
            return ""
        area = ar.async_get(self.hass).async_get_area(dev.area_id)
        return area.name if area else ""

    async def async_process(self, user_input: conversation.ConversationInput) -> conversation.ConversationResult:
        result = await self._process(user_input)
        if SATELLITE_SILENT and user_input.device_id:
            # The Voice PE is only the microphone (Nutzer, 17.11.2026): it never says anything, not even when
            # the room's speaker failed or the PC is off. The pipeline keeps text-to-speech (the satellite
            # asks for that stage and blinks red without it); empty speech makes the pipeline skip it.
            result.response.async_set_speech("")
            result = dataclasses.replace(result, continue_conversation=False)
        return result

    async def _process(self, user_input: conversation.ConversationInput) -> conversation.ConversationResult:
        conversation_id = user_input.conversation_id or ulid.ulid_now()
        if self._client.recently_down:
            # the transcription just found the PC off: no second wait for the same timeout
            return await self._fallback(user_input, conversation_id)
        try:
            out = await self._client.ask(user_input.text, room=self._room(user_input.device_id),
                                         speak=bool(user_input.device_id))
            text = str(out.get("text") or "").strip() or "Dazu habe ich keine Antwort bekommen."
            if out.get("spoken_on"):
                # Jarvis said it on the room's speaker (the Sonos): empty speech makes the pipeline skip
                # text-to-speech, so the satellite stays silent. No follow-up listening either — the
                # satellite would hear Jarvis' own voice from the speaker.
                _LOGGER.debug("answer spoken on %s", out["spoken_on"])
                return self._result(user_input.language, "", conversation_id)
        except JarvisAuthError:
            _LOGGER.warning("Jarvis refused the key")
            text = BAD_KEY
        except JarvisUnreachable as err:
            _LOGGER.info("Jarvis unreachable (%s), falling back to %s", err, FALLBACK_AGENT)
            return await self._fallback(user_input, conversation_id)
        return self._result(user_input.language, text, conversation_id, continue_=text.endswith("?"))

    async def _fallback(self, user_input, conversation_id):
        """PC off: Home Assistant's own agent still handles lights, timers, the shopping list."""
        try:
            result = await conversation.async_converse(
                self.hass, user_input.text, conversation_id, user_input.context, user_input.language,
                agent_id=FALLBACK_AGENT, device_id=user_input.device_id, satellite_id=user_input.satellite_id)
        except Exception:  # noqa: BLE001 — the fallback must never leave the satellite silent
            _LOGGER.exception("fallback agent failed")
            return self._result(user_input.language, OFFLINE, conversation_id)
        if result.response.response_type == intent.IntentResponseType.ERROR:
            return self._result(user_input.language, OFFLINE, conversation_id)
        return result

    @staticmethod
    def _result(language, text, conversation_id, continue_=False):
        response = intent.IntentResponse(language=language)
        response.async_set_speech(text)
        return conversation.ConversationResult(response=response, conversation_id=conversation_id,
                                               continue_conversation=continue_)
