"""
Small signal helpers of the voice engine, all on 16-bit PCM bytes.

  resample(pcm, src, dst)          linear resampling (mono int16)
  fade(pcm, start_gain, end_gain)  gain ramp, for click-free pause / resume / duck
  wav_bytes / read_wav             WAV in and out for debug clips, tests and fixtures
  Decoder(mime)                    provider audio (mp3, wav, raw pcm) -> 16-bit PCM at out_rate, chunk by chunk
  EchoGuard                        the mic hears his own voice from a speaker in the room:
                                   level model (as the old HUD did) plus a text check, and an
                                   optional speexdsp echo canceller when the package is there
  estimate_delay(ref, mic, rate)   cross-correlation: how many ms later the speaker's sound arrives

Rauschunterdrückung: RNNoise/DeepFilterNet are not wired in yet — Silero VAD copes with room noise
and the browser already applies its own suppression. The hook is `denoise()`; it returns the frame
unchanged unless a denoiser was registered.
"""
import array
import io
import math
import struct
import time


def _ints(pcm):
    a = array.array("h")
    a.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    return a


def resample(pcm, src, dst):
    if src == dst or not pcm:
        return pcm
    a = _ints(pcm)
    n = len(a)
    if n < 2:
        return pcm
    m = int(n * dst / src)
    out = array.array("h", bytes(2 * m))
    step = (n - 1) / max(1, m - 1)
    for i in range(m):
        x = i * step
        j = int(x)
        f = x - j
        k = j + 1 if j + 1 < n else j
        out[i] = int(a[j] * (1 - f) + a[k] * f)
    return out.tobytes()


def fade(pcm, start_gain, end_gain):
    a = _ints(pcm)
    n = len(a)
    if not n:
        return pcm
    for i in range(n):
        g = start_gain + (end_gain - start_gain) * i / max(1, n - 1)
        v = int(a[i] * g)
        a[i] = max(-32768, min(32767, v))
    return a.tobytes()


def gain(pcm, g):
    return fade(pcm, g, g) if g != 1.0 else pcm


def silence(ms, rate):
    return bytes(2 * int(rate * ms / 1000))


def wav_bytes(pcm, rate, channels=1):
    buf = io.BytesIO()
    buf.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
              struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * channels * 2, channels * 2, 16) +
              b"data" + struct.pack("<I", len(pcm)) + pcm)
    return buf.getvalue()


def read_wav(data):
    """-> (pcm int16 mono bytes, rate). Stereo is averaged, 8-bit widened, other depths refused."""
    import wave
    with wave.open(io.BytesIO(data)) as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width == 1:
        raw = array.array("h", [(b - 128) << 8 for b in raw]).tobytes()
    elif width != 2:
        raise ValueError(f"unsupported sample width {width}")
    if ch > 1:
        a = _ints(raw)
        mono = array.array("h", [int(sum(a[i:i + ch]) / ch) for i in range(0, len(a) - ch + 1, ch)])
        raw = mono.tobytes()
    return raw, rate


def rms(pcm):
    a = _ints(pcm)
    if not a:
        return 0.0
    return math.sqrt(sum(x * x for x in a) / len(a)) / 32768.0


# ── provider audio -> PCM ─────────────────────────────────────
class Decoder:
    """Feeds the chunks a TTS provider sends and yields 16-bit mono PCM at out_rate.

    mp3 goes through PyAV's parser frame by frame (streaming), WAV strips its header and
    reads the rate from it, "audio/pcm;rate=22050" is taken as is (Piper's stream)."""
    def __init__(self, mime, out_rate):
        self.mime = (mime or "").split(";")[0].strip().lower()
        self.out_rate = out_rate
        self.rate = None
        self.header = b""
        self.codec = None
        self.resampler = None
        if self.mime == "audio/pcm":
            for part in (mime or "").split(";")[1:]:
                k, _, v = part.strip().partition("=")
                if k == "rate" and v.isdigit():
                    self.rate = int(v)
            self.rate = self.rate or 22050
        elif self.mime in ("audio/mpeg", "audio/mp3", "audio/aac", "audio/ogg", "audio/mp4"):
            import av
            name = {"audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/aac": "aac", "audio/ogg": "vorbis",
                    "audio/mp4": "aac"}[self.mime]
            self.codec = av.CodecContext.create(name, "r")
            self.resampler = av.AudioResampler(format="s16", layout="mono", rate=out_rate)
            self.av = av

    def feed(self, chunk):
        if not chunk:
            return b""
        if self.codec is not None:
            out = []
            if not self.header and chunk[:3] == b"ID3" and len(chunk) >= 10:
                size = 10 + ((chunk[6] & 0x7F) << 21 | (chunk[7] & 0x7F) << 14 | (chunk[8] & 0x7F) << 7 | (chunk[9] & 0x7F))
                chunk = chunk[size:]
                self.header = b"id3"
            for packet in self.codec.parse(chunk):
                for frame in self.codec.decode(packet):
                    for f in self.resampler.resample(frame):
                        out.append(f.to_ndarray().tobytes())
            return b"".join(out)
        if self.mime in ("audio/wav", "audio/x-wav", "audio/wave"):
            if self.rate is None:
                self.header += chunk
                if len(self.header) < 44:
                    return b""
                if self.header[:4] != b"RIFF":
                    raise ValueError("not a WAV stream")
                self.rate = struct.unpack("<I", self.header[24:28])[0]
                ch = struct.unpack("<H", self.header[22:24])[0]
                # the data chunk may come after other chunks: find it
                i = 12
                while i + 8 <= len(self.header):
                    cid, size = self.header[i:i + 4], struct.unpack("<I", self.header[i + 4:i + 8])[0]
                    if cid == b"data":
                        chunk, self.header = self.header[i + 8:], b"done"
                        break
                    i += 8 + size
                else:
                    return b""
                self.channels = ch
            if getattr(self, "channels", 1) > 1:
                a = _ints(chunk)
                ch = self.channels
                chunk = array.array("h", [int(sum(a[i:i + ch]) / ch) for i in range(0, len(a) - ch + 1, ch)]).tobytes()
            return resample(chunk, self.rate, self.out_rate)
        if self.mime == "audio/pcm":
            return resample(chunk, self.rate, self.out_rate)
        raise ValueError(f"cannot decode {self.mime}")

    def flush(self):
        if self.codec is not None:
            out = []
            try:
                for frame in self.codec.decode(None):
                    for f in self.resampler.resample(frame):
                        out.append(f.to_ndarray().tobytes())
            except Exception:  # noqa: BLE001 — some codecs refuse a second flush
                pass
            return b"".join(out)
        return b""


# ── noise suppression hook ────────────────────────────────────
_DENOISER = {"fn": None}


def set_denoiser(fn):
    """fn(frame_bytes) -> frame_bytes, 16 kHz. RNNoise or DeepFilterNet can plug in here."""
    _DENOISER["fn"] = fn


def denoise(frame):
    fn = _DENOISER["fn"]
    return fn(frame) if fn else frame


# ── echo from a speaker in the room ───────────────────────────
def estimate_delay(ref, mic, rate, max_ms=2500, step_ms=10):
    """How many ms after the reference the same sound shows in the mic (coarse envelope
    cross-correlation on 10-ms energy frames; good enough to align a Sonos announcement)."""
    def envelope(pcm):
        a = _ints(pcm)
        n = int(rate * step_ms / 1000)
        return [math.sqrt(sum(x * x for x in a[i:i + n]) / max(1, n)) for i in range(0, len(a) - n + 1, n)]
    r, m = envelope(ref), envelope(mic)
    if len(r) < 5 or len(m) < 5:
        return None
    rm, mm = sum(r) / len(r), sum(m) / len(m)
    r = [x - rm for x in r]
    m = [x - mm for x in m]
    best, best_lag = -1e18, 0
    for lag in range(0, min(len(m) - 3, int(max_ms / step_ms))):
        s = sum(r[i] * m[i + lag] for i in range(min(len(r), len(m) - lag)))
        if s > best:
            best, best_lag = s, lag
    return best_lag * step_ms


class EchoGuard:
    """While Jarvis sounds from a speaker the mic cannot cancel (Sonos, Echo), decide whether a
    loud moment is the user or himself.

    - level(): his echo as the mic hears it, learned during the first `warmup_ms` of each
      announcement and decaying afterwards; a voice must beat it by `factor`.
    - text_match(): share of a transcript's words found in his recent sentences.
    - If `speexdsp` is importable an echo canceller runs on the frames (`cancel()`), fed with the
      reference PCM we sent to the speaker, shifted by the delay estimate."""
    def __init__(self, factor=1.8, decay=0.996, warmup_ms=1200, frame_ms=20, rate=16000):
        self.factor, self.decay, self.warmup_ms, self.frame_ms = factor, decay, warmup_ms, frame_ms
        self.rate = rate
        self.echo = 0.0
        self.active_ms = None
        self.said = []
        self.canceller = None
        self.delay_ms = None
        self._ref = bytearray()
        try:
            import speexdsp  # noqa: F401
            self.canceller = speexdsp.EchoCanceller.create(int(rate * frame_ms / 1000), int(rate * 2.0), rate)
        except Exception:  # noqa: BLE001 — not installed: level + text model only
            self.canceller = None

    def start(self):
        self.echo, self.active_ms = 0.0, 0
        self._ref = bytearray()

    def stop(self):
        self.active_ms = None

    def reference(self, pcm16k):
        """PCM (16 kHz) that is being played on the room speaker right now."""
        if self.active_ms is not None:
            self._ref += pcm16k
            del self._ref[:-self.rate * 2 * 6]          # keep six seconds

    def observe(self, level):
        """Feed the mic level of a frame while he speaks; True = still in the warm-up (ignore barge-in)."""
        if self.active_ms is None:
            return False
        warming = self.active_ms < self.warmup_ms
        self.active_ms += self.frame_ms
        if warming or level <= self.echo * self.factor:
            self.echo = max(level, self.echo * self.decay)
        else:
            self.echo *= self.decay
        return warming

    def bar(self):
        return self.echo * self.factor if self.active_ms is not None else 0.0

    def cancel(self, frame):
        if self.canceller is None or self.active_ms is None or len(self._ref) < len(frame):
            return frame
        try:
            lag = int(self.rate * 2 * (self.delay_ms or 0) / 1000)
            lag -= lag % 2
            start = max(0, len(self._ref) - len(frame) - lag)
            ref = bytes(self._ref[start:start + len(frame)]).ljust(len(frame), b"\0")
            return self.canceller.process(frame, ref)
        except Exception:  # noqa: BLE001
            return frame

    def remember(self, sentence, now=None):
        self.said.append((time.time() if now is None else now, sentence.lower()))
        del self.said[:-12]

    def text_match(self, text, within_s=None, now=None):
        """within_s: only sentences queued that recently (a long answer's echo arrives after it ends)."""
        import re
        words = re.findall(r"\w{3,}", (text or "").lower())
        now = time.time() if now is None else now
        said = " ".join(t for at, t in self.said if within_s is None or now - at <= within_s)
        if not words or not said:
            return 0.0
        return sum(1 for w in words if w in said) / len(words)
