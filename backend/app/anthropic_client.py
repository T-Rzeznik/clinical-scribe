"""The Anthropic API client — ONE async client for the whole app.

Created at import time and reused across every request, exactly like the DB
engine in db.py: the client owns an HTTP connection pool to the Anthropic API,
so making a fresh one per request would throw away pooled connections and pay a
new TLS handshake each time.

`AsyncAnthropic` (not `Anthropic`) because our request handlers are `async def`
running on the event loop — a synchronous client would block that loop during
the (relatively long) generation call and stall every other request.
"""

from anthropic import AsyncAnthropic

from app.config import settings

# The SDK also auto-reads ANTHROPIC_API_KEY from the environment, but we pass it
# explicitly from our typed settings so there's ONE documented source of truth
# (the .env locally, Secrets Manager in prod) — same pattern as database_url.
client = AsyncAnthropic(api_key=settings.anthropic_api_key)

# The model is a deliberate architecture decision (see docs/architecture.md):
# claude-sonnet-5 balances quality, latency, and cost for real-time streaming.
# MODEL = "claude-sonnet-5"
# Temporarily on Haiku 4.5 — cheaper/faster for local dev; swap back before ship.
MODEL = "claude-haiku-4-5"
