"""SOAP note generation — stream the note out of Claude, token by token.

This is the "streaming FROM Anthropic" half of the requirement. It's an async
GENERATOR: instead of building the whole note in memory and returning it, it
`yield`s each chunk of text the instant Claude produces it. Whoever consumes this
(the SSE route) can forward each chunk straight to the browser — so nothing is
ever fully buffered on our side.
"""

from collections.abc import AsyncGenerator

from app.anthropic_client import MODEL, client

# A SOAP note is short prose; 4096 tokens is comfortable headroom. We stream, so
# there's no HTTP-timeout worry from a large value anyway.
MAX_TOKENS = 4096


async def stream_soap_note(
    system_prompt: str, transcript: str
) -> AsyncGenerator[str, None]:
    """Yield the SOAP note in text chunks as Claude generates it.

    `system_prompt` is the template's prompt_body (the instructions); `transcript`
    is the visit text the provider pasted, sent as the user message.

    `client.messages.stream(...)` opens a streaming connection to Anthropic.
    `stream.text_stream` is an async iterator of just the text pieces — we
    `async for` over it (yielding control back to the event loop between chunks,
    so other requests keep being served) and re-`yield` each piece downstream.
    """
    async with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        thinking={"type": "disabled"},  # snappy first token; SOAP is a transform, not deep reasoning
        system=system_prompt,
        messages=[{"role": "user", "content": transcript}],
    ) as stream:
        async for text_chunk in stream.text_stream:
            yield text_chunk
