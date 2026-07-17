"""Semantic ICD-10 search — in-memory cosine over local MiniLM embeddings.

This is the *semantic* search the architecture called for, built WITHOUT pgvector.
Instead of a Postgres VECTOR column + `ORDER BY embedding <=> query`, we do the same
math in the app process:

  1. Once, at first use, embed every catalog description with a local ONNX MiniLM
     model (`fastembed` — no PyTorch, and no external API at query time; the model
     weights are downloaded once and cached, then everything runs offline).
  2. Hold those vectors in a single L2-normalized numpy matrix (the "index").
  3. Per query, embed the query string and rank catalog rows by cosine similarity
     (a dot product, because the rows are pre-normalized).

Because the rows are unit vectors and the query is normalized, `matrix @ query` IS
the cosine of the angle between them — 1.0 = same meaning, ~0 = unrelated. This is a
drop-in for a pgvector backend: identical inputs/outputs, so `icd.py` and the
frontend never change when the store swaps to RDS + pgvector in prod.

The model is a module-level singleton loaded lazily under a lock — same pattern as
the DB engine (`db.py`) and the Anthropic client: one expensive resource, reused.
"""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

# Small, fast, 384-dim English model shipped as a quantized ONNX graph — runs on
# CPU with no PyTorch. Good enough for short clinical descriptions + short queries.
_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

# Stable on-disk cache so the ~90 MB weights survive a %TEMP% clean and aren't
# re-downloaded every reboot (fastembed's default lives under the temp dir).
_CACHE_DIR = str(Path.home() / ".fastembed_cache")

# Calibrated against real layman queries (see the build session): correct clinical
# matches score ~0.37-0.70; gibberish tops out ~0.29. A 0.35 floor cleanly separates
# signal from noise, so a nonsense query returns nothing instead of confident junk.
SCORE_FLOOR = 0.35

# Lazily-initialized singletons. `_matrix` doubling as the "is the index built?"
# flag keeps the fast path a single `is None` check after warm-up.
_model: TextEmbedding | None = None
_matrix: np.ndarray | None = None      # shape (N, dim), each row L2-normalized
_rows: list[dict] | None = None        # catalog rows, aligned with _matrix rows
_lock = threading.Lock()               # guards the one-time index build


def _normalize(m: np.ndarray) -> np.ndarray:
    """Scale each row to unit length so a dot product equals cosine similarity.

    Guard against a zero-length row (norm 0) to avoid a divide-by-zero — swap any
    zero norm for 1 (that row stays all-zeros and simply never matches).
    """
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return m / norms


def _embed(texts: list[str]) -> np.ndarray:
    """Run the model over a batch of strings -> (len(texts), dim) float32 matrix.

    `TextEmbedding.embed` yields one vector per input; we materialize the generator
    into a numpy array so we can do vectorized math on it.
    """
    return np.asarray(list(_model.embed(texts)), dtype=np.float32)


def _ensure_index(catalog: list[dict]) -> None:
    """Build the embedding index once (load model + embed all descriptions).

    Double-checked locking: the cheap `is not None` check outside the lock is the
    warm path taken on every request after the first; the lock + re-check ensures
    two concurrent first-requests don't both load the model.
    """
    global _model, _matrix, _rows
    if _matrix is not None:
        return
    with _lock:
        if _matrix is not None:
            return
        _model = TextEmbedding(model_name=_MODEL_NAME, cache_dir=_CACHE_DIR)
        vectors = _embed([row["description"] for row in catalog])
        _matrix = _normalize(vectors)
        _rows = list(catalog)


def search(query: str, catalog: list[dict], limit: int = 5) -> list[dict]:
    """Rank catalog rows by cosine similarity to the query.

    Returns `[{code, description, score}, ...]` highest-first, dropping anything
    below SCORE_FLOOR. Raises if the model/index can't be built (offline first run,
    etc.) — the caller (`icd.py`) catches that and falls back to keyword search.
    """
    q = query.strip()
    if not q:
        return []

    _ensure_index(catalog)

    q_vec = _embed([q])[0]
    q_norm = np.linalg.norm(q_vec)
    if q_norm == 0:
        return []
    q_vec = q_vec / q_norm

    scores = _matrix @ q_vec               # cosine similarity for every row at once
    order = np.argsort(-scores)            # indices, best score first

    results: list[dict] = []
    for i in order[:limit]:
        score = float(scores[i])
        if score < SCORE_FLOOR:
            break                          # sorted desc -> nothing after this clears it
        row = _rows[i]
        results.append(
            {"code": row["code"], "description": row["description"], "score": round(score, 4)}
        )
    return results
