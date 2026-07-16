"""ICD-10 code search — in-memory keyword fallback (pre-pgvector).

The architecture calls for semantic search via pgvector: embed ~200-300 codes,
embed the query, `ORDER BY embedding <=> query`. pgvector has no official Windows
binary and isn't installed on the local Postgres yet, so this module is the
documented FALLBACK: a curated in-memory catalog scored by keyword overlap.

The public shape (a `search(query, limit)` returning ranked `{code, description}`)
is deliberately the same one a pgvector-backed version would expose, so swapping
the implementation later is a drop-in — the route and frontend don't change.
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.deps import get_current_user
from app.models import User

router = APIRouter(prefix="/icd", tags=["icd"])

# A small curated slice of common ICD-10 codes. In the pgvector version this is a
# DB table of ~200-300 rows with an embedding column; here it's an in-memory list.
ICD10_CATALOG: list[dict] = [
    {"code": "E11.9", "description": "Type 2 diabetes mellitus without complications"},
    {"code": "E11.65", "description": "Type 2 diabetes mellitus with hyperglycemia"},
    {"code": "E11.22", "description": "Type 2 diabetes mellitus with diabetic chronic kidney disease"},
    {"code": "E10.9", "description": "Type 1 diabetes mellitus without complications"},
    {"code": "E78.5", "description": "Hyperlipidemia, unspecified"},
    {"code": "I10", "description": "Essential (primary) hypertension"},
    {"code": "I25.10", "description": "Atherosclerotic heart disease of native coronary artery without angina"},
    {"code": "I48.91", "description": "Unspecified atrial fibrillation"},
    {"code": "I50.9", "description": "Heart failure, unspecified"},
    {"code": "J45.909", "description": "Unspecified asthma, uncomplicated"},
    {"code": "J44.9", "description": "Chronic obstructive pulmonary disease, unspecified"},
    {"code": "J06.9", "description": "Acute upper respiratory infection, unspecified"},
    {"code": "J02.9", "description": "Acute pharyngitis, unspecified"},
    {"code": "J20.9", "description": "Acute bronchitis, unspecified"},
    {"code": "R51.9", "description": "Headache, unspecified"},
    {"code": "G43.909", "description": "Migraine, unspecified, not intractable, without status migrainosus"},
    {"code": "R05.9", "description": "Cough, unspecified"},
    {"code": "R50.9", "description": "Fever, unspecified"},
    {"code": "R10.9", "description": "Unspecified abdominal pain"},
    {"code": "R07.9", "description": "Chest pain, unspecified"},
    {"code": "M54.5", "description": "Low back pain"},
    {"code": "M25.561", "description": "Pain in right knee"},
    {"code": "M25.562", "description": "Pain in left knee"},
    {"code": "K21.9", "description": "Gastro-esophageal reflux disease without esophagitis"},
    {"code": "K59.00", "description": "Constipation, unspecified"},
    {"code": "N39.0", "description": "Urinary tract infection, site not specified"},
    {"code": "F41.1", "description": "Generalized anxiety disorder"},
    {"code": "F32.9", "description": "Major depressive disorder, single episode, unspecified"},
    {"code": "F41.9", "description": "Anxiety disorder, unspecified"},
    {"code": "E66.9", "description": "Obesity, unspecified"},
    {"code": "E03.9", "description": "Hypothyroidism, unspecified"},
    {"code": "D64.9", "description": "Anemia, unspecified"},
    {"code": "Z00.00", "description": "Encounter for general adult medical examination without abnormal findings"},
    {"code": "Z23", "description": "Encounter for immunization"},
    {"code": "B34.9", "description": "Viral infection, unspecified"},
    {"code": "L03.90", "description": "Cellulitis, unspecified"},
    {"code": "T78.40XA", "description": "Allergy, unspecified, initial encounter"},
    {"code": "J30.9", "description": "Allergic rhinitis, unspecified"},
    {"code": "J30.1", "description": "Allergic rhinitis due to pollen (seasonal)"},
    {"code": "J30.2", "description": "Other seasonal allergic rhinitis"},
    {"code": "R06.02", "description": "Shortness of breath"},
    {"code": "Z47.1", "description": "Aftercare following joint replacement surgery"},
    {"code": "Z47.89", "description": "Encounter for other orthopedic aftercare"},
    {"code": "Z98.890", "description": "Other specified postprocedural states"},
    {"code": "R53.83", "description": "Other fatigue"},
    {"code": "R42", "description": "Dizziness and giddiness"},
    {"code": "R11.2", "description": "Nausea with vomiting, unspecified"},
]

# Pre-tokenize each description once at import time so search doesn't re-split
# 40 strings on every request. (In the pgvector version this is precomputed embeddings.)
_INDEX: list[tuple[dict, set[str]]] = [
    (row, set(row["description"].lower().replace(",", " ").replace("-", " ").split()))
    for row in ICD10_CATALOG
]

# Fast exact lookup by code, so we can validate AI-suggested codes against the
# catalog (uppercased key — codes are case-insensitive but conventionally upper).
_BY_CODE: dict[str, dict] = {row["code"].upper(): row for row in ICD10_CATALOG}


def validate_codes(codes: list[str]) -> list[dict]:
    """Filter a list of raw code strings down to the ones our catalog recognizes.

    The AI SUGGESTS codes; this is the guardrail that keeps a hallucinated or
    malformed code out of the record. We return the catalog's canonical
    `{code, description}` (not the model's text) and drop anything unknown,
    de-duplicating while preserving order.
    """
    seen: set[str] = set()
    valid: list[dict] = []
    for raw in codes:
        key = raw.strip().upper()
        row = _BY_CODE.get(key)
        if row is not None and key not in seen:
            seen.add(key)
            valid.append(row)
    return valid


def search(query: str, limit: int = 5) -> list[dict]:
    """Rank catalog codes by keyword overlap with the query.

    Scoring is deliberately simple (this is the fallback): +2 for each query token
    that appears as a whole word in the description, +1 for a substring match
    anywhere in the description. Codes with zero signal are dropped. A pgvector
    version would replace this with cosine distance over embeddings — same return
    shape, so callers don't change.
    """
    q = query.lower().strip()
    if not q:
        return []
    q_tokens = set(q.replace(",", " ").replace("-", " ").split())

    scored: list[tuple[int, dict]] = []
    for row, desc_tokens in _INDEX:
        score = 2 * len(q_tokens & desc_tokens)  # whole-word overlap
        if q in row["description"].lower():      # phrase substring bonus
            score += 1
        if score > 0:
            scored.append((score, row))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _score, row in scored[:limit]]


@router.get("/search")
async def search_icd(
    q: str = Query(..., min_length=1, description="Free-text symptom or diagnosis"),
    limit: int = Query(5, ge=1, le=20),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Suggest ICD-10 codes for a free-text query (authed).

    Returns `{"results": [{code, description}, ...]}`, highest-ranked first.
    """
    return {"results": search(q, limit)}


class ValidateRequest(BaseModel):
    """Body for POST /icd/validate — the raw codes the AI suggested."""

    codes: list[str]


@router.post("/validate")
async def validate_icd(
    body: ValidateRequest,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return only the AI-suggested codes that exist in our catalog (authed).

    Returns `{"results": [{code, description}, ...]}` with canonical descriptions,
    so the frontend can show trustworthy suggestions and never store a code we
    don't recognize.
    """
    return {"results": validate_codes(body.codes)}
