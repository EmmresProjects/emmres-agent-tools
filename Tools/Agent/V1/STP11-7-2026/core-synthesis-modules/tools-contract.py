#!/usr/bin/env python3
"""tools-contract.py — contract tests and the acceptance gate for the
core-synthesis-modules Skill Tool.

Implements the two remaining capabilities from catalog category "Core
Synthesis Scripts and Modules":
  * Core Module Contract Tester  — per-module contract tests (run with
    `python3 tools-voice.py contract-test`)
  * Core Synthesis Acceptance Gate — the full offline acceptance run
    (run with `python3 tools-voice.py acceptance`)

The acceptance gate verifies, for the required greeting sample:
  1. offline guard (no network code in the tool)
  2. required phrase + preset rules planned (pause after "friends",
     emphasis on "welcome")
  3. synthesis through the provider router (ONNX preferred)
  4. WAV reopen validation
  5. duration within 2.5-4 s
  6. friends-pause present in audio (silence gap >= 200 ms)
  7. welcome emphasis (gain >= 1.2 metadata + RMS boost in audio)
  8. peak at or below -1 dBFS and loudness near -14 LUFS
  9. determinism (second run -> identical sha256)
 10. human listening checks are reported as REQUIRED-HUMAN-REVIEW (they
     cannot be automated and are never claimed as passed)
"""

import importlib.util as _ilu
import math
import os
import sys as _sys


def _load_sibling(name):
    """Load a sibling tools-*.py module by file (filenames contain hyphens,
    so plain `import` syntax is not possible). Results are cached in
    sys.modules under a private key."""
    key = "voice-skill-" + name
    mod = _sys.modules.get(key)
    if mod is None:
        here = os.path.dirname(os.path.abspath(__file__))
        spec = _ilu.spec_from_file_location(key, os.path.join(here, name + ".py"))
        mod = _ilu.module_from_spec(spec)
        _sys.modules[key] = mod
        spec.loader.exec_module(mod)
    return mod


_audio = _load_sibling("tools-audio")
_core = _load_sibling("tools-core")
_providers = _load_sibling("tools-providers")

integrated_loudness = _audio.integrated_loudness
master = _audio.master
peak_dbfs = _audio.peak_dbfs
read_wav = _audio.read_wav
wav_header_info = _audio.wav_header_info
write_wav = _audio.write_wav
VoicePresets = _core.VoicePresets
chunk_text = _core.chunk_text
load_config = _core.load_config
load_json = _core.load_json
plan_utterance = _core.plan_utterance
sha256_file = _core.sha256_file
skill_root = _core.skill_root
ProviderRouter = _providers.ProviderRouter
Tokenizer = _core.Tokenizer
Phonemizer = _core.Phonemizer
SkillToolError = _core.SkillToolError
ModelLoader = _core.ModelLoader
limit_peak = _audio.limit_peak


# --------------------------------------------------------------------------
# Contract tests
# --------------------------------------------------------------------------

def _result(name, ok, detail="", skipped=False):
    return {"test": name, "ok": bool(ok), "detail": detail,
            "status": "skipped" if skipped else ("passed" if ok else "failed")}


def test_config_loader():
    cfg = load_config()
    errs = [cfg["preset_default"]]
    ok = isinstance(cfg["providers"]["order"], list) and cfg["audio"]["sample_rate"] > 0
    return _result("config-loader", ok, "preset_default=%s" % errs[0])


def test_tokenizer():
    tok = Tokenizer()
    ids = tok.encode("Hello world")
    assert len(ids) == 11
    assert tok.decode(ids) == "Hello world"
    ids2, spans = tok.encode_with_spans("Hi there")
    assert ids2 and spans[0][0] == 0
    # word indexing: "Hi" -> word 0, "there" -> word 1
    w1 = [s for s in spans if s[0] == 1]
    assert w1 and w1[0][1] == 3
    return _result("tokenizer", True, "vocab=%d chars" % tok.vocab_size)


def test_phonemizer():
    ph = Phonemizer()
    seq = ph.phonemize("hello")
    assert seq and "HH" in seq
    # acronym expansion
    expanded, applied = ph.apply_dictionary("NASA and TTS work offline")
    assert "N A S A" in expanded or "n a s a" in expanded.lower()
    assert applied
    return _result("phonemizer", True, "phonemes=%d" % len(seq))


def test_pronunciation():
    ph = Phonemizer()
    expanded, applied = ph.apply_dictionary("The GPU and CPU are NASA partners")
    low = expanded.lower()
    assert "n a s a" in low
    assert any(a["rule"] == "acronym" for a in applied)
    return _result("pronunciation", True, "applied=%d" % len(applied))


def test_voice_presets():
    presets = VoicePresets()
    name = "friendly-male-30s"
    assert name in presets.list()
    errors = presets.validate(presets.get(name))
    assert not errors, errors
    return _result("voice-presets", True, "presets=%s" % ", ".join(presets.list()))


def test_sentence_chunker():
    text = ("Sentence one is short. Sentence two is also short. "
            "Sentence three is a bit longer but still fine.")
    chunks = chunk_text(text, max_chars=60)
    assert chunks
    assert all(len(c) <= 60 for c in chunks)
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")
    return _result("sentence-chunker", True, "chunks=%d" % len(chunks))


def test_prosody_planner():
    presets = VoicePresets()
    preset = presets.get("friendly-male-30s")
    plan = plan_utterance("Greetings, friends! Welcome to my video.", preset)
    words = plan["words"]
    assert any(w["text"].lower().strip(".,!?") == "friends"
               and w["pause_after_ms"] >= 200 for w in words)
    assert any(w["text"].lower().strip(".,!?") == "welcome"
               and w["gain"] >= 1.2 for w in words)
    return _result("prosody-planner", True, "planned_ms=%d" % plan["total_ms"])


def test_model_loader():
    loader = ModelLoader()
    caps = loader.capabilities()
    info = loader.resolve("onnx")
    verify = loader.verify(info)
    ok = verify["ok"]
    if not ok:
        return _result("model-loader", False,
                       "checksum mismatch: expected %s got %s"
                       % (info["expected_sha256"], verify["actual_sha256"]))
    return _result("model-loader", True, "onnx model sha256 verified")


def test_wav_writer():
    import tempfile
    tmp = tempfile.mkdtemp(prefix="voice-contract-")
    path = os.path.join(tmp, "roundtrip.wav")
    samples = [math.sin(2 * math.pi * 440 * i / 22050) * 0.5 for i in range(22050)]
    write_wav(path, samples, 22050)
    data = read_wav(path)
    assert data["sample_rate"] == 22050
    assert abs(len(data["samples"]) - 22050) <= 1
    header = wav_header_info(path)
    assert header["valid"] and header["sample_width"] == 2
    return _result("wav-writer", True, "roundtrip OK, %d frames" % len(data["samples"]))


def test_loudness_meter():
    # 1 kHz sine at -20 dBFS should measure around -23.3 LUFS (ITU K-weight)
    n = 48000
    samples = [math.sin(2 * math.pi * 1000 * i / 48000) * 0.1 for i in range(n)]
    lufs = integrated_loudness(samples, 48000)
    ok = abs(lufs - (-23.3)) < 0.8
    return _result("loudness-meter", ok, "1 kHz @ -20 dBFS -> %.2f LUFS" % lufs)


def test_peak_limiter():
    samples = [1.5, -1.2, 0.5, -0.1]
    out, gain = limit_peak(samples, -1.0)
    ok = peak_dbfs(out) <= -1.0 + 1e-6 and gain < 1.0
    return _result("peak-limiter", ok, "limited peak=%.2f dBFS" % peak_dbfs(out))


def test_stub_provider():
    cfg = load_config()
    router = ProviderRouter(cfg)
    preset = VoicePresets().get(cfg["preset_default"])
    plan = plan_utterance("Hello world.", preset)
    samples, meta, used, warnings = router.synthesize(
        "Hello world.", plan, preset, cfg["audio"], preference="stub")
    ok = used == "stub" and meta.get("quality_warning") and len(samples) > 0
    return _result("stub-provider", ok, "used=%s samples=%d" % (used, len(samples)))


def test_onnx_provider():
    try:
        cfg = load_config()
        router = ProviderRouter(cfg)
        health = router.health()
        if not health.get("onnx", {}).get("available"):
            return _result("onnx-provider", True,
                           "skipped: onnxruntime or model unavailable in this environment",
                           skipped=True)
        preset = VoicePresets().get(cfg["preset_default"])
        plan = plan_utterance("Hello offline neural world.", preset)
        samples, meta, used, warnings = router.synthesize(
            "Hello offline neural world.", plan, preset, cfg["audio"],
            preference="onnx")
        ok = used == "onnx" and len(samples) > 0 and meta.get("inference_seconds") is not None
        return _result("onnx-provider", ok,
                       "used=%s tokens=%d inference=%.4fs"
                       % (used, meta.get("tokens", 0), meta.get("inference_seconds", 0)))
    except Exception as exc:  # pragma: no cover
        return _result("onnx-provider", False, "error: %s" % exc)


def test_piper_provider():
    cfg = load_config()
    router = ProviderRouter(cfg)
    health = router.health()
    piper = health.get("piper", {})
    # The contract under test is the health reporting: structure must be
    # present whether or not the optional binary is installed.
    ok = ("available" in piper and "reason" in piper)
    detail = "available=%s reason=%s" % (piper.get("available"), piper.get("reason"))
    if not ok:
        return _result("piper-provider", False, detail)
    if not piper.get("available"):
        return _result("piper-provider", True, detail, skipped=True)
    return _result("piper-provider", True, detail)


def test_provider_router():
    cfg = load_config()
    router = ProviderRouter(cfg)
    preset = VoicePresets().get(cfg["preset_default"])
    plan = plan_utterance("Router fallback test.", preset)
    # Force preference to a provider that must exist (stub is always available).
    samples, meta, used, warnings = router.synthesize(
        "Router fallback test.", plan, preset, cfg["audio"], preference="stub")
    ok = used == "stub"
    # Determinism: same input -> identical bytes.
    import tempfile
    tmp = tempfile.mkdtemp(prefix="voice-router-")
    p1 = os.path.join(tmp, "a.wav")
    p2 = os.path.join(tmp, "b.wav")
    write_wav(p1, samples, cfg["audio"]["sample_rate"])
    samples2, _, _, _ = router.synthesize("Router fallback test.", plan, preset,
                                         cfg["audio"], preference="stub")
    write_wav(p2, samples2, cfg["audio"]["sample_rate"])
    det = sha256_file(p1) == sha256_file(p2)
    return _result("provider-router", ok and det,
                   "used=%s deterministic=%s" % (used, det))


def test_structured_errors():
    err = SkillToolError("INPUT_VALIDATION", "bad input", {"field": "text"})
    d = err.to_dict()
    ok = (d["error"]["code"] == "INPUT_VALIDATION"
          and d["error"]["exit_code"] == 3
          and "registry" in d)
    return _result("structured-errors", ok, "exit_code=%d" % d["error"]["exit_code"])


def test_offline_guard():
    """Static check: the tool never imports or calls network facilities."""
    root = skill_root()
    forbidden_imports = {"socket", "urllib", "urllib2", "urllib3", "http",
                         "requests", "ftplib", "telnetlib", "smtplib", "ssl",
                         "asyncio"}
    problems = []
    for fname in os.listdir(root):
        if not (fname.startswith("tools-") and fname.endswith(".py")):
            continue
        with open(os.path.join(root, fname), "r", encoding="utf-8") as fh:
            src = fh.read()
        for word in forbidden_imports:
            if re_search_import(src, word):
                problems.append("%s imports %s" % (fname, word))
    ok = not problems
    return _result("offline-guard", ok, "; ".join(problems) if problems else "no network imports")


def re_search_import(src, word):
    import re
    return bool(re.search(r"^\s*(import|from)\s+%s\b" % re.escape(word), src, re.M))


def run_all(verbose=False):
    tests = [
        test_config_loader,
        test_tokenizer,
        test_phonemizer,
        test_pronunciation,
        test_voice_presets,
        test_sentence_chunker,
        test_prosody_planner,
        test_model_loader,
        test_wav_writer,
        test_loudness_meter,
        test_peak_limiter,
        test_stub_provider,
        test_onnx_provider,
        test_piper_provider,
        test_provider_router,
        test_structured_errors,
        test_offline_guard,
    ]
    results = []
    for fn in tests:
        try:
            r = fn()
        except Exception as exc:
            r = _result(fn.__name__.replace("test_", ""), False,
                        "exception: %r" % exc)
        results.append(r)
    passed = sum(1 for r in results if r["ok"] and not r.get("skipped"))
    skipped = sum(1 for r in results if r.get("skipped"))
    failed = len(results) - passed - skipped
    summary = {"total": len(results), "passed": passed,
               "failed": failed, "skipped": skipped,
               "ok": failed == 0, "results": results}
    if verbose:
        for r in results:
            status = "SKIP" if r.get("skipped") else ("PASS" if r["ok"] else "FAIL")
            print("%-22s %s  %s" % (r["test"], status, r["detail"]))
    return summary


# --------------------------------------------------------------------------
# Acceptance gate
# --------------------------------------------------------------------------

def _silence_gap_ms(samples, sample_rate, start_ms, end_ms, threshold_db=-45.0):
    """Measure the longest silence run inside [start_ms, end_ms)."""
    start = int(start_ms / 1000.0 * sample_rate)
    end = int(end_ms / 1000.0 * sample_rate)
    start = max(0, start)
    end = min(len(samples), end)
    best = 0.0
    run = 0.0
    for i in range(start, end):
        s = abs(samples[i])
        if s < 10 ** (threshold_db / 20.0):
            run += 1.0
            best = max(best, run)
        else:
            run = 0.0
    return best / sample_rate * 1000.0


def _region_rms_dbfs(samples, sample_rate, start_ms, end_ms):
    start = int(start_ms / 1000.0 * sample_rate)
    end = int(end_ms / 1000.0 * sample_rate)
    seg = samples[start:end]
    if not seg:
        return -120.0
    m = sum(s * s for s in seg) / len(seg)
    return 10.0 * math.log10(m) if m > 0 else -120.0


def acceptance_gate(cfg, out_dir=None, preference=None):
    """Run the full acceptance flow. Returns a report dict (never raises for
    failed checks — failed checks are recorded in the report)."""
    phrases = load_json(os.path.join(skill_root(), "asset-acceptance-phrases.json"))
    required = phrases["required_phrase"]
    checks = phrases["checks"]
    out_dir = out_dir or os.path.join(skill_root(), "output", "acceptance")
    os.makedirs(out_dir, exist_ok=True)

    presets = VoicePresets()
    preset = presets.get(cfg["preset_default"])
    plan = plan_utterance(required, preset)

    # 1. offline guard
    offline = test_offline_guard()

    # 2. plan checks
    plan_words = plan["words"]
    friends = next((w for w in plan_words
                 if w["text"].lower().strip(".,!?") == "friends"), None)
    welcome = next((w for w in plan_words
                 if w["text"].lower().strip(".,!?") == "welcome"), None)
    plan_ok = (friends is not None and friends["pause_after_ms"] >= checks["friends_pause_ms"]["min"]
               and welcome is not None and welcome["gain"] >= checks["welcome_emphasis_gain"]["min"])

    # 3. synthesis through the router
    router = ProviderRouter(cfg)
    deadline = ProviderRouter.make_deadline(cfg["limits"]["synthesis_timeout_seconds"])
    try:
        raw_samples, meta, used, warnings = router.synthesize(
            required, plan, preset, cfg["audio"], preference=preference,
            deadline=deadline)
        # Delivery requires the mastered chain (DC removal -> loudness -> peak).
        samples, mastering_report = master(
            raw_samples, cfg["audio"]["sample_rate"],
            target_lufs=cfg["audio"]["target_lufs"],
            peak_dbfs_target=cfg["audio"]["target_peak_dbfs"],
            normalize=cfg["audio"].get("loudness_normalization", True))
        synth_ok = True
        synth_error = None
    except Exception as exc:
        samples, meta, used, warnings = [], {}, None, []
        mastering_report = None
        synth_ok = False
        synth_error = str(exc)

    # 4. WAV reopen
    wav_path = os.path.join(out_dir, "greeting.wav")
    if synth_ok:
        write_wav(wav_path, samples, cfg["audio"]["sample_rate"])
    reopen_ok = False
    reopen_detail = ""
    if synth_ok:
        try:
            header = wav_header_info(wav_path)
            data = read_wav(wav_path)
            reopen_ok = header["valid"] and len(data["samples"]) > 0
            reopen_detail = "frames=%d" % header["frames"]
        except Exception as exc:
            reopen_detail = "reopen failed: %r" % exc

    # 5-8. audio checks
    duration_ms = len(samples) / cfg["audio"]["sample_rate"] * 1000.0 if synth_ok else 0.0
    dur_ok = checks["duration_ms"]["min"] <= duration_ms <= checks["duration_ms"]["max"]

    friends_gap = _silence_gap_ms(samples, cfg["audio"]["sample_rate"],
                                  friends["end_ms"], friends["end_ms"] + 400,
                                  ) if (synth_ok and friends) else 0.0
    gap_ok = friends_gap >= checks["friends_pause_ms"]["min"]

    welcome_gain = welcome["gain"] if welcome else 0.0
    gain_ok = welcome_gain >= checks["welcome_emphasis_gain"]["min"]
    rms_welcome = _region_rms_dbfs(samples, cfg["audio"]["sample_rate"],
                                   welcome["start_ms"], welcome["end_ms"]) if synth_ok else -120.0
    rms_prev = -120.0
    if synth_ok and len(plan_words) > 1:
        idx = plan_words.index(welcome)
        if idx > 0:
            prev = plan_words[idx - 1]
            rms_prev = _region_rms_dbfs(samples, cfg["audio"]["sample_rate"],
                                        prev["start_ms"], prev["end_ms"])
    rms_boost_ok = rms_welcome > rms_prev

    peak = peak_dbfs(samples) if synth_ok else -120.0
    peak_ok = peak <= cfg["audio"]["target_peak_dbfs"] + 1e-6
    lufs = integrated_loudness(samples, cfg["audio"]["sample_rate"]) if synth_ok else -120.0
    lufs_ok = abs(lufs - cfg["audio"]["target_lufs"]) <= 1.5

    # 9. determinism
    det_ok = False
    det_detail = ""
    if synth_ok:
        raw2, _, _, _ = router.synthesize(required, plan, preset, cfg["audio"],
                                         preference=preference, deadline=deadline)
        samples2, _ = master(raw2, cfg["audio"]["sample_rate"],
                             target_lufs=cfg["audio"]["target_lufs"],
                             peak_dbfs_target=cfg["audio"]["target_peak_dbfs"],
                             normalize=cfg["audio"].get("loudness_normalization", True))
        path2 = os.path.join(out_dir, "greeting-repeat.wav")
        write_wav(path2, samples2, cfg["audio"]["sample_rate"])
        det_ok = sha256_file(wav_path) == sha256_file(path2)
        det_detail = "sha256 identical=%s" % det_ok

    # 10. human listening checks
    listening = [
        {"check": "warmth_listening", "status": "REQUIRED-HUMAN-REVIEW"},
        {"check": "friendliness_listening", "status": "REQUIRED-HUMAN-REVIEW"},
        {"check": "robotic_buzz_listening", "status": "REQUIRED-HUMAN-REVIEW"},
        {"check": "naturalness_listening", "status": "REQUIRED-HUMAN-REVIEW"},
    ]

    automated = {
        "offline_guard": offline["ok"],
        "plan_rules": plan_ok,
        "synthesis": synth_ok,
        "wav_reopen": reopen_ok,
        "duration_2.5_to_4s": dur_ok,
        "friends_pause_present": gap_ok,
        "welcome_emphasis": gain_ok and rms_boost_ok,
        "peak_at_or_below_-1_dbfs": peak_ok,
        "loudness_near_-14_lufs": lufs_ok,
        "deterministic_output": det_ok,
    }
    failed = [k for k, v in automated.items() if not v]
    report = {
        "acceptance": "core-synthesis-acceptance-gate",
        "required_phrase": required,
        "preset": cfg["preset_default"],
        "provider_used": used,
        "quality_warnings": warnings,
        "duration_ms": round(duration_ms, 1),
        "peak_dbfs": round(peak, 2),
        "loudness_lufs": round(lufs, 2),
        "friends_pause_ms_measured": round(friends_gap, 1),
        "welcome_gain": welcome_gain,
        "welcome_rms_dbfs": round(rms_welcome, 2),
        "previous_word_rms_dbfs": round(rms_prev, 2),
        "mastering": mastering_report,
        "determinism": det_detail,
        "synthesis_error": synth_error,
        "automated_checks": automated,
        "failed_checks": failed,
        "listening_checks": listening,
        "listening_note": "Human listening checks cannot be automated; they are "
                          "reported as REQUIRED-HUMAN-REVIEW and are never "
                          "claimed as passed.",
        "wav_path": wav_path,
        "passed": not failed,
    }
    return report
