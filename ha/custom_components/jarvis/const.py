"""Constants for the Jarvis integration."""

DOMAIN = "jarvis"

CONF_URL = "url"
CONF_KEY = "key"
CONF_IP = "ip"

# Jarvis answers within 50 s (JARVIS_ASK_TIMEOUT_S); a little on top for the network.
TIMEOUT_S = 55
PING_TIMEOUT_S = 10
# The GPU transcribes a sentence in ~0.1 s; 10 s covers loading the model after it was freed.
HEAR_TIMEOUT_S = 10
# Over Tailscale in the LAN a connect takes milliseconds; a PC that is off never answers it.
CONNECT_TIMEOUT_S = 3
# After a failed call the next one within this time goes to the fallback straight away.
DOWN_MEMORY_S = 30
# Longest utterance passed on (16 kHz 16-bit mono); Jarvis refuses more than 30 s.
MAX_AUDIO_BYTES = 30 * 16000 * 2

# When the PC is off, Home Assistant's own agent still switches lights and sets timers,
FALLBACK_AGENT = "conversation.home_assistant"
# and the Pi's Whisper (pi/voice/compose.yml) still understands what was said.
FALLBACK_STT = "stt.faster_whisper"

# The Voice PE is only the microphone: a satellite never gets speech back; Jarvis answers on the room's speaker.
SATELLITE_SILENT = True
