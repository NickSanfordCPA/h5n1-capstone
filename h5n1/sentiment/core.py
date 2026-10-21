"""Sentiment scoring — pure, side-effect-free functions.

The SAME functions here are imported by exploration notebooks (small samples) and
by the Modal batch job (full corpus). No DB access, no file I/O, no globals: text
in, scores out. That is what makes "it worked in the notebook" mean it works in
production. See ARCHITECTURE_SKETCH.md section 3.

Two models. DEFAULT_MODEL (XLM-R) is the multilingual one: the Meta arm was built
around it, and Waree's x_post_sentiment, the X sentiment for every stream, uses it. ENGLISH_MODEL is cardiffnlp's
TweetEval RoBERTa retrained on tweets through 2021, the model behind every exploratory X
sentiment result. Both label negative / neutral / positive. `translate` stays a stub.
"""
from __future__ import annotations

import re
from functools import lru_cache

DEFAULT_MODEL = "cardiffnlp/twitter-xlm-roberta-base-sentiment"
ENGLISH_MODEL = "cardiffnlp/twitter-roberta-base-sentiment-latest"


@lru_cache(maxsize=2)
def load_model(name: str = DEFAULT_MODEL):
    """Lazily load a tokenizer+model. Requires the [ml] extra (torch, transformers)."""
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(name)
    model = AutoModelForSequenceClassification.from_pretrained(name)
    model.eval()
    return tokenizer, model


def translate(texts: list[str]) -> list[str]:
    """Translate non-English texts to English before scoring (cf. Kathunia et al., 2024)."""
    raise NotImplementedError("Implemented in the sentiment phase.")


def preprocess(text: str, collapse_newlines: bool = False) -> str:
    """The cardiffnlp convention: user handles -> '@user', links -> 'http'. With
    collapse_newlines, line breaks become one space first."""
    text = text or ""
    if collapse_newlines:
        text = re.sub(r"\s*[\r\n]+\s*", " ", text)
    text = re.sub(r"@\w+", "@user", text)
    return re.sub(r"https?://\S+", "http", text)


def score_probs(texts: list[str], model=None, max_length: int = 128,
                collapse_newlines: bool = False) -> list[tuple[float, float, float]]:
    """(p_negative, p_neutral, p_positive) per text, on the model's device. Pure: no I/O."""
    import torch

    tokenizer, mdl = model or load_model(ENGLISH_MODEL)
    enc = tokenizer([preprocess(t, collapse_newlines) for t in texts], padding=True, truncation=True,
                    max_length=max_length, return_tensors="pt").to(mdl.device)
    with torch.inference_mode():
        p = torch.softmax(mdl(**enc).logits, dim=-1)
    labels = [mdl.config.id2label[i].lower() for i in range(p.shape[1])]
    order = [labels.index(k) for k in ("negative", "neutral", "positive")]
    return [tuple(float(row[i]) for i in order) for row in p]


def score_sentiment(texts: list[str], model=None) -> list[float]:
    """Map each text to a [-1, 1] sentiment score, p_positive - p_negative."""
    return [pos - neg for neg, _, pos in score_probs(texts, model)]
