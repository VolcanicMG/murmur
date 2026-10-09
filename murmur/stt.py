"""Speech to text (Parakeet via onnx-asr). The model ships inside the build: `python -m murmur fetch-model` downloads it once
into packaging/models/parakeet (gitignored), the spec copies that folder into the exe, and load_model reads it offline. A source
checkout without the folder still falls back to the Hugging Face cache (download on first load) so dev runs work."""
import sys
from pathlib import Path

import onnx_asr

_model = None
DEFAULT_MODEL, DEFAULT_QUANT = "nemo-parakeet-tdt-0.6b-v2", "int8"  # v2 = English-only, lower English WER than the multilingual v3; int8 matches fp32


def model_dir():
    """Folder the build ships the default model in: _internal/packaging/models/parakeet in the exe, packaging/models/parakeet in a checkout."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "packaging" / "models" / "parakeet"


def load_model(cfg):
    """[stt] path set = a user's own onnx-asr model folder (config.json names the type; quantized files are tried first, then
    plain). Otherwise the default model: the bundled folder when present, else the Hugging Face cache."""
    global _model
    stt = cfg["stt"]
    quant = stt.get("quantization") or None
    if custom := stt.get("path", ""):
        try:
            _model = onnx_asr.load_model(None, custom, quantization=quant)
        except Exception:
            if quant is None:
                raise
            _model = onnx_asr.load_model(None, custom, quantization=None)
        return
    name = model_name(stt)
    bundled = model_dir() if (name, quant) == (DEFAULT_MODEL, DEFAULT_QUANT) and model_dir().is_dir() else None
    _model = onnx_asr.load_model(name, bundled, quantization=quant)


LEGACY_DEFAULT = "nemo-parakeet-tdt-0.6b-v3"  # the old default, written into every saved config.toml before v2 shipped


def model_name(stt):
    """[stt] model, with the old default read as the current one: Settings never offered a model name, so a saved v3 is the
    stale default, not a choice (it kept every install on v3 int8: 21.6% vs v2's 16.4% word errors on Ethan's takes)."""
    name = stt.get("model") or DEFAULT_MODEL
    return DEFAULT_MODEL if name == LEGACY_DEFAULT else name


def fetch(dest=None):
    """Download the default model into dest (default model_dir()) if it isn't there yet; returns the folder. Build-time only."""
    dest = Path(dest) if dest else model_dir()
    onnx_asr.load_model(DEFAULT_MODEL, dest, quantization=DEFAULT_QUANT)  # onnx-asr fills a missing local dir, then loads offline
    return dest


def recognize(audio):
    """audio: float32 mono 16 kHz ndarray -> transcript."""
    return str(_model.recognize(audio)).strip()
