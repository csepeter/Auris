# Third-party notices

## LocalText2Voice

Parts of Auris text preparation and audiobook export behavior are derived from
or inspired by LocalText2Voice:

https://github.com/estebanstifli/LocalText2Voice

MIT License

Copyright (c) 2026 Esteban / AndromedaNova.com

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Trafilatura

Auris uses Trafilatura to extract article text and metadata from downloaded
HTML pages:

https://trafilatura.readthedocs.io/

Copyright 2019-2026 Adrien Barbaresi and contributors.

Licensed under the Apache License, Version 2.0:

https://www.apache.org/licenses/LICENSE-2.0

## MOSS-TTS-Nano ONNX runtime (vendored)

`reader/core/vendor/moss_tts_nano_onnx/` contains the unmodified ONNX CPU
runtime and text-normalization modules of MOSS-TTS-Nano:

https://github.com/OpenMOSS/MOSS-TTS-Nano

Copyright OpenMOSS Team. Licensed under the Apache License, Version 2.0; the
full license text is in `reader/core/vendor/moss_tts_nano_onnx/LICENSE`.
The MOSS-TTS, MOSS-TTS-Nano and MOSS Audio Tokenizer model weights are
downloaded from Hugging Face at runtime and are licensed under Apache-2.0.

## Supertonic (vendored helper)

`reader/core/vendor/supertonic/helper.py` is copied unchanged from:

https://github.com/supertone-oss-archive/supertonic

MIT License, Copyright (c) 2025 Supertone Inc. The full text is in
`reader/core/vendor/supertonic/LICENSE`. The Supertonic 3 model weights are
downloaded at runtime from `supertone-oss-archive/supertonic-3` and are
licensed under the BigScience OpenRAIL-M license, which includes use-based
restrictions that users must follow.

## omnivoice-triton kernels (vendored)

`reader/core/vendor/omnivoice_triton/` contains the Triton kernels and
`models/patching.py` of omnivoice-triton 0.1.0 (package-internal imports made
relative):

https://github.com/newgrit1004/omnivoice-triton

Copyright Sewon Kim. Licensed under the Apache License, Version 2.0; the full
text is in `reader/core/vendor/omnivoice_triton/LICENSE`. The optional Triton
compiler (`triton`, or `triton-windows` on Windows, MIT) is installed by the
user from Settings.

## onnx-asr and NVIDIA Parakeet TDT 0.6B v3

Quality control can use the `onnx-asr` package (MIT) to run NVIDIA Parakeet
TDT 0.6B v3. The ONNX export (`istupakov/parakeet-tdt-0.6b-v3-onnx`) is
downloaded from Hugging Face on first use; the model is licensed under
CC-BY-4.0 by NVIDIA.

## Piper and Hungarian Piper voices (optional)

The optional Piper engine uses the `piper-tts` package (piper1-gpl,
GPL-3.0-or-later, bundling espeak-ng, GPL-3.0). Auris does not bundle it; the
user installs it separately from Settings. The Hungarian voices (`anna`,
`berta`, `imre`) are downloaded from `rhasspy/piper-voices`; their training
data is published under CC0.

## HuSpaCy

Hungarian character detection can use the HuSpaCy `hu_core_news_md` model,
installed on request from https://huggingface.co/huspacy. HuSpaCy is licensed
under Apache-2.0; see the model card for the licenses of its training data.

## Pyphen

Pyphen (https://github.com/Kozea/Pyphen) and its Hungarian hyphenation
dictionary are used to tell PDF line-break hyphens from compound hyphens.
Pyphen is licensed under GPL-2.0+/LGPL-2.1+/MPL-1.1; the Hungarian dictionary
is distributed by the LibreOffice project under the same terms.
