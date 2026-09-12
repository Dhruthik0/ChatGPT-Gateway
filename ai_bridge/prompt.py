import secrets

_TAIL = (
    "Respond with only the result. Do not add preamble, explanation of your "
    "process, or follow-up questions."
)


def build(instruction: str, input_text: str | None) -> str:
    """Frame the caller's input as data, not instruction.

    Not a security boundary -- an LLM can still be talked out of it -- but it
    removes the common accidental case of input that reads like a command.
    """
    instruction = instruction.strip()
    if not input_text or not input_text.strip():
        return f"{instruction}\n\n{_TAIL}"

    open_d, close_d = "--- INPUT ---", "--- END INPUT ---"
    if open_d in input_text or close_d in input_text:
        nonce = secrets.token_hex(4)
        open_d, close_d = f"--- INPUT-{nonce} ---", f"--- END INPUT-{nonce} ---"

    return f"{instruction}\n\n{open_d}\n{input_text}\n{close_d}\n\n{_TAIL}"
