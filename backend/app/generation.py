"""SOAP note generation — stream the note out of Claude, token by token.

This is the "streaming FROM Anthropic" half of the requirement. It's an async
GENERATOR: instead of building the whole note in memory and returning it, it
`yield`s each chunk of text the instant Claude produces it. Whoever consumes this
(the SSE route) can forward each chunk straight to the browser — so nothing is
ever fully buffered on our side.

It also runs a TOOL-USE loop: Claude may pause generation to call
`get_patient_history`. When it does, we run the tool, feed the result back, and
let Claude continue — all within the same stream to the browser.
"""

from collections.abc import AsyncGenerator, Awaitable, Callable

from app.anthropic_client import MODEL, client

# A SOAP note is short prose; 4096 tokens is comfortable headroom. We stream, so
# there's no HTTP-timeout worry from a large value anyway.
MAX_TOKENS = 4096

# The tool Claude may call mid-generation. NOTE the empty input schema: the model
# passes NO arguments. "Which patient / which provider" is decided server-side by
# the executor closure (see encounters.py), so the model can't reach another
# patient's records — the tool always means "THIS patient, MY notes".
PATIENT_HISTORY_TOOL = {
    "name": "get_patient_history",
    "description": (
        "Retrieve this patient's prior visit notes (SOAP) that you, the current "
        "provider, previously authored. Call this when the transcript references "
        "past history, ongoing conditions, prior diagnoses, or medication history, "
        "so the note is grounded in the patient's actual record. Returns prior "
        "notes newest-first, or a note that there are none."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

# A tool executor: given a tool name + the model's input, run it and return the
# text result. The route supplies this as a closure over the DB session + scope.
ToolExecutor = Callable[[str, dict], Awaitable[str]]


async def stream_soap_note(
    system_prompt: str,
    transcript: str,
    tool_executor: ToolExecutor,
) -> AsyncGenerator[dict, None]:
    """Yield generation events as Claude produces the note.

    Runs an agentic loop: each turn opens a stream, `yield`s the text as it
    arrives, then inspects the finished turn. If Claude stopped to call a tool
    (`stop_reason == "tool_use"`), we run the tool via `tool_executor`, append the
    result to the conversation, and loop for another turn. Otherwise we're done.

    Events are dicts: {"type": "text", "text": ...} for note text, or
    {"type": "reset"} emitted when a tool-use turn ends. A tool-use turn often
    contains conversational narration ("I'll look up the prior notes…") that is
    NOT part of the note; the reset tells the consumer to discard whatever it has
    shown so far, so only the FINAL answer turn ends up as the note.
    """
    messages: list[dict] = [{"role": "user", "content": transcript}]

    while True:
        async with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            tools=[PATIENT_HISTORY_TOOL],
            messages=messages,
        ) as stream:
            # Stream the visible text out as it's produced. During a tool-use turn
            # this may be empty (the model goes straight to the tool call).
            async for text_chunk in stream.text_stream:
                yield {"type": "text", "text": text_chunk}
            final = await stream.get_final_message()

        # No tool requested → this was the final answer; the loop ends.
        if final.stop_reason != "tool_use":
            break

        # This turn only led to a tool call — any text it streamed was narration.
        # Tell the consumer to throw it away before the real note streams next turn.
        yield {"type": "reset"}

        # Record Claude's turn verbatim (it holds the tool_use block we must answer).
        messages.append({"role": "assistant", "content": final.content})

        # Run each requested tool and collect its result. Each tool_result must
        # carry the matching tool_use_id so Claude can pair answer to request.
        tool_results: list[dict] = []
        for block in final.content:
            if block.type == "tool_use":
                output = await tool_executor(block.name, block.input)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": output,
                    }
                )
        # Feed the results back as the next user turn, then loop for Claude's
        # continued generation.
        messages.append({"role": "user", "content": tool_results})
