# Vendored third-party code

These files are copied unchanged from their upstream projects so that Auris
does not have to install their pinned, conflicting dependency sets.

| Folder | Upstream | Revision | License |
| --- | --- | --- | --- |
| `moss_tts_nano_onnx/` | https://github.com/OpenMOSS/MOSS-TTS-Nano (ONNX CPU runtime, text normalization) | tree `8b7bcc9341b3b4ef3a3a58ba1338a7d85ff133eb` | Apache-2.0 (`moss_tts_nano_onnx/LICENSE`) |
| `omnivoice_triton/` | https://github.com/newgrit1004/omnivoice-triton (`kernels/`, `models/patching.py` only; imports made relative) | 0.1.0 | Apache-2.0 (`omnivoice_triton/LICENSE`) |
| `supertonic/` | https://github.com/supertone-oss-archive/supertonic (`py/helper.py`) | archived main branch, 2026-09 | MIT (`supertonic/LICENSE`) |

Auris-specific behaviour (reading reference audio with `soundfile`, Hungarian
text handling) lives in `core/local_engines.py`, not in these files.
