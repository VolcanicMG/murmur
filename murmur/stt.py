"""Speech to text (Parakeet via onnx-asr). The model ships inside the build: `python -m murmur fetch-model` downloads it once
into packaging/models/parakeet (gitignored), the spec copies that folder into the exe, and load_model reads it offline. A source
checkout without the folder still falls back to the Hugging Face cache (download on first load) so dev runs work."""
import sys
from pathlib import Path

import onnx_asr

_model = None
DEFAULT_MODEL, DEFAULT_QUANT = "nemo-parakeet-tdt-0.6b-v3", "int8"


def model_dir():
    """Folder the build ships the default model in: _internal/packaging/models/parakeet in the exe, packaging/models/parakeet in a checkout."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "packaging" / "models" / "parakeet"


def load_model(cfg):
    global _model
    stt = cfg["stt"]
    bundled = model_dir() if (stt["model"], stt["quantization"]) == (DEFAULT_MODEL, DEFAULT_QUANT) and model_dir().is_dir() else None
    _model = onnx_asr.load_model(stt["model"], bundled, quantization=stt["quantization"])


def fetch(dest=None):
    """Download the default model into dest (default model_dir()) if it isn't there yet; returns the folder. Build-time only."""
    dest = Path(dest) if dest else model_dir()
    onnx_asr.load_model(DEFAULT_MODEL, dest, quantization=DEFAULT_QUANT)  # onnx-asr fills a missing local dir, then loads offline
    return dest


def recognize(audio):
    """audio: float32 mono 16 kHz ndarray -> transcript."""
    return str(_model.recognize(audio)).strip()
