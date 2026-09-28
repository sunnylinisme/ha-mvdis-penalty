"""Minimal local OCR runtime for the MVDIS CAPTCHA model."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterable
from io import BytesIO
from pathlib import Path
from typing import Any

ASSET_DIR = Path(os.environ.get("OCR_ASSET_DIR", "/app/ocr_assets"))
MODEL_PATH = ASSET_DIR / "common_old.onnx"
CHARSET_PATH = ASSET_DIR / "charset.json"


def ctc_decode(indices: Iterable[int], charset: list[str]) -> str:
    """Collapse repeated CTC indices, omit blanks, and return decoded text."""
    result: list[str] = []
    previous: int | None = None
    for value in indices:
        index = int(value)
        if index != previous and index != 0 and 0 <= index < len(charset):
            result.append(charset[index])
        previous = index
    return "".join(result)


class LocalOcr:
    """Load only the ddddocr recognition model used by this add-on."""

    def __init__(self) -> None:
        self._session: Any = None
        self._charset: list[str] = []
        self._lock = threading.Lock()

    def classification(self, image_bytes: bytes) -> str:
        """Recognize a CAPTCHA using the same preprocessing and CTC decoding."""
        with self._lock:
            if self._session is None:
                self._load()

            import numpy as np
            from PIL import Image

            with Image.open(BytesIO(image_bytes)) as image:
                target_height = 64
                target_width = int(image.size[0] * (target_height / image.size[1]))
                image = image.resize(
                    (target_width, target_height), Image.Resampling.LANCZOS
                )
                image = image.convert("L")
                image_array = np.asarray(image, dtype=np.float32) / 255.0

            image_array = image_array[np.newaxis, np.newaxis, :, :]
            input_name = self._session.get_inputs()[0].name
            output = self._session.run(None, {input_name: image_array})[0]
            if output.ndim == 3 and output.shape[1] == 1:
                indices = np.argmax(output[:, 0, :], axis=1)
            elif output.ndim == 3:
                indices = np.argmax(output[0, :, :], axis=1)
            else:
                indices = np.atleast_1d(np.argmax(output, axis=-1))
            return ctc_decode(indices, self._charset)

    def _load(self) -> None:
        import onnxruntime

        if not MODEL_PATH.is_file() or not CHARSET_PATH.is_file():
            raise RuntimeError("Local OCR assets are missing")
        with CHARSET_PATH.open(encoding="utf-8") as file:
            charset = json.load(file)
        if not isinstance(charset, list) or not charset or charset[0] != "":
            raise RuntimeError("Local OCR charset is invalid")
        onnxruntime.set_default_logger_severity(3)
        self._session = onnxruntime.InferenceSession(
            str(MODEL_PATH), providers=["CPUExecutionProvider"]
        )
        self._charset = [str(value) for value in charset]
