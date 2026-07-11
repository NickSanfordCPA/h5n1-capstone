# modal_jobs/

Modal app definitions for scaled GPU work (sentiment scoring, image embeddings).
Each job imports the SAME `h5n1.*` library functions the notebooks use — it only
adds scale, GPU, and I/O. Stood up in the sentiment phase, not now.

Run pattern: `modal run modal_jobs/score.py`
