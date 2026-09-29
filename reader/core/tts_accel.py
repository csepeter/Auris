"""Optional inference acceleration for OmniVoice.

Two layers (can be combined):

1. **CUDA Graph** (native Windows + Linux, pure PyTorch)
   Captures OmniVoice.forward for each tensor shape and replays it across
   the iterative unmasking steps. Benefit depends on shape reuse and workload;
   measure cold capture separately from warm replay.

2. **Triton kernels** (Linux, WSL2 and native Windows via triton-windows)
   Fuses RMSNorm / SwiGLU / residual+norm with the omnivoice-triton kernels
   vendored in ``core/vendor/omnivoice_triton`` (upstream declares Python
   ≥3.12 only, but the kernels run on 3.11).
   Measured on an RTX 3090 (Python 3.11, torch 2.11, Hungarian batches):
   RTF 0.085 eager → 0.068 Triton → 0.065 hybrid, with the same ASR CER.

Install (optional): Settings → GPU acceleration → "Install Triton", or
``AURIS_TRITON=1`` during setup. Only the Triton compiler itself is
installed; ``triton_install_commands`` picks the triton-windows build that
matches the installed torch.
"""

from __future__ import annotations

import logging
from types import MethodType
from typing import Any

log = logging.getLogger(__name__)

ACCEL_MODES = ("off", "auto", "eager", "cuda_graph", "triton", "hybrid")
# Long-form generation produces several shapes as OmniVoice chunks long text.
# Keeping every graph alive is counterproductive: each graph retains a large
# logits buffer and its CUDA-private allocations.
MAX_CACHED_GRAPHS = 4
# Optional: pad sequence lengths to this multiple so packs of similar length
# replay one graph. Disabled: on an RTX 3090 (2026-09-29, 16 steps, 4-sentence
# Hungarian packs) it saved one capture per three shapes but warm packs were
# ~8% slower, and different kernel shapes changed the iterative decoding
# (outputs not waveform-identical). Padded positions are masked out.
GRAPH_LENGTH_BUCKET = 0


class CUDAGraphForward:
    """Wrap ``OmniVoice.forward`` with per-shape CUDA Graph capture/replay.

    First call for a given ``input_ids.shape`` captures a graph; later calls
    with the same shape replay it (big win across 16–32 unmasking steps).
    """

    def __init__(self, model: Any, bucket: int = GRAPH_LENGTH_BUCKET) -> None:
        self._model = model
        self._original_forward = model.forward
        self._graphs: dict[tuple, dict] = {}
        self.disabled_reason = ""
        self._bucket = max(0, int(bucket or 0))
        config = getattr(model, "config", None)
        self._pad_id = int(getattr(config, "audio_mask_id", 1024) or 1024)
        self.stats = {"captures": 0, "replays": 0, "padded": 0}
        # TTSEngine serializes calls and consumes outputs before the next
        # replay. Independent shape graphs can share their private pool.
        self._pool = None

    @staticmethod
    def _shape_key(input_ids, audio_mask=None, attention_mask=None,
                   document_ids=None, position_ids=None) -> tuple:
        # Optional masks and positions change the captured computation even
        # when input_ids has exactly the same shape.
        return tuple(
            None if tensor is None else
            (tuple(tensor.shape), tuple(tensor.stride()), tensor.dtype, tensor.device)
            for tensor in (input_ids, audio_mask, attention_mask, document_ids, position_ids)
        )

    def _capture(
        self,
        input_ids,
        audio_mask,
        attention_mask=None,
        document_ids=None,
        position_ids=None,
    ) -> dict:
        import torch

        key = self._shape_key(input_ids, audio_mask, attention_mask, document_ids, position_ids)
        log.info("CUDA Graph capture for shape %s …", key)

        # Release the oldest graph before allocating a new one, limiting peak
        # memory as well as the number of resident shapes.
        if len(self._graphs) >= MAX_CACHED_GRAPHS:
            del self._graphs[next(iter(self._graphs))]

        static_input_ids = input_ids.clone()
        static_audio_mask = audio_mask.clone()
        static_attn_mask = attention_mask.clone() if attention_mask is not None else None
        static_doc_ids = document_ids.clone() if document_ids is not None else None
        static_pos_ids = position_ids.clone() if position_ids is not None else None

        kwargs: dict[str, Any] = {}
        if static_attn_mask is not None:
            kwargs["attention_mask"] = static_attn_mask
        if static_doc_ids is not None:
            kwargs["document_ids"] = static_doc_ids
        if static_pos_ids is not None:
            kwargs["position_ids"] = static_pos_ids

        torch.cuda.synchronize()
        s = torch.cuda.Stream()
        s.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(s):
            static_output = self._original_forward(
                static_input_ids,
                static_audio_mask,
                **kwargs,
            )
        torch.cuda.current_stream().wait_stream(s)
        torch.cuda.synchronize()

        graph = torch.cuda.CUDAGraph()
        if self._pool is None:
            self._pool = torch.cuda.graph_pool_handle()
        # thread_local: a second export lane launching kernels on its own
        # stream must not invalidate this lane's capture.
        with torch.cuda.graph(graph, pool=self._pool, capture_error_mode="thread_local"):
            static_output = self._original_forward(
                static_input_ids,
                static_audio_mask,
                **kwargs,
            )

        self.stats["captures"] += 1
        entry = {
            "graph": graph,
            "static_input_ids": static_input_ids,
            "static_audio_mask": static_audio_mask,
            "static_attn_mask": static_attn_mask,
            "static_doc_ids": static_doc_ids,
            "static_pos_ids": static_pos_ids,
            "static_output": static_output,
        }
        self._graphs[key] = entry
        try:
            allocated = torch.cuda.memory_allocated() / (1024**3)
            reserved = torch.cuda.memory_reserved() / (1024**3)
            memory = f", allocated={allocated:.2f}GB reserved={reserved:.2f}GB"
        except Exception:
            memory = ""
        log.info(
            "CUDA Graph captured for shape %s (cache=%d%s)",
            key,
            len(self._graphs),
            memory,
        )
        return entry

    def __call__(
        self,
        input_ids,
        audio_mask,
        labels=None,
        attention_mask=None,
        document_ids=None,
        position_ids=None,
    ):
        # Training / labelled forward → never graph.
        if labels is not None or getattr(self._model, "training", False):
            return self._original_forward(
                input_ids,
                audio_mask,
                labels,
                attention_mask,
                document_ids,
                position_ids,
            )

        if self.disabled_reason or document_ids is not None:
            return self._original_forward(
                input_ids, audio_mask, labels, attention_mask, document_ids, position_ids
            )

        original_length = None
        padded = self._pad_to_bucket(input_ids, audio_mask, attention_mask, position_ids)
        if padded is not None:
            original_length = input_ids.shape[-1]
            input_ids, audio_mask, attention_mask = padded
            self.stats["padded"] += 1

        key = self._shape_key(input_ids, audio_mask, attention_mask, document_ids, position_ids)
        if key not in self._graphs:
            try:
                entry = self._capture(
                    input_ids, audio_mask, attention_mask, document_ids, position_ids,
                )
            except RuntimeError as exc:
                self.clear()
                self.disabled_reason = str(exc)
                log.warning("CUDA Graph capture disabled until reload: %s", exc)
                return self._original_forward(
                    input_ids, audio_mask, labels, attention_mask, document_ids, position_ids
                )
        else:
            # Refresh LRU order so frequently reused shapes stay resident.
            entry = self._graphs.pop(key)
            self._graphs[key] = entry

        entry["static_input_ids"].copy_(input_ids)
        entry["static_audio_mask"].copy_(audio_mask)
        if attention_mask is not None and entry["static_attn_mask"] is not None:
            entry["static_attn_mask"].copy_(attention_mask)
        if document_ids is not None and entry["static_doc_ids"] is not None:
            entry["static_doc_ids"].copy_(document_ids)
        if position_ids is not None and entry["static_pos_ids"] is not None:
            entry["static_pos_ids"].copy_(position_ids)

        entry["graph"].replay()
        self.stats["replays"] += 1
        output = entry["static_output"]
        if original_length is not None:
            return _slice_logits(output, original_length)
        return output

    def _pad_to_bucket(self, input_ids, audio_mask, attention_mask, position_ids):
        """Pad [N, C, T] inputs and their [N, 1, T, T] mask to a bucket length."""
        if not self._bucket or attention_mask is None or position_ids is not None:
            return None
        if input_ids.dim() != 3 or audio_mask.dim() != 2 or attention_mask.dim() != 4:
            return None
        if attention_mask.dtype.is_floating_point:
            return None
        length = int(input_ids.shape[-1])
        target = -(-length // self._bucket) * self._bucket
        if target == length:
            return None
        import torch

        extra = target - length
        ids = torch.nn.functional.pad(input_ids, (0, extra), value=self._pad_id)
        mask = torch.nn.functional.pad(audio_mask, (0, extra), value=False)
        attn = torch.nn.functional.pad(attention_mask, (0, extra, 0, extra), value=False)
        # Padded queries attend only to themselves: finite values that no real
        # position can see, since real rows never attend to these columns.
        idx = torch.arange(length, target, device=attn.device)
        attn[:, :, idx, idx] = True
        return ids, mask, attn

    def clear(self) -> None:
        self._graphs.clear()
        self._pool = None


def _slice_logits(output, length: int):
    """Drop padded positions from a model output whose logits are [N, C, T, V]."""
    import copy

    logits = getattr(output, "logits", None)
    if logits is None:
        return output[..., :length, :] if hasattr(output, "shape") else output
    sliced = copy.copy(output)
    try:
        sliced.logits = logits[:, :, :length, :]
    except Exception:
        sliced["logits"] = logits[:, :, :length, :]
    return sliced


def _fast_predict_tokens_with_scoring(
    model,
    c_logits,
    u_logits,
    gen_config,
):
    """Equivalent greedy CFG scoring with one log-softmax instead of three."""
    if getattr(gen_config, "class_temperature", 0.0) > 0.0:
        return model._auris_original_predict_tokens(
            c_logits, u_logits, gen_config
        )

    import torch
    import torch.nn.functional as F

    guidance = float(getattr(gen_config, "guidance_scale", 0.0))
    if guidance:
        # Normalization constants in the two input log-softmax operations are
        # per-position scalars and cancel in the final log-softmax.
        guided_logits = c_logits + guidance * (c_logits - u_logits)
    else:
        guided_logits = c_logits
    log_probs = F.log_softmax(guided_logits, dim=-1)
    log_probs[..., model.config.audio_mask_id] = -float("inf")
    # max returns both values and indices: avoid a second full vocabulary
    # reduction for argmax at every decoding step.
    confidence_scores, pred_tokens = torch.max(log_probs, dim=-1)
    return pred_tokens, confidence_scores


def apply_scoring_optimization(model) -> bool:
    """Install the exact greedy CFG fast path used by audiobook export."""
    if hasattr(model, "_auris_original_predict_tokens"):
        return True
    original = getattr(model, "_predict_tokens_with_scoring", None)
    if original is None:
        log.warning("OmniVoice scoring optimization unavailable")
        return False
    model._auris_original_predict_tokens = original
    model._predict_tokens_with_scoring = MethodType(
        _fast_predict_tokens_with_scoring, model
    )
    log.info("OmniVoice greedy CFG scoring optimization installed")
    return True


def triton_available() -> bool:
    try:
        import triton  # noqa: F401

        return True
    except (ImportError, OSError, RuntimeError):
        return False


def omnivoice_triton_available() -> bool:
    try:
        from core.vendor.omnivoice_triton.patching import apply_triton_kernels  # noqa: F401

        return True
    except (ImportError, OSError, RuntimeError):
        return False


def triton_install_commands(torch_version: str, system: str) -> list[list[str]]:
    """pip argument lists that add the Triton compiler for this torch build.

    triton-windows tracks torch minor versions (torch 2.6 → triton 3.2, …,
    torch 2.11 → triton 3.7). On Linux the CUDA torch wheels already depend
    on the matching ``triton``.
    """
    import re

    match = re.match(r"(\d+)\.(\d+)", str(torch_version or ""))
    if not match:
        raise ValueError("Ismeretlen PyTorch-verzió.")
    major, minor = int(match.group(1)), int(match.group(2))
    commands: list[list[str]] = []
    if system == "Windows":
        if major != 2 or minor < 6:
            raise ValueError("A Triton Windowson PyTorch 2.6 vagy újabb verziót igényel.")
        triton_minor = minor - 4
        commands.append([f"triton-windows>=3.{triton_minor},<3.{triton_minor + 1}"])
    elif not triton_available():
        commands.append(["triton"])
    return commands


def install_triton(python: str | None = None) -> dict:
    """Install Triton kernels into the running environment (NVIDIA only)."""
    import importlib
    import platform
    import subprocess
    import sys

    probe = probe_accel()
    if probe.get("backend") != "cuda":
        return {"ok": False, "message": "A Triton-gyorsítás NVIDIA GPU-t igényel."}
    try:
        commands = triton_install_commands(probe.get("torch_version", ""), platform.system())
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    for args in commands:
        result = subprocess.run([python or sys.executable, "-m", "pip", "install", *args],
                                capture_output=True, text=True)
        if result.returncode:
            return {"ok": False, "message": (result.stderr or result.stdout)[-600:]}
    importlib.invalidate_caches()
    ready = triton_available() and omnivoice_triton_available()
    return {"ok": ready, "message": (
        "Telepítve. Válaszd a Hibrid módot, mentsd a beállítást, majd töltsd újra a beszédmotort."
        if ready else "A csomagok települtek, de a Triton nem tölthető be; indítsd újra az Aurist.")}


def probe_accel() -> dict:
    """Report what acceleration backends are importable."""
    import platform

    cuda = False
    backend = "cpu"
    device_name = "CPU"
    torch_version = ""
    try:
        import torch

        torch_version = torch.__version__
        cuda = bool(torch.cuda.is_available())
        if cuda:
            backend = "rocm" if getattr(torch.version, "hip", None) else "cuda"
            device_name = torch.cuda.get_device_name()
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            backend = "mps"
            device_name = "Apple Metal (MPS)"
    except ImportError:
        pass
    has_triton = triton_available()
    has_ovt = omnivoice_triton_available()
    return {
        "cuda": cuda,
        "backend": backend,
        "device_name": device_name,
        "torch_version": torch_version,
        "triton": has_triton,
        "omnivoice_triton": has_ovt,
        "platform": platform.system(),
        "recommended": _recommend_mode(backend == "cuda", has_triton, has_ovt),
    }


def _recommend_mode(cuda: bool, has_triton: bool, has_ovt: bool) -> str:
    if not cuda:
        return "eager"
    # Optional kernels being importable is not a speed/compatibility test.
    # Keep hybrid an explicit opt-in on all NVIDIA architectures.
    return "cuda_graph"


def resolve_accel_mode(requested: str | None) -> str:
    """Map settings value to an effective mode string."""
    mode = (requested or "auto").strip().lower()
    if mode not in ACCEL_MODES:
        mode = "auto"
    if mode in ("off", "eager"):
        return mode
    probe = probe_accel()
    if probe.get("backend", "cuda" if probe["cuda"] else "cpu") != "cuda":
        return "eager"
    if mode == "auto":
        return probe["recommended"]
    if mode == "triton" and not (probe["triton"] and probe["omnivoice_triton"]):
        log.warning("tts_accel=triton requested but packages missing; using cuda_graph")
        return "cuda_graph"
    if mode == "hybrid" and not (probe["triton"] and probe["omnivoice_triton"]):
        log.warning("tts_accel=hybrid requested but Triton missing; using cuda_graph")
        return "cuda_graph"
    return mode


def apply_triton_to_omnivoice(model) -> bool:
    """Apply omnivoice-triton kernel patches to ``model.llm``. Returns success."""
    try:
        from core.vendor.omnivoice_triton.patching import (
            apply_triton_kernels,
            find_patchable_model,
        )
    except ImportError as exc:
        log.info("Triton kernels unavailable (%s)", exc)
        return False

    try:
        target = getattr(model, "llm", None)
        if target is None:
            target = find_patchable_model(model)
        apply_triton_kernels(target)
        log.info("Applied omnivoice-triton kernels to LLM backbone")
        return True
    except Exception as exc:
        log.warning("Triton kernel patch failed: %s", exc)
        return False


def apply_cuda_graph_to_omnivoice(model) -> CUDAGraphForward | None:
    """Install CUDA Graph wrapper on ``model.forward``. Returns wrapper or None."""
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        wrapper = CUDAGraphForward(model)
        model.forward = wrapper
        log.info("CUDA Graph forward wrapper installed on OmniVoice")
        return wrapper
    except Exception as exc:
        log.warning("CUDA Graph wrap failed: %s", exc)
        return None


def apply_acceleration(model, mode: str | None = "auto") -> dict:
    """Apply requested acceleration to a loaded OmniVoice model.

    Returns a status dict: ``{mode, triton, cuda_graph, message}``.
    """
    effective = resolve_accel_mode(mode)
    status = {
        "requested": mode or "auto",
        "effective": effective,
        "triton": False,
        "cuda_graph": False,
        "scoring_opt": False,
        "message": "",
        "probe": probe_accel(),
    }

    if effective == "off":
        status["message"] = "Acceleration off"
        return status

    status["scoring_opt"] = apply_scoring_optimization(model)

    if effective == "eager":
        status["message"] = "PyTorch scoring optimization (no CUDA Graph / Triton)"
        return status

    if effective in ("triton", "hybrid"):
        status["triton"] = apply_triton_to_omnivoice(model)

    if effective in ("cuda_graph", "hybrid") or (
        effective == "triton" and not status["triton"]
    ):
        # Always pair triton with graph when possible; pure triton is weak alone.
        wrapper = apply_cuda_graph_to_omnivoice(model)
        status["cuda_graph"] = wrapper is not None
        if wrapper is not None:
            # Keep a handle so reload can clear graphs.
            model._auris_cuda_graph = wrapper

    if effective == "hybrid":
        if status["triton"] and status["cuda_graph"]:
            status["message"] = "Hybrid acceleration: Triton kernels + CUDA Graph"
        elif status["cuda_graph"]:
            status["message"] = "CUDA Graph only (Triton unavailable on this platform)"
            status["effective"] = "cuda_graph"
        elif status["triton"]:
            status["message"] = "Triton kernels only (CUDA Graph wrap failed)"
            status["effective"] = "triton"
        else:
            status["message"] = "No acceleration applied"
            status["effective"] = "off"
    elif effective == "cuda_graph":
        status["message"] = (
            "CUDA Graph acceleration enabled"
            if status["cuda_graph"]
            else "CUDA Graph failed"
        )
        if not status["cuda_graph"]:
            status["effective"] = "off"
    elif effective == "triton":
        # Prefer adding graph as well when we fell through above
        if status["triton"] and status["cuda_graph"]:
            status["message"] = "Triton + CUDA Graph"
            status["effective"] = "hybrid"
        elif status["triton"]:
            status["message"] = "Triton kernels enabled"
        elif status["cuda_graph"]:
            status["message"] = "CUDA Graph (Triton patch unavailable)"
            status["effective"] = "cuda_graph"
        else:
            status["message"] = "Triton not available"
            status["effective"] = "off"

    log.info("TTS acceleration: %s", status["message"])
    print(f"TTS acceleration: {status['message']}", flush=True)
    return status
