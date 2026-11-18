# Third-Party Notices and Licenses — Offline Neural Voice Skill Tool

This folder bundles runtime components so the Skill Tool works fully offline.
Every bundled file is listed here. Do not remove these notices.

## This Skill Tool (original code and assets)

The `tools-*` source files, JSON/SSML assets and this documentation are original
work released under the MIT License. Copyright 2026 the Offline Neural Voice
Skill Tool authors. Permission is granted to use, copy, modify and distribute
these files subject to the MIT License terms; the warranty is disclaimed.

## Bundled components (unmodified redistributed files)

| File | Component | Version | License | Source |
|---|---|---|---|---|
| dependencies-piper_tts-1.8.0-...whl | piper-tts (engine; includes piper_phonemize and espeak-ng-data) | 1.8.0 | GPL-3.0-or-later (espeak-ng is also GPL-3.0-or-later) | https://pypi.org/project/piper-tts/ · https://github.com/rhasspy/piper |
| dependencies-onnxruntime-1.32.0-cp313-...whl | ONNX Runtime (CPU inference) | 1.32.0 | MIT | https://pypi.org/project/onnxruntime/ · https://github.com/microsoft/onnxruntime |
| dependencies-numpy-2.5.4-cp313-...whl | NumPy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | https://pypi.org/project/numpy/ · https://github.com/numpy/numpy |
| dependencies-protobuf-7.36.2-cp310-...whl | Protocol Buffers | 7.36.2 | 3-Clause BSD | https://pypi.org/project/protobuf/ · https://github.com/protocolbuffers/protobuf |
| dependencies-flatbuffers-25.12.19-py2.py3-...whl | FlatBuffers | 25.12.19 | Apache-2.0 | https://pypi.org/project/flatbuffers/ · https://github.com/google/flatbuffers |
| dependencies-packaging-26.3-py3-...whl | packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | https://pypi.org/project/packaging/ · https://github.com/pypa/packaging |
| dependencies-pathvalidate-3.3.1-py3-...whl | pathvalidate | 3.3.1 | MIT | https://pypi.org/project/pathvalidate/ · https://github.com/thombashi/pathvalidate |
| asset-voice-lessac-medium.onnx | Piper voice `en_US-lessac-medium` | model metadata says piper_version 1.0.0, quality medium, single speaker, en-US, dataset `lessac` | The rhasspy/piper-voices repository is tagged `license:mit` | https://huggingface.co/rhasspy/piper-voices (path en/en_US/lessac/medium/) · https://github.com/rhasspy/piper |
| asset-voice-lessac-medium.onnx.json | Voice config for the above (phoneme map, inference defaults) | n/a | as above | as above |

## piper-tts / espeak-ng (GPL-3.0-or-later) — what this means for you

- The `piper-tts` wheel is redistributed **unmodified**, with these notices, exactly
  as the GPL permits. Source for the corresponding binaries is available at the
  URLs above (PyPI publishes the sdist; GitHub carries the source tree).
- The Skill Tool invokes the engine **as a separate process/library** and never
  links GPL code into itself; treat the bundle as an aggregate containing a
  GPL-3.0 component.
- If your distribution requirements cannot accommodate a GPL-3.0 component,
  delete `dependencies-piper_tts-*.whl`, `dependencies-onnxruntime-*.whl`,
  `dependencies-numpy-*.whl` and the other wheels, and install Piper yourself
  under your own compliance review (`pip install piper-tts` plus a voice of
  your choice). The `synthesize` command then works with your own engine
  install, and the rest of the tool (validation, mastering, normalization,
  consent, provenance) works unchanged.

## Voice model provenance and rights

`en_US-lessac-medium` is a synthetic voice trained by the Piper project; it is
not the voice of a specific real, identifiable person, and this Skill Tool
does not clone real voices. If you replace it with another model, the
operator is responsible for (a) the model's license, (b) consent/rights for
any cloned voice, and (c) the disclosure line required by
`asset-safety-policy.json`. See `knowledge-readme.md` → "Safety and approval rules".

## No other third-party material

No other code, media, models, or data from third parties is bundled. The
`__pycache__` folders and the `.ov-venv` directory are created locally by
`tools-ov-setup.py` at install time and are **not** part of this distribution.
