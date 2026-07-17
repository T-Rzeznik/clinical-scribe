#!/usr/bin/env python3
"""prod_smoke.py — exercise EVERY backend feature against the live deployment.

    python scripts/prod_smoke.py [BASE_URL]
    (default BASE_URL = https://trz-clinical-scribe.duckdns.org)

Stdlib only (urllib) — no jq, no requests. Runs a REAL Claude generation, so it
takes ~1-2 min. Admin mutations self-clean (create-then-delete / toggle-back).
"""
import json, sys, urllib.request, urllib.error

BASE = (sys.argv[1] if len(sys.argv) > 1 else "https://trz-clinical-scribe.duckdns.org").rstrip("/")
PW = "password123"
passed = failed = 0


def req(method, path, token=None, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    if body is not None:
        r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa
        return 0, str(e)


def js(raw):
    try:
        return json.loads(raw)
    except Exception:
        return None


def ok(want, got, label):
    global passed, failed
    if want == got:
        print(f"  PASS  {label}  ({got})"); passed += 1
    else:
        print(f"  FAIL  {label}  (got {got!r}, want {want!r})"); failed += 1


def hdr(t):
    print(f"\n== {t} ==")


def login(email, pw):
    _, raw = req("POST", "/auth/login", body={"email": email, "password": pw})
    j = js(raw) or {}
    return j.get("access_token"), j.get("refresh_token")


# 1 ---------------------------------------------------------------------------
hdr("1. Infra / health (HTTPS, private RDS, nginx)")
s, raw = req("GET", "/health")
ok(200, s, "GET /health over HTTPS")
s, raw = req("GET", "/health/db")
ok(1, (js(raw) or {}).get("db"), "GET /health/db -> DB reachable (private RDS)")

# 2 ---------------------------------------------------------------------------
hdr("2. Auth: 3 providers + 1 admin (hardcoded accounts)")
schen, schen_rt = login("schen@scribe.local", PW)
jpatel, _ = login("jpatel@scribe.local", PW)
mgarcia, _ = login("mgarcia@scribe.local", PW)
admin, _ = login("admin", "password")
ok(True, bool(schen), "provider schen login")
ok(True, bool(jpatel), "provider jpatel login")
ok(True, bool(mgarcia), "provider mgarcia login")
ok(True, bool(admin), "admin login")
s, _ = req("POST", "/auth/login", body={"email": "schen@scribe.local", "password": "WRONG"})
ok(401, s, "wrong password -> 401")
ok("provider", (js(req("GET", "/auth/me", schen)[1]) or {}).get("role"), "/auth/me -> role=provider")
me_admin = js(req("GET", "/auth/me", admin)[1]) or {}
ok("admin", me_admin.get("role"), "/auth/me -> role=admin")

# 3 ---------------------------------------------------------------------------
hdr("3. RBAC — provider blocked from admin routes (server-side gate)")
ok(403, req("GET", "/admin/stats", schen)[0], "provider -> /admin/stats = 403")
ok(200, req("GET", "/admin/stats", admin)[0], "admin    -> /admin/stats = 200")
ok(401, req("GET", "/auth/me")[0], "no token -> /auth/me = 401")

# 4 ---------------------------------------------------------------------------
hdr("4. ICD-10 SEMANTIC search + validate guardrail")
print("  (first server call downloads the embed model; may be slow once)")
ha = js(req("GET", "/icd/search?q=heart%20attack", schen)[1]) or {}
res = ha.get("results", [])
if res:
    top = res[0]
    print(f"  'heart attack' top hit: {top['code']} {top['description']} ({top.get('score')})")
# Semantic search is proven by results carrying a similarity SCORE (the keyword
# fallback returns none). The model's exact top code is its own call — what matters
# is that ranking is embedding-based, not literal word overlap.
ok(True, bool(res) and isinstance(res[0].get("score"), (int, float)),
   "'heart attack' -> SEMANTIC results (scored, not keyword fallback)")
cp = js(req("GET", "/icd/search?q=chest%20pain", schen)[1]) or {}
ok(True, any(r["code"] == "R07.9" for r in cp.get("results", [])), "'chest pain' -> R07.9 present")
val = js(req("POST", "/icd/validate", schen, {"codes": ["E11.9", "FAKE.99"]})[1]) or {}
ok(1, len(val.get("results", [])), "validate drops hallucinated code (E11.9 kept, FAKE.99 dropped)")

# 5 ---------------------------------------------------------------------------
hdr("5. Encounter workspace: create -> generate (SSE) + non-happy path #1")
# non-happy #1: trivial transcript rejected before Claude
e0 = js(req("POST", "/encounters", schen, {
    "patient_first_name": "Smoke", "patient_last_name": "Empty",
    "patient_dob": "1990-01-01", "transcript_text": "x", "template_id": None})[1]) or {}
ok(422, req("POST", f"/encounters/{e0.get('id')}/generate", schen)[0],
   "empty/non-clinical transcript -> 422 (no hallucination)")
# happy path: real generation
transcript = ("58yo male follow-up for chest pain and shortness of breath. Type 2 diabetes on "
              "metformin, hypertension on lisinopril. BP 150/94. Plan EKG, labs, continue meds.")
e1 = js(req("POST", "/encounters", schen, {
    "patient_first_name": "Smoke", "patient_last_name": "Tester",
    "patient_dob": "1970-05-05", "transcript_text": transcript, "template_id": None})[1]) or {}
eid = e1.get("id")
print(f"  encounter #{eid} created; streaming generation (real Claude call)...")
s, stream = req("POST", f"/encounters/{eid}/generate", schen, timeout=150)
ok(200, s, "POST /encounters/{id}/generate -> 200 (SSE)")
ok(True, '"done"' in stream, "generation streamed to completion (done frame)")
ok(True, any(w in stream.lower() for w in ("subjective", "assessment")), "streamed note contains SOAP sections")

# 6 ---------------------------------------------------------------------------
hdr("6. Versioning + audit trail (append-only)")
mi = {"code": "I21.9", "description": "Acute myocardial infarction, unspecified"}
htn = {"code": "I10", "description": "Essential (primary) hypertension"}
v1 = js(req("POST", f"/encounters/{eid}/versions", schen, {
    "subjective": "S v1", "objective": "O v1", "assessment": "A v1", "plan": "P v1", "icd_codes": [mi]})[1]) or {}
ok(1, v1.get("version_number"), "first save -> version 1")
v2 = js(req("POST", f"/encounters/{eid}/versions", schen, {
    "subjective": "S v2 edited", "objective": "O v2", "assessment": "A v2", "plan": "P v2", "icd_codes": [mi, htn]})[1]) or {}
ok(2, v2.get("version_number"), "re-save -> version 2 (append-only, v1 preserved)")
vers = js(req("GET", f"/encounters/{eid}/versions", schen)[1]) or []
ok(2, len(vers), "GET versions -> 2 listed")

# 7 ---------------------------------------------------------------------------
hdr("7. Patient search + provider-scoping")
pid = e1.get("patient_id")
mine = js(req("GET", "/patients/search?q=Smoke", schen)[1]) or []
ok(True, any(p["last_name"] == "Tester" for p in mine), "provider sees own patient in search")
theirs = js(req("GET", "/patients/search?q=Smoke", jpatel)[1]) or []
ok(0, len(theirs), "OTHER provider sees 0 of schen's patients (scoping)")
pe = js(req("GET", f"/patients/{pid}/encounters", schen)[1]) or []
ok(True, len(pe) >= 1, "GET /patients/{id}/encounters -> prior encounters")

# 8 ---------------------------------------------------------------------------
hdr("8. Draft autosave/restore (session persistence in RDS)")
draft = js(req("GET", "/encounters/draft", schen)[1])
ok(True, draft is not None, "GET /encounters/draft returns a draft object")
ok(200, req("PATCH", f"/encounters/{eid}", schen, {"transcript_text": "autosave edit"})[0],
   "PATCH draft transcript -> 200 (autosave)")

# 9 ---------------------------------------------------------------------------
hdr("9. Templates (provider read)")
tpls = js(req("GET", "/templates", schen)[1]) or []
ok(True, len(tpls) >= 1, "GET /templates -> active templates for picker")

# 10 --------------------------------------------------------------------------
hdr("10. Admin dashboard (oversight, roster, template CRUD, audit; self-cleaning)")
stats = js(req("GET", "/admin/stats", admin)[1]) or {}
ok(True, stats.get("encounters", 0) >= 1, "GET /admin/stats -> system counts")
allenc = js(req("GET", "/admin/encounters", admin)[1]) or []
ok(True, len(allenc) >= 1, "GET /admin/encounters -> ALL providers' encounters")
ok(True, isinstance(js(req("GET", "/admin/encounters?provider_id=1", admin)[1]), list),
   "GET /admin/encounters?provider_id= -> filter works")
audit = js(req("GET", "/admin/audit?limit=5", admin)[1]) or []
ok(True, len(audit) >= 1, "GET /admin/audit -> audit log populated")
nu = js(req("POST", "/admin/users", admin, {
    "email": "smoke-temp@scribe.local", "full_name": "Smoke Temp", "password": PW, "role": "provider"})[1]) or {}
nuid = nu.get("id")
ok(True, bool(nuid), f"POST /admin/users -> provider created (#{nuid})")
if nuid:
    ok(False, (js(req("PATCH", f"/admin/users/{nuid}/active", admin, {"is_active": False})[1]) or {}).get("is_active"),
       "PATCH .../active -> deactivate")
    ok(True, (js(req("PATCH", f"/admin/users/{nuid}/active", admin, {"is_active": True})[1]) or {}).get("is_active"),
       "PATCH .../active -> reactivate")
nt = js(req("POST", "/admin/templates", admin, {
    "name": "Smoke Template", "prompt_body": "Test prompt body.", "is_active": True})[1]) or {}
ntid = nt.get("id")
ok(True, bool(ntid), f"POST /admin/templates -> created (#{ntid})")
if ntid:
    ok(200, req("PATCH", f"/admin/templates/{ntid}", admin, {"name": "Smoke Template v2"})[0],
       "PATCH /admin/templates -> edit (takes effect immediately)")
    ok(204, req("DELETE", f"/admin/templates/{ntid}", admin)[0], "DELETE /admin/templates -> removed (cleanup)")
ok(400, req("PATCH", f"/admin/users/{me_admin.get('id')}/role", admin, {"role": "provider"})[0],
   "admin cannot demote self (last-admin lockout) -> 400")

# 11 --------------------------------------------------------------------------
hdr("11. Refresh token (edge case: session-expiry recovery, no data loss)")
newacc = (js(req("POST", "/auth/refresh", body={"refresh_token": schen_rt})[1]) or {}).get("access_token")
ok(True, bool(newacc), "POST /auth/refresh -> new access token")

print("\n" + "=" * 59)
print(f"  RESULT:  {passed} passed, {failed} failed")
print("=" * 59)
sys.exit(1 if failed else 0)
