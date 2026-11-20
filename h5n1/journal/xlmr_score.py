"""XLM-R sentiment scoring, identical for both platforms. Standalone: no h5n1 imports, so the
same file runs on a GPU machine and on primary for the control check.

    python xlmr_score.py --in posts.parquet --out scores/ [--device cuda] [--limit N]

Settled specification (FABLE_INSTRUCTIONS section 3):
    model      cardiffnlp/twitter-xlm-roberta-base-sentiment
    precision  fp32
    length     512 tokens, truncated
    cleaning   newline -> space; a token starting with "@" (longer than one character) -> "@user";
               a token starting with "http" -> "http"   (the model card's own preprocessing)
    score      p_positive - p_negative

Input: parquet with columns `id` and `text`. Output: one parquet per CHUNK posts in --out,
`scores_<start>.parquet` with id, p_neg, p_neu, p_pos. Resumable: a chunk whose file exists is
skipped. Posts are processed in a fixed order (sorted by id) so chunk boundaries are stable;
within a chunk, batches are length-sorted for speed, which does not change any score beyond
padding-level float noise.
"""
from __future__ import annotations

import argparse
import pathlib

import numpy as np
import pandas as pd
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

MODEL = "cardiffnlp/twitter-xlm-roberta-base-sentiment"
CHUNK = 20_000
MAX_LEN = 512


def preprocess(text: str) -> str:
    out = []
    for t in (text or "").replace("\r", " ").replace("\n", " ").split(" "):
        if t.startswith("@") and len(t) > 1:
            t = "@user"
        elif t.startswith("http"):
            t = "http"
        out.append(t)
    return " ".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--model", default=MODEL, help="hub id, or a local folder holding the same weights")
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    df = pd.read_parquet(a.src, columns=["id", "text"]).sort_values("id").reset_index(drop=True)
    if a.limit:
        df = df.head(a.limit)
    tok = AutoTokenizer.from_pretrained(a.model)
    model = AutoModelForSequenceClassification.from_pretrained(a.model).to(a.device).float().eval()
    labels = [model.config.id2label[i].lower() for i in range(3)]
    assert labels == ["negative", "neutral", "positive"], labels
    print(f"{len(df):,} posts, device {a.device}, torch {torch.__version__}", flush=True)
    for start in range(0, len(df), CHUNK):
        dest = out / f"scores_{start:08d}.parquet"
        if dest.exists():
            continue
        part = df.iloc[start:start + CHUNK]
        texts = [preprocess(t) for t in part["text"]]
        order = np.argsort([len(t) for t in texts])
        probs = np.zeros((len(texts), 3), dtype=np.float32)
        with torch.inference_mode():
            for i in range(0, len(order), a.batch):
                idx = order[i:i + a.batch]
                enc = tok([texts[j] for j in idx], return_tensors="pt", padding=True,
                          truncation=True, max_length=MAX_LEN).to(a.device)
                probs[idx] = torch.softmax(model(**enc).logits.float(), dim=-1).cpu().numpy()
        res = pd.DataFrame({"id": part["id"].to_numpy(), "p_neg": probs[:, 0],
                            "p_neu": probs[:, 1], "p_pos": probs[:, 2]})
        tmp = dest.with_suffix(".tmp")
        res.to_parquet(tmp, index=False)
        tmp.replace(dest)
        print(f"chunk {start:,}: {len(res):,} scored", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
