#!/usr/bin/env python3
"""tools-audio.py — offline audio preparation and waveform synchronization.

Reads and writes WAV (PCM 8/16/24/32 + float32) and AIFF/AIFF-C (PCM) with
pure stdlib parsing; other formats (MP3/FLAC/OGG) convert through a
configurable external ffmpeg when installed. Analysis: levels, silence,
onsets, beat estimation, channel balance. Everything is frame-accurate and
deterministic. Pure stdlib.
"""
import math
import struct

core = None
err = None
require = None


def _init():
    global core
    if core is None:
        import importlib.util as ilu
        import sys as sys_
        from pathlib import Path as P

        def sib(name):
            key = "studio." + name
            mod = sys_.modules.get(key)
            if mod is not None:
                return mod
            p = P(__file__).resolve().parent / ("tools-" + name + ".py")
            spec = ilu.spec_from_file_location(key, p)
            mod = ilu.module_from_spec(spec)
            sys_.modules[key] = mod
            spec.loader.exec_module(mod)
            return mod
        core = sib("core")
    return core


def err(code, message, **details):
    _init()
    return core.err(code, message, **details)


def require(cond, code, message, **details):
    _init()
    return core.require(cond, code, message, **details)


class Audio(object):
    """Decoded audio: float samples in [-1, 1], shape [channel][frame]."""
    __slots__ = ("rate", "channels", "samples")

    def __init__(self, rate, channels, samples):
        self.rate = int(rate)
        self.channels = int(channels)
        self.samples = samples
        require(self.channels >= 1 and len(samples) == self.channels,
                "invalid_input", "channel/sample mismatch")
        require(self.rate >= 8000, "invalid_input", "suspicious sample rate %d" % self.rate)

    @property
    def frames(self):
        return len(self.samples[0]) if self.samples else 0

    @property
    def duration(self):
        return self.frames / float(self.rate)


# ------------------------------------------------------------- decoding -----

def decode_wav(data):
    require(data[:4] == b"RIFF" and data[8:12] == b"WAVE", "invalid_wav",
            "not a RIFF/WAVE file")
    pos = 12
    fmt = None
    payload = bytearray()
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        ln = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + ln]
        if cid == b"fmt ":
            fmt = body
        elif cid == b"data":
            payload += body
        pos += 8 + ln + (ln & 1)
    require(fmt is not None and len(fmt) >= 16, "invalid_wav", "missing fmt chunk")
    tag, ch, rate, _brate, _balign, bits = struct.unpack("<HHIIHH", fmt[:16])
    if tag == 0xFFFE and len(fmt) >= 40:
        tag = struct.unpack("<H", fmt[24:26])[0]
    if tag == 3:  # IEEE float
        require(bits in (32, 64), "invalid_wav", "unsupported float depth %d" % bits)
        width = bits // 8
        n = len(payload) // (width * ch)
        chans = [[] for _ in range(ch)]
        for i in range(n):
            for c in range(ch):
                off = (i * ch + c) * width
                if bits == 32:
                    v = struct.unpack("<f", payload[off:off + 4])[0]
                else:
                    v = struct.unpack("<d", payload[off:off + 8])[0]
                chans[c].append(max(-1.0, min(1.0, v)))
        return Audio(rate, ch, chans)
    require(tag == 1, "invalid_wav", "unsupported WAV codec tag %d (PCM only)" % tag)
    require(bits in (8, 16, 24, 32), "invalid_wav", "unsupported bit depth %d" % bits)
    width = bits // 8
    n = len(payload) // (width * ch)
    chans = [[] for _ in range(ch)]
    for i in range(n):
        for c in range(ch):
            off = (i * ch + c) * width
            if bits == 8:
                v = (payload[off] - 128) / 128.0
            elif bits == 16:
                v = struct.unpack("<h", payload[off:off + 2])[0] / 32768.0
            elif bits == 24:
                raw = payload[off] | (payload[off + 1] << 8) | (payload[off + 2] << 16)
                if raw & 0x800000:
                    raw -= 0x1000000
                v = raw / 8388608.0
            else:
                v = struct.unpack("<i", payload[off:off + 4])[0] / 2147483648.0
            chans[c].append(v)
    return Audio(rate, ch, chans)


def decode_aiff(data):
    require(data[:4] == b"FORM" and data[8:12] in (b"AIFF", b"AIFC"), "invalid_aiff",
            "not an AIFF/AIFF-C file")
    pos = 12
    comm = None
    ssnd = None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        ln = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + ln]
        if cid == b"COMM":
            comm = body
        elif cid == b"SSND":
            ssnd = body
        pos += 8 + ln + (ln & 1)
    require(comm is not None and len(comm) >= 18, "invalid_aiff", "missing COMM chunk")
    require(ssnd is not None, "invalid_aiff", "missing SSND chunk")
    ch, nframes, bits = struct.unpack(">hIh", comm[:8])
    # 80-bit extended float sample rate: 2-byte exponent + 8-byte mantissa
    expo = struct.unpack(">h", comm[8:10])[0]
    mant = struct.unpack(">Q", comm[10:18])[0]
    rate = int(math.ldexp(mant, expo - 16383 - 63)) or 44100
    if not (8000 <= rate <= 384000):
        rate = 44100
    offset = struct.unpack(">I", ssnd[:4])[0]
    body = ssnd[8 + offset:]
    width = (bits + 7) // 8
    n = min(nframes, len(body) // (width * ch))
    chans = [[] for _ in range(ch)]
    for i in range(n):
        for c in range(ch):
            off = (i * ch + c) * width
            raw = int.from_bytes(body[off:off + width], "big", signed=True)
            chans[c].append(raw / float(1 << (width * 8 - 1)))
    return Audio(rate, ch, chans)


def encode_wav(audio, bits=16):
    """Encode to PCM WAV (8/16/24/32-bit int) or float32 (bits='32f')."""
    require(audio.channels >= 1 and audio.frames >= 0, "invalid_input", "bad audio")
    if bits == "32f":
        return _wav_bytes(audio, 3, 4, lambda v: struct.pack("<f", v))
    require(bits in (8, 16, 24, 32), "invalid_input", "bits must be 8/16/24/32 (or '32f' float)")
    if bits == 8:
        return _wav_bytes(audio, 1, 1, lambda v: bytes((int(max(-1.0, min(1.0, v)) * 127) + 128,)))
    if bits == 16:
        return _wav_bytes(audio, 1, 2, lambda v: struct.pack("<h", int(max(-1, min(1, v)) * 32767)))
    if bits == 24:
        def p24(v):
            raw = int(max(-1, min(1, v)) * 8388607)
            if raw < 0:
                raw += 1 << 24
            return bytes((raw & 0xFF, (raw >> 8) & 0xFF, (raw >> 16) & 0xFF))
        return _wav_bytes(audio, 1, 3, p24)
    return _wav_bytes(audio, 1, 4, lambda v: struct.pack("<i", int(max(-1, min(1, v)) * 2147483647)))


def _wav_bytes(audio, tag, width, pack):
    n = audio.frames
    ch = audio.channels
    payload = bytearray()
    for i in range(n):
        for c in range(ch):
            payload += pack(audio.samples[c][i])
    byte_rate = audio.rate * ch * width
    block = ch * width
    hdr = b"RIFF" + struct.pack("<I", 36 + len(payload)) + b"WAVE"
    hdr += b"fmt " + struct.pack("<IHHIIHH", 16, tag, ch, audio.rate, byte_rate, block,
                                 width * 8)
    hdr += b"data" + struct.pack("<I", len(payload))
    return bytes(hdr) + bytes(payload)


def load_audio(path, ffmpeg=None):
    """Load WAV/AIFF natively; other formats via configured ffmpeg (optional)."""
    _init()
    data = core.read_bytes(path)
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return decode_wav(data), {"format": "wav", "path": str(path)}
    if data[:4] == b"FORM":
        return decode_aiff(data), {"format": "aiff", "path": str(path)}
    cfg = core.load_config()
    ff = ffmpeg or core.cfg_get(cfg, "apps.ffmpeg")
    if ff:
        out_wav = core.temp_dir() / "ingest.wav"
        res = core.run_command([ff, "-y", "-i", str(path), "-ar", "48000",
                                "-ac", "2", "-c:a", "pcm_s16le", str(out_wav)])
        if res["returncode"] == 0:
            a, meta = load_audio(out_wav)
            meta["converted_via"] = "ffmpeg"
            meta["source_format"] = str(path).rsplit(".", 1)[-1]
            return a, meta
        raise err("audio_convert_failed", "ffmpeg failed: %s" % res["stderr"][-300:])
    raise err("unsupported_format",
              "%s: only WAV/AIFF decode natively; install/configure ffmpeg in "
              "studio-config.json (apps.ffmpeg) for MP3/FLAC/OGG" % path)


# ------------------------------------------------------------- transforms ---

def convert(audio, rate=None, channels=None):
    """Sample-rate conversion (linear interpolation) and channel remapping."""
    if rate is None and channels is None:
        return Audio(audio.rate, audio.channels, [list(c) for c in audio.samples])
    samples = audio.samples
    ch = audio.channels
    if channels is not None and channels != ch:
        if channels == 1:
            mono = [(sum(c[i] for c in samples) / ch) for i in range(audio.frames)]
            samples = [mono]
        elif ch == 1:
            samples = [list(samples[0]) for _ in range(channels)]
        else:
            while len(samples) < channels:
                samples.append(list(samples[len(samples) % ch]))
            samples = samples[:channels]
        ch = channels
    if rate is not None and rate != audio.rate:
        n_out = max(1, int(round(audio.frames * rate / float(audio.rate))))
        out = []
        for c in samples:
            nc = []
            for i in range(n_out):
                t = i * (audio.frames - 1) / float(max(1, n_out - 1))
                i0 = int(t)
                i1 = min(audio.frames - 1, i0 + 1)
                f = t - i0
                nc.append(c[i0] * (1 - f) + c[i1] * f)
            out.append(nc)
        samples = out
        audio_rate = rate
    else:
        audio_rate = rate or audio.rate
    return Audio(audio_rate, ch, samples)


def normalize(audio, peak_db=-1.0):
    peak = 0.0
    for c in audio.samples:
        for v in c:
            a = abs(v)
            if a > peak:
                peak = a
    if peak == 0:
        return audio, {"peak_before": 0.0, "gain": 0.0, "note": "silent"}
    target = 10 ** (peak_db / 20.0)
    gain = target / peak
    return (Audio(audio.rate, audio.channels,
                  [[max(-1.0, min(1.0, v * gain)) for v in c] for c in audio.samples]),
            {"peak_before": round(peak, 5), "gain": round(gain, 5),
             "peak_after": round(peak * gain, 5)})


def gain_fade(audio, gain_db=0.0, fade_in_ms=0, fade_out_ms=0):
    g = 10 ** (gain_db / 20.0)
    n = audio.frames
    fi = int(audio.rate * fade_in_ms / 1000.0)
    fo = int(audio.rate * fade_out_ms / 1000.0)
    out = []
    for c in audio.samples:
        nc = []
        for i, v in enumerate(c):
            f = g
            if fi > 0 and i < fi:
                f *= i / float(fi)
            if fo > 0 and i > n - fo:
                f *= (n - i) / float(fo)
            nc.append(max(-1.0, min(1.0, v * f)))
        out.append(nc)
    return Audio(audio.rate, audio.channels, out)


def mix(stems, gains=None, out_rate=None):
    """Sum stems into a review mix (Scratch Track Assembler core)."""
    _init()
    require(stems, "invalid_input", "no stems to mix")
    gains = gains or [1.0] * len(stems)
    require(len(gains) == len(stems), "invalid_input", "gains/stem count mismatch")
    rate = out_rate or max(s.rate for s in stems)
    stems = [convert(s, rate=rate, channels=2) for s in stems]
    n = max(s.frames for s in stems)
    mix_c = [[0.0] * n for _ in range(2)]
    for s, g in zip(stems, gains):
        for c in range(min(2, s.channels)):
            for i, v in enumerate(s.samples[c]):
                mix_c[c][i] += v * g
    peak = max(max(abs(v) for v in c) for c in mix_c) or 1.0
    if peak > 1.0:
        k = 0.99 / peak
        mix_c = [[v * k for v in c] for c in mix_c]
    return Audio(rate, 2, mix_c)


def trim(audio, start_ms, end_ms):
    s = int(start_ms * audio.rate / 1000.0)
    e = int(end_ms * audio.rate / 1000.0) if end_ms is not None else audio.frames
    s, e = max(0, s), min(audio.frames, e)
    require(e > s, "invalid_input", "empty trim range")
    return Audio(audio.rate, audio.channels, [c[s:e] for c in audio.samples])


# -------------------------------------------------------------- analysis ----

def levels(audio):
    peaks = []
    rms = []
    for c in audio.samples:
        p = 0.0
        s = 0.0
        for v in c:
            a = abs(v)
            if a > p:
                p = a
            s += v * v
        peaks.append(p)
        rms.append(math.sqrt(s / max(1, len(c))))
    def db(v):
        return round(20 * math.log10(v), 2) if v > 0 else -float("inf")
    clip = sum(1 for c in audio.samples for v in c if abs(v) >= 0.999)
    silence = sum(1 for c in audio.samples for v in c if abs(v) < 0.0005)
    n = audio.frames * audio.channels
    return {"sample_rate": audio.rate, "channels": audio.channels,
            "frames": audio.frames, "duration_s": round(audio.duration, 3),
            "peak": [round(p, 5) for p in peaks], "peak_db": [db(p) for p in peaks],
            "rms": [round(r, 5) for r in rms], "rms_db": [db(r) for r in rms],
            "clipped_samples": clip, "silent_samples": silence,
            "channel_imbalance_db": round(abs(db(peaks[0]) - db(peaks[-1])), 2)
            if len(peaks) > 1 else 0.0,
            "dc_offset": round(sum(c[0] for c in audio.samples) / max(1, audio.frames), 5)}


def mono_envelope(audio, hop_ms=10):
    """Mono energy envelope at hop resolution (analysis core)."""
    ch = audio.channels
    n = audio.frames
    hop = max(1, int(audio.rate * hop_ms / 1000.0))
    env = []
    for i in range(0, n, hop):
        s = 0.0
        cnt = 0
        for j in range(i, min(n, i + hop)):
            for c in range(ch):
                s += audio.samples[c][j] ** 2
                cnt += 1
        env.append(math.sqrt(s / max(1, cnt)))
    return env, hop


def detect_silence(audio, threshold=0.01, min_ms=250):
    env, hop = mono_envelope(audio, hop_ms=10)
    ms_per = 10
    regions = []
    start = None
    for i, v in enumerate(env):
        if v < threshold and start is None:
            start = i * ms_per
        elif v >= threshold and start is not None:
            if (i * ms_per - start) >= min_ms:
                regions.append([start, i * ms_per])
            start = None
    if start is not None and (len(env) * ms_per - start) >= min_ms:
        regions.append([start, len(env) * ms_per])
    return {"silence_regions_ms": regions,
            "silence_threshold": threshold,
            "total_silent_ms": sum(b - a for a, b in regions)}


def detect_onsets(audio, threshold=0.12, min_gap_ms=60):
    """Energy-rise onsets: word/impact/footstep candidates (frame-accurate
    after fps conversion)."""
    env, hop = mono_envelope(audio, hop_ms=5)
    if not env:
        return {"onsets_ms": []}
    onsets = []
    last = -1e9
    for i in range(1, len(env)):
        prev = max(env[max(0, i - 6):i]) if i > 0 else 0
        if env[i] > prev + threshold and env[i] > 0.02 and \
                (i * 5 - last) >= min_gap_ms:
            onsets.append(i * 5)
            last = i * 5
    return {"onsets_ms": onsets, "count": len(onsets), "hop_ms": 5,
            "threshold": threshold}


def estimate_beats(audio, bpm_range=(70, 180)):
    """Beat grid estimate via autocorrelation of the onset envelope."""
    env, hop = mono_envelope(audio, hop_ms=10)
    if len(env) < 20:
        return {"beats_ms": [], "bpm": None}
    flux = [max(0.0, env[i] - env[i - 1]) for i in range(1, len(env))]
    n = len(flux)
    best_bpm = None
    best_score = -1.0
    for bpm in range(bpm_range[0], bpm_range[1] + 1):
        lag_ms = 60000.0 / bpm
        lag = int(round(lag_ms / 10.0))
        if lag < 2 or lag >= n // 2:
            continue
        score = 0.0
        for i in range(n - lag):
            score += flux[i] * flux[i + lag]
        score /= (n - lag)
        if score > best_score:
            best_score, best_bpm = score, bpm
    if best_bpm is None:
        return {"beats_ms": [], "bpm": None}
    period = 60000.0 / best_bpm
    beats = []
    if flux:
        first = max(range(len(flux)), key=lambda i: flux[i])
        t = first * 10
        while t < audio.duration * 1000 - period:
            beats.append(int(t))
            t += period
    return {"beats_ms": beats, "bpm": best_bpm, "period_ms": round(period, 2),
            "bars": int(len(beats) // 4), "confidence": "autocorrelation estimate; verify by ear"}


def beat_sheet(audio, bpm=None):
    """Musical timing sheet for animation (Beat Sheet Extractor)."""
    _init()
    est = estimate_beats(audio) if bpm is None else {"bpm": bpm, "beats_ms": [],
                                                     "period_ms": round(60000.0 / bpm, 2)}
    bpm = est.get("bpm")
    if not bpm:
        return {"bpm": None, "beats": [], "note": "no stable tempo found"}
    period = est["period_ms"]
    beats = est.get("beats_ms") or list(range(0, int(audio.duration * 1000), int(period)))
    sheet = []
    bar = 1
    for i, ms in enumerate(beats):
        if i and i % 4 == 0:
            bar += 1
        emphasis = "downbeat" if i % 4 == 0 else "beat"
        sheet.append({"beat": i + 1, "bar": bar, "ms": ms, "kind": emphasis})
    return {"bpm": bpm, "period_ms": period, "beats": sheet,
            "note": "derive walk/run cycles and impacts from these beats"}


def audio_to_frame_events(events_ms, fps=24, kind="onset"):
    """Convert millisecond events to frame-accurate markers."""
    out = []
    for ms in events_ms:
        out.append({"ms": ms, "frame": int(round(ms * fps / 1000.0)), "kind": kind})
    return out


def drift_check(audio, expected_events_ms, fps=24, tolerance_ms=40):
    """Reconciler: audio length vs frame grid and event alignment."""
    dur_ms = audio.duration * 1000
    audio_events = detect_onsets(audio).get("onsets_ms", [])
    pairs = []
    for e in expected_events_ms:
        near = min(audio_events, key=lambda a: abs(a - e)) if audio_events else None
        pairs.append({"expected_ms": e, "nearest_audio_ms": near,
                      "drift_ms": (None if near is None else round(near - e, 1)),
                      "in_tolerance": near is not None and abs(near - e) <= tolerance_ms})
    frames = int(math.ceil(dur_ms * fps / 1000.0))
    return {"duration_ms": round(dur_ms, 1), "frames_at_fps": frames, "fps": fps,
            "pairs": pairs,
            "unmatched": sum(1 for p in pairs if not p["in_tolerance"]),
            "note": "sample-rate/timeline drift shows as growing drift_ms"}


# ------------------------------------------------------------- proxies ------

def proxy(audio, rate=22050, channels=1, bits=16):
    """Lightweight scrub proxy; the production master stays untouched."""
    a = convert(audio, rate=rate, channels=channels)
    return a


def scrub_peaks(audio, buckets=500):
    """Peak-per-bucket waveform data for offline scrub UIs."""
    n = audio.frames
    size = max(1, n // buckets)
    peaks = []
    for b in range(buckets):
        s = b * size
        if s >= n:
            break
        p = 0.0
        for j in range(s, min(n, s + size)):
            for c in audio.samples:
                a = abs(c[j])
                if a > p:
                    p = a
        peaks.append(round(p, 4))
    return {"peaks": peaks, "buckets": len(peaks),
            "ms_per_bucket": round(audio.duration * 1000 / max(1, len(peaks)), 2)}


def audio_qa(path, fps=24):
    """Audio Peak and Clipping Checker + drift reconciliation."""
    _init()
    audio, meta = load_audio(path)
    lv = levels(audio)
    issues = []
    if lv["clipped_samples"] > 0:
        issues.append({"issue": "clipping", "detail": "%d samples at or above full scale"
                       % lv["clipped_samples"]})
    if lv["channels"] > 1 and lv["channel_imbalance_db"] > 3:
        issues.append({"issue": "channel_imbalance",
                       "detail": "%.1f dB between channels" % lv["channel_imbalance_db"]})
    if lv["rms_db"][0] < -40:
        issues.append({"issue": "very_quiet", "detail": "RMS %.1f dB" % lv["rms_db"][0]})
    silent = detect_silence(audio)
    if silent["total_silent_ms"] > audio.duration * 500:
        issues.append({"issue": "mostly_silence",
                       "detail": "%d ms silent" % silent["total_silent_ms"]})
    return {**meta, **lv, "issues": issues,
            "frames_at_fps": int(math.ceil(audio.duration * fps)),
            "verdict": "pass" if not issues else "%d issue(s)" % len(issues)}


# ------------------------------------------------------------- ingest -------

def ingest(path, out_dir, fps=24):
    """Offline Audio Ingestor: edit-safe copy + normalized naming + provenance."""
    _init()
    from pathlib import Path as P
    src = P(str(path))
    require(src.is_file(), "invalid_input", "audio file not found: %s" % src)
    out_dir = P(str(out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)
    name = core.slugify(src.stem) + src.suffix.lower()
    dst = out_dir / name
    core.write_bytes(str(dst), core.read_bytes(str(src)))
    audio, meta = load_audio(str(dst))
    return {"copy": str(dst), "original": str(src), "meta": meta,
            "levels": levels(audio), "frames_at_fps": int(math.ceil(audio.duration * fps)),
            "edit_safe": "original untouched; edit the copy"}


def sfx_library(directory, pattern="*"):
    """Index local sound effects: duration, loudness, tags from filenames."""
    _init()
    from pathlib import Path as P
    d = P(str(directory))
    require(d.is_dir(), "invalid_input", "not a directory: %s" % d)
    rows = []
    for f in sorted(d.glob(str(pattern))):
        if not f.is_file():
            continue
        row = {"file": f.name, "bytes": f.stat().st_size}
        if f.suffix.lower() in (".wav", ".aif", ".aiff"):
            try:
                a, _ = load_audio(str(f))
                lv = levels(a)
                row.update({"duration_s": round(a.duration, 3),
                            "peak_db": lv["peak_db"][0], "rms_db": lv["rms_db"][0],
                            "rate": a.rate, "channels": a.channels})
            except Exception as e:
                row["error"] = getattr(e, "code", "undecodable")
        else:
            row["needs_ffmpeg"] = True
        rows.append(row)
    return {"directory": str(d), "count": len(rows), "files": rows[:500],
            "hint": "add 'license' and 'tags' fields per file in your registry"}


def audio_conform(scratch_mix, final_stems, out_path, gains=None):
    """Final Audio Conform: replace temporary stems with finals while
    preserving duration and sync points."""
    _init()
    scratch, _ = load_audio(scratch_mix)
    stems = []
    for s in final_stems:
        a, _ = load_audio(s)
        stems.append(a)
    mixed = mix(stems, gains=gains or [1.0] * len(stems), out_rate=scratch.rate)
    require(abs(mixed.duration - scratch.duration) < 0.05, "sync_mismatch",
            "final mix is %.3fs vs approved %.3fs — conform aborted"
            % (mixed.duration, scratch.duration))
    core.write_bytes(str(out_path), encode_wav(mixed, bits=24))
    return {"path": str(out_path), "duration_s": round(mixed.duration, 3),
            "matched_scratch": True, "stems": len(stems)}


# --------------------------------------------- dialogue craft (14.2/14.3) ---

def dialogue_cleanup(path, out_path, gate_threshold=0.01, floor=0.02,
                     target_peak_db=-1.0, pad_ms=80):
    """Dialogue Cleanup Operator: trim edge silence, gate quiet noise
    between phrases, soften clicks, normalize to a headroom-safe peak.
    Non-destructive: writes a new file, original untouched."""
    _init()
    audio, meta = load_audio(path)
    dur_ms = audio.frames * 1000 / audio.rate
    sil = detect_silence(audio, threshold=gate_threshold, min_ms=120)
    # content bounds from the envelope (before gating)
    env, _ = mono_envelope(audio, hop_ms=10)
    loud = [i * 10 for i, v in enumerate(env) if v >= gate_threshold]
    first_ms = loud[0] if loud else 0
    last_ms = loud[-1] if loud else dur_ms
    # gate: attenuate samples inside silence spans
    for s, e in sil["silence_regions_ms"]:
        a = int(s * audio.rate / 1000)
        b = min(audio.frames, int(e * audio.rate / 1000))
        for c in range(audio.channels):
            sm = audio.samples[c]
            for i in range(a, b):
                sm[i] = sm[i] * floor
    # de-click: tame isolated single-sample jumps
    for c in range(audio.channels):
        sm = audio.samples[c]
        for i in range(1, len(sm) - 1):
            if abs(sm[i] - sm[i - 1]) > 0.9 and abs(sm[i + 1] - sm[i]) > 0.9:
                sm[i] = (sm[i - 1] + sm[i + 1]) / 2.0
    trimmed = trim(audio, max(0, first_ms - pad_ms),
                   min(dur_ms, last_ms + pad_ms))
    final, info = normalize(trimmed, peak_db=target_peak_db)
    core.write_bytes(out_path, encode_wav(final, bits=24))
    lv = levels(final)
    return {"out": str(out_path), "rate": final.rate,
            "channels": final.channels, "frames": final.frames,
            "duration_ms": int(final.frames * 1000 / final.rate),
            "gated_spans": len(sil["silence_regions_ms"]),
            "trimmed_ms": int(dur_ms - final.frames * 1000 / final.rate),
            "peak": max(lv["peak"]), "rms": max(lv["rms"]),
            "clipped_samples": lv["clipped_samples"],
            "original": str(path)}


def cue_markers(path, fps=24):
    """Waveform Cue Marker: onsets + silences as named, frame-accurate cues
    for the timeline."""
    _init()
    audio, meta = load_audio(path)
    ons = detect_onsets(audio).get("onsets_ms", [])
    sil = detect_silence(audio, threshold=0.01, min_ms=200)
    cues = [{"name": "onset-%03d" % (i + 1), "ms": int(ms), "kind": "onset"}
            for i, ms in enumerate(ons)]
    for j, (s, e) in enumerate(sil["silence_regions_ms"]):
        cues.append({"name": "silence-%03d" % (j + 1), "ms": int(s),
                     "kind": "silence", "end_ms": int(e)})
    cues.sort(key=lambda c: c["ms"])
    for c in cues:
        c["frame"] = int(round(c["ms"] * fps / 1000.0))
    return {"cues": cues, "count": len(cues), "fps": fps,
            "duration_ms": int(audio.frames * 1000 / audio.rate)}


# ------------------------------------------------------ scratch (14.6) ------

def scratch_assemble(stems, out_path, gains=None, lead_index=0):
    """Scratch Track Assembler: mix stems with the dialogue stem loudest,
    write a 16-bit review WAV, report per-stem levels."""
    _init()
    require(stems, "invalid_input", "no stems given")
    g = list(gains or [0.8] * len(stems))
    g[int(lead_index)] = max(g)
    mixed = mix(stems, gains=g)
    core.write_bytes(out_path, encode_wav(mixed, bits=16))
    rows = []
    for i, s in enumerate(stems):
        lv = levels(s)
        rows.append({"stem": i, "gain": round(g[i], 3),
                     "peak": max(lv["peak"]), "rms": max(lv["rms"])})
    return {"out": str(out_path), "rate": mixed.rate,
            "frames": mixed.frames, "stems": rows,
            "duration_ms": int(mixed.frames * 1000 / mixed.rate)}
