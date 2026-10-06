import onnx_asr

_model = None


def load_model(cfg):
    global _model
    _model = onnx_asr.load_model(cfg["stt"]["model"], quantization=cfg["stt"]["quantization"])


def recognize(audio):
    """audio: float32 mono 16 kHz ndarray -> transcript."""
    return str(_model.recognize(audio)).strip()
