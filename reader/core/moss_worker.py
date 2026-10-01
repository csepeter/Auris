"""Isolated MOSS-TTS 1.5 worker (JSON lines over stdin/stdout).

The 4B model uses ``trust_remote_code`` modules and ~14 GB of VRAM. Running it
in its own process keeps those modules out of the Auris process and releases
all GPU memory reliably when the engine is unloaded or cancelled.
"""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.desktop_support import activate_gpu_runtime
activate_gpu_runtime()

import json
import os
import sys
import traceback
from collections import OrderedDict


def reply(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
    sys.stdout.flush()


_CODES: "OrderedDict[str, object]" = OrderedDict()


def reference_codes(processor, path: str):
    """Encode a reference clip once per file version (soundfile, no torchcodec)."""
    import soundfile as sf
    import torch

    st = os.stat(path)
    key = f"{os.path.abspath(path)}|{st.st_mtime_ns}|{st.st_size}"
    if key in _CODES:
        _CODES.move_to_end(key)
        return _CODES[key]
    wav, sr = sf.read(path, dtype="float32", always_2d=True)
    codes = processor.encode_audios_from_wav([torch.from_numpy(wav.T.copy())], int(sr))[0]
    _CODES[key] = codes
    while len(_CODES) > 16:
        _CODES.popitem(last=False)
    return codes


def main() -> None:
    header = json.loads(sys.stdin.readline())
    import numpy as np
    import soundfile as sf
    import torch
    import transformers
    from transformers import AutoModel, AutoProcessor

    if torch.cuda.is_available():
        # Upstream: the cuDNN SDPA backend is broken for this model.
        torch.backends.cuda.enable_cudnn_sdp(False)
        torch.backends.cuda.enable_flash_sdp(True)
        torch.backends.cuda.enable_mem_efficient_sdp(True)
        torch.backends.cuda.enable_math_sdp(True)
        device, dtype = "cuda", torch.bfloat16
    else:
        device, dtype = "cpu", torch.float32
    from huggingface_hub import snapshot_download

    # Resolve pinned snapshots first: remote code is loaded from these exact
    # revisions only, never from whatever the repository head is today.
    repo = snapshot_download(header["model"], revision=header.get("revision"))
    kwargs = {"trust_remote_code": True}
    if header.get("codec"):
        kwargs["codec_path"] = snapshot_download(header["codec"], revision=header.get("codec_revision"))
    processor = AutoProcessor.from_pretrained(repo, **kwargs)
    processor.audio_tokenizer = processor.audio_tokenizer.to(device)
    model = AutoModel.from_pretrained(
        repo, trust_remote_code=True, attn_implementation="sdpa", dtype=dtype,
    ).to(device).eval()
    sample_rate = int(processor.model_config.sampling_rate)
    reply({"ok": True, "event": "ready", "device": device, "dtype": str(dtype).replace("torch.", ""),
           "sample_rate": sample_rate, "transformers": transformers.__version__})

    for line in sys.stdin:
        try:
            request = json.loads(line)
            if request.get("command") == "shutdown":
                reply({"ok": True, "event": "shutdown"})
                return
            if request.get("command") != "generate":
                raise ValueError("Unknown worker command")
            seed = int(request.get("seed", -1))
            if seed >= 0:
                torch.manual_seed(seed)
                if torch.cuda.is_available():
                    torch.cuda.manual_seed_all(seed)
            reference = request.get("reference_audio")
            message_kwargs = {"text": request["text"]}
            if request.get("language"):
                message_kwargs["language"] = request["language"]
            if reference:
                message_kwargs["reference"] = [reference_codes(processor, reference)]
            message = processor.build_user_message(**message_kwargs)
            batch = processor([[message]], mode="generation")
            with torch.inference_mode():
                output = model.generate(
                    input_ids=batch["input_ids"].to(device),
                    attention_mask=batch["attention_mask"].to(device),
                    max_new_tokens=int(request.get("max_new_tokens", 4096)),
                    do_sample=True,
                    audio_temperature=float(request.get("temperature", 1.7)),
                    audio_top_p=float(request.get("top_p", 0.8)),
                    audio_top_k=int(request.get("top_k", 25)),
                    audio_repetition_penalty=1.0,
                )
            decoded = list(processor.decode(output, return_stereo=False))
            if not decoded or decoded[0] is None:
                raise RuntimeError("MOSS-TTS produced no audio")
            audio = decoded[0].audio_codes_list[0]
            audio = audio.float().cpu().numpy() if hasattr(audio, "cpu") else np.asarray(audio)
            sf.write(request["output_path"], np.asarray(audio, dtype=np.float32).reshape(-1), sample_rate)
            reply({"ok": True, "event": "generated", "samples": int(np.asarray(audio).size)})
        except Exception as exc:
            reply({"ok": False, "error": str(exc), "traceback": traceback.format_exc()})


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        reply({"ok": False, "event": "startup_error", "error": str(exc),
               "traceback": traceback.format_exc()})
