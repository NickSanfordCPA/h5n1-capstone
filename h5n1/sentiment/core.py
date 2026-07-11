"""Sentiment scoring — pure, side-effect-free functions.

The SAME functions here are imported by exploration notebooks (small samples) and
by the Modal batch job (full corpus). No DB access, no file I/O, no globals: text
in, scores out. That is what makes "it worked in the notebook" mean it works in
production. See ARCHITECTURE_SKETCH.md section 3.

These are stubs on purpose. The contract (function names + signatures) exists from
day one so the notebook<->Modal wiring is stable; the bodies are implemented in the
sentiment phase.
"""
from __future__ import annotations

from functools import lru_cache

DEFAULT_MODEL = "cardiffnlp/twitter-xlm-roberta-base-sentiment"


@lru_cache(maxsize=2)
def load_model(name: str = DEFAULT_MODEL):
    """Lazily load a tokenizer+model. Requires the [ml] extra (torch, transformers)."""
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(name)
    model = AutoModelForSequenceClassification.from_pretrained(name)
    return tokenizer, model


def translate(texts: list[str]) -> list[str]:
    """Translate non-English texts to English before scoring (cf. Kathunia et al., 2024)."""
    raise NotImplementedError("Implemented in the sentiment phase.")


def score_sentiment(texts: list[str], model=None) -> list[float]:
    """Map each text to a [-1, 1] sentiment score. Pure: no I/O, no side effects."""
    raise NotImplementedError("Implemented in the sentiment phase.")
