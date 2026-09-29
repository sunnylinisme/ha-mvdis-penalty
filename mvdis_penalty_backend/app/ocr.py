"""Minimal local OCR runtime for the MVDIS CAPTCHA model."""

from __future__ import annotations

import json
import os
import re
import threading
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

ASSET_DIR = Path(os.environ.get("OCR_ASSET_DIR", "/app/ocr_assets"))
MODEL_PATH = ASSET_DIR / "common_old.onnx"
CHARSET_PATH = ASSET_DIR / "charset.json"
# The ddddocr CTC score is conservative on MVDIS images with dense interference
# lines. Live calibration samples showed unanimous, visually correct readings as
# low as 0.765. Agreement remains the primary safety signal: 2/4 splits are never
# submitted, while unanimous and 3/4 decisions use separate calibrated floors.
OCR_UNANIMOUS_MIN_CONFIDENCE = 0.75
OCR_MAJORITY_MIN_CONFIDENCE = 0.80


@dataclass(frozen=True, slots=True)
class OcrDecision:
    """One ensemble decision, including whether it is safe to submit."""

    code: str
    confidence: float
    agreement: int
    sample_count: int
    reliable: bool


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


def assess_candidates(candidates: Iterable[tuple[str, float]]) -> OcrDecision:
    """Assess a CAPTCHA by agreement first and calibrated model confidence."""
    normalized = [
        (re.sub(r"[^A-Z0-9]", "", text.upper()), confidence)
        for text, confidence in candidates
    ]
    valid = [(text, confidence) for text, confidence in normalized if len(text) == 4]
    pool = valid or normalized
    if not pool:
        return OcrDecision("", 0.0, 0, 0, False)

    votes = Counter(text for text, _confidence in pool)
    best_confidence: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for position, (text, confidence) in enumerate(pool):
        best_confidence[text] = max(best_confidence.get(text, 0.0), confidence)
        first_seen.setdefault(text, position)
    code = max(
        votes,
        key=lambda text: (
            votes[text],
            best_confidence[text],
            -first_seen[text],
        ),
    )
    matching_confidence = [confidence for text, confidence in pool if text == code]
    confidence = sum(matching_confidence) / len(matching_confidence)
    agreement = votes[code]
    unanimous = agreement == len(normalized)
    reliable = len(code) == 4 and (
        (unanimous and confidence >= OCR_UNANIMOUS_MIN_CONFIDENCE)
        or (agreement >= 3 and confidence >= OCR_MAJORITY_MIN_CONFIDENCE)
    )
    return OcrDecision(code, confidence, agreement, len(normalized), reliable)


def choose_candidate(candidates: Iterable[tuple[str, float]]) -> str:
    """Choose a four-character CAPTCHA by agreement, then model confidence."""
    return assess_candidates(candidates).code


class LocalOcr:
    """Load only the ddddocr recognition model used by this add-on."""

    def __init__(self) -> None:
        self._session: Any = None
        self._charset: list[str] = []
        self._lock = threading.Lock()

    def classification(self, image_bytes: bytes) -> str:
        """Recognize one CAPTCHA through several local-only image variants."""
        return self.analyze(image_bytes).code

    def analyze(self, image_bytes: bytes) -> OcrDecision:
        """Recognize one CAPTCHA and report ensemble reliability metadata."""
        with self._lock:
            if self._session is None:
                self._load()

            import numpy as np
            from PIL import Image, ImageEnhance, ImageFilter, ImageOps

            with Image.open(BytesIO(image_bytes)) as image:
                target_height = 64
                target_width = int(image.size[0] * (target_height / image.size[1]))
                image = image.resize(
                    (target_width, target_height), Image.Resampling.LANCZOS
                )
                image = image.convert("L")
                variants = (
                    image,
                    ImageOps.autocontrast(image, cutoff=1),
                    ImageEnhance.Contrast(image).enhance(1.35),
                    ImageOps.autocontrast(image.filter(ImageFilter.SHARPEN), cutoff=1),
                )

            candidates: list[tuple[str, float]] = []
            input_name = self._session.get_inputs()[0].name
            for variant in variants:
                image_array = np.asarray(variant, dtype=np.float32) / 255.0
                image_array = image_array[np.newaxis, np.newaxis, :, :]
                output = self._session.run(None, {input_name: image_array})[0]
                candidates.append(self._decode_output(output, np))
            return assess_candidates(candidates)

    def _decode_output(self, output: Any, np: Any) -> tuple[str, float]:
        """Decode model logits and estimate confidence for emitted characters."""
        if output.ndim == 3 and output.shape[1] == 1:
            logits = output[:, 0, :]
        elif output.ndim == 3:
            logits = output[0, :, :]
        else:
            logits = np.atleast_2d(output)

        indices = np.argmax(logits, axis=-1)
        text = ctc_decode(indices, self._charset)
        shifted = logits - np.max(logits, axis=-1, keepdims=True)
        probabilities = np.exp(shifted)
        probabilities /= np.sum(probabilities, axis=-1, keepdims=True)

        emitted: list[float] = []
        previous: int | None = None
        for position, value in enumerate(indices):
            index = int(value)
            if index != previous and index != 0 and 0 <= index < len(self._charset):
                emitted.append(float(probabilities[position, index]))
            previous = index
        confidence = sum(emitted) / len(emitted) if emitted else 0.0
        return text, confidence

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
