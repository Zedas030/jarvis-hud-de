"""Speech to text on the PC's GPU: the Voice PE is only the ear.

Home Assistant hands over the audio of one utterance (it ends the stream itself after the speaker stops).
It is collected and sent to Jarvis' /api/hear in one go: 16 kHz mono is ~100 KB for three seconds, which
crosses the LAN in milliseconds, and keeping it means the Pi's Whisper can get the very same audio when
the PC is off. A warm-up call goes out the moment listening starts, so a Whisper that was freed while idle
is loaded again before the user has finished the sentence.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterable

from homeassistant.components import stt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import JarvisAuthError, JarvisClient, JarvisUnreachable
from .const import DOMAIN, FALLBACK_STT, MAX_AUDIO_BYTES

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([JarvisSpeechToText(entry, entry.runtime_data)])


class JarvisSpeechToText(stt.SpeechToTextEntity):
    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, entry: ConfigEntry, client: JarvisClient) -> None:
        self._client = client
        self._attr_unique_id = f"{entry.entry_id}-stt"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Jarvis",
                                            manufacturer="Jarvis", model="Sprachassistent auf dem PC",
                                            entry_type=DeviceEntryType.SERVICE)

    @property
    def supported_languages(self) -> list[str]:
        return ["de"]

    @property
    def supported_formats(self) -> list[stt.AudioFormats]:
        return [stt.AudioFormats.WAV]

    @property
    def supported_codecs(self) -> list[stt.AudioCodecs]:
        return [stt.AudioCodecs.PCM]

    @property
    def supported_bit_rates(self) -> list[stt.AudioBitRates]:
        return [stt.AudioBitRates.BITRATE_16]

    @property
    def supported_sample_rates(self) -> list[stt.AudioSampleRates]:
        return [stt.AudioSampleRates.SAMPLERATE_16000]

    @property
    def supported_channels(self) -> list[stt.AudioChannels]:
        return [stt.AudioChannels.CHANNEL_MONO]

    async def async_process_audio_stream(self, metadata: stt.SpeechMetadata,
                                         stream: AsyncIterable[bytes]) -> stt.SpeechResult:
        warm = None if self._client.recently_down else asyncio.create_task(self._warm())
        audio = bytearray()
        async for chunk in stream:
            if len(audio) < MAX_AUDIO_BYTES:
                audio += chunk[: MAX_AUDIO_BYTES - len(audio)]
        if warm is not None:
            await warm
        if not self._client.recently_down:
            try:
                out = await self._client.hear(bytes(audio))
                if out.get("status") != "error":
                    _LOGGER.debug("Jarvis heard %r in %s ms", out.get("text"), out.get("ms"))
                    return stt.SpeechResult(str(out.get("text") or ""), stt.SpeechResultState.SUCCESS)
                _LOGGER.info("Jarvis could not transcribe, using %s", FALLBACK_STT)
            except JarvisAuthError:
                _LOGGER.warning("Jarvis refused the key, using %s", FALLBACK_STT)
            except JarvisUnreachable as err:
                _LOGGER.info("Jarvis unreachable (%s), using %s", err, FALLBACK_STT)
        return await self._fallback(metadata, bytes(audio))

    async def _warm(self) -> None:
        try:
            await self._client.hear(b"", timeout=5)
        except JarvisAuthError:
            pass        # reported by the real call
        except JarvisUnreachable:
            pass        # recently_down is set: the audio goes straight to the Pi

    async def _fallback(self, metadata: stt.SpeechMetadata, audio: bytes) -> stt.SpeechResult:
        entity = stt.async_get_speech_to_text_entity(self.hass, FALLBACK_STT)
        if entity is None:
            _LOGGER.warning("%s not found, nothing understood", FALLBACK_STT)
            return stt.SpeechResult(None, stt.SpeechResultState.ERROR)

        async def replay():
            for i in range(0, len(audio), 4096):
                yield audio[i:i + 4096]

        try:
            return await entity.internal_async_process_audio_stream(metadata, replay())
        except Exception:  # noqa: BLE001 — never leave the satellite hanging
            _LOGGER.exception("fallback speech to text failed")
            return stt.SpeechResult(None, stt.SpeechResultState.ERROR)
