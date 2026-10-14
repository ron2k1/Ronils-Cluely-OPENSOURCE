"""Checks for the Gemini backend in src/brain.py. Run with the venv python:
    .venv\\Scripts\\python.exe tests\\test_brain.py          (offline, fake client)
    .venv\\Scripts\\python.exe tests\\test_brain.py --live   (also hits the real API)
Exits 0 + prints OK on success; prints FAIL and exits 1 otherwise.
The offline checks feed real google-genai response objects through a fake client,
so stream parsing is tested against the SDK's actual types with no network.
--live needs GEMINI_API_KEY and spends a few tokens."""
import base64
import os
import re
import sys
import time

# Pin the documented defaults so a developer's own env overrides don't skew the checks.
for var in ("CLUELY_MODEL", "CLUELY_THINKING", "CLUELY_AUTO_MODEL",
            "CLUELY_AUTO_THINKING", "CLUELY_HISTORY_TURNS", "CLUELY_ANSWER_TIMEOUT",
            "CLUELY_AUTO_TIMEOUT"):
    os.environ.pop(var, None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from google.genai import errors, types  # noqa: E402

import brain  # noqa: E402

PNG_1PX = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)).decode("ascii")


def _chunk(text, thought=False, finish=None):
    part = types.Part(text=text, thought=thought or None)
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role="model", parts=[part]), finish_reason=finish)])


def _blocked_chunk():
    return types.GenerateContentResponse(prompt_feedback=types.GenerateContentResponsePromptFeedback(
        block_reason=types.BlockedReason.SAFETY))


class FakeModels:
    def __init__(self, replies, get_error=None):
        self.replies = list(replies)   # one list of chunks per call
        self.calls = []
        self.looked_up = []
        self.get_error = get_error

    def generate_content_stream(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": list(contents), "config": config})
        yield from self.replies.pop(0)

    def get(self, *, model):
        self.looked_up.append(model)
        if self.get_error:
            raise self.get_error


class FakeClient:
    def __init__(self, *replies, get_error=None):
        self.models = FakeModels(replies, get_error)


def _texts(content):
    return [p.text for p in content.parts if p.text]


def check_streaming_skips_thoughts():
    client = FakeClient([_chunk("plan", thought=True), _chunk("Hello"), _chunk(", world")])
    s = brain._Session("m", "high", client=client)
    seen = []
    out = s.ask("hi", on_text=seen.append)
    assert out == "Hello, world", f"joined answer was {out!r}"
    assert seen == ["Hello", ", world"], f"streamed chunks were {seen!r}"


def check_config_main_vs_lean():
    client = FakeClient([_chunk("a")], [_chunk("b")])
    brain._Session("gemini-x", "high", client=client).ask("q")
    brain._Session("gemini-y", "", lean=True, client=client).ask("q")
    main, lean = client.models.calls
    assert main["model"] == "gemini-x" and lean["model"] == "gemini-y", "model ids"
    assert main["config"].system_instruction == brain.SYSTEM_PREAMBLE, "main preamble"
    assert lean["config"].system_instruction == brain.LEAN_PREAMBLE, "lean preamble"
    level = main["config"].thinking_config.thinking_level
    assert level == types.ThinkingLevel.HIGH, f"thinking level was {level!r}"
    assert lean["config"].thinking_config is None, "empty thinking = model default"


def check_per_session_timeout():
    client = FakeClient([_chunk("a")])
    brain._Session("m", "", timeout_s=12.5, client=client).ask("q")
    cfg = client.models.calls[0]["config"]
    assert cfg.http_options.timeout == 12500, f"timeout was {cfg.http_options.timeout!r}"


def check_stop_reasons_reported():
    client = FakeClient([_chunk("", finish=types.FinishReason.RECITATION)], [_blocked_chunk()],
                        [_chunk("1: def f():"), _chunk("", finish=types.FinishReason.RECITATION)],
                        [_chunk("done", finish=types.FinishReason.STOP)])
    s = brain._Session("m", "", client=client)
    for expected in ("RECITATION", "SAFETY"):
        try:
            s.ask("q")
        except RuntimeError as exc:
            assert expected in str(exc), f"empty-answer error was {exc}"
        else:
            raise AssertionError(f"an empty {expected} answer should raise")
    seen = []
    out = s.ask("q", on_text=seen.append)
    assert out.startswith("1: def f():") and "RECITATION" in out, f"cut-off answer was {out!r}"
    assert "RECITATION" in seen[-1], "the cut-off note should stream to the panel too"
    assert s.ask("q") == "done", "a normal STOP adds no note"


def check_bad_thinking_level_rejected():
    try:
        brain._Session("m", "turbo", client=FakeClient([_chunk("x")])).ask("q")
    except ValueError:
        return
    raise AssertionError("an unknown thinking level should raise ValueError")


def check_image_and_context():
    client = FakeClient([_chunk("ok")])
    brain._Session("m", "", client=client).ask(
        "solve it", transcript="Them: what is two plus two", notes="remember X",
        image_b64=PNG_1PX)
    (turn,) = client.models.calls[0]["contents"]
    img, txt = turn.parts
    assert img.inline_data.mime_type == "image/png", "image mime"
    assert img.inline_data.data == base64.b64decode(PNG_1PX), "image bytes"
    for needle in ("what is two plus two", "remember X", "solve it"):
        assert needle in txt.text, f"{needle!r} missing from the prompt"


def check_history_text_only_and_bounded():
    client = FakeClient([_chunk("A1")], [_chunk("A2")], [_chunk("A3")])
    s = brain._Session("m", "", history_turns=1, client=client)
    s.ask("Q1", transcript="LONG TRANSCRIPT", image_b64=PNG_1PX)
    s.ask("Q2")
    second = client.models.calls[1]["contents"]
    assert [c.role for c in second] == ["user", "model", "user"], "history roles"
    assert "Q1" in _texts(second[0])[0], "history keeps the question"
    assert "LONG TRANSCRIPT" not in _texts(second[0])[0], "history drops the transcript"
    assert all(p.inline_data is None for p in second[0].parts), "history drops the image"
    assert _texts(second[1]) == ["A1"], "history keeps the answer"
    s.ask("Q3")
    third = client.models.calls[2]["contents"]
    assert len(third) == 3 and "Q2" in _texts(third[0])[0], "history bounded to 1 turn"


def check_empty_answer_raises_and_keeps_history():
    client = FakeClient([_chunk("A1")], [_chunk("thinking only", thought=True)], [_chunk("A3")])
    s = brain._Session("m", "", client=client)
    s.ask("Q1")
    try:
        s.ask("Q2")
    except RuntimeError:
        pass
    else:
        raise AssertionError("an empty answer should raise RuntimeError")
    s.ask("Q3")
    third = client.models.calls[2]["contents"]
    assert len(third) == 3, "a failed turn must not be added to history"


def check_reset_clears_history():
    client = FakeClient([_chunk("A1")], [_chunk("A2")])
    s = brain._Session("m", "", client=client)
    s.ask("Q1")
    s.reset()
    s.ask("Q2")
    assert len(client.models.calls[1]["contents"]) == 1, "reset should drop history"


def check_missing_key_message():
    saved = os.environ.pop(brain.API_KEY_ENV, None)
    brain._client = None
    try:
        brain._shared_client()
    except brain.MissingKeyError as exc:
        assert brain.API_KEY_ENV in str(exc), "error should name the env var"
    else:
        raise AssertionError("a missing key should raise RuntimeError")
    finally:
        brain._client = None
        if saved is not None:
            os.environ[brain.API_KEY_ENV] = saved


def check_warm_reports_problems():
    saved_client, saved_key = brain._client, os.environ.pop(brain.API_KEY_ENV, None)
    try:
        brain._client = FakeClient()
        assert brain.warm() == "", "a healthy warm-up reports nothing"
        assert brain._client.models.looked_up == [brain._main._model, brain._auto._model], \
            "warm-up looks up both models"
        brain._client = FakeClient(get_error=errors.APIError(
            404, {"error": {"code": 404, "status": "NOT_FOUND", "message": "x"}}))
        problem = brain.warm()
        assert "404" in problem and "NOT_FOUND" in problem, f"API error status was {problem!r}"
        brain._client = None
        assert brain.API_KEY_ENV in brain.warm(), "missing key named in the status"
        brain._client = FakeClient()
        saved_thinking, brain._main._thinking = brain._main._thinking, "hgih"
        try:
            assert "hgih" in brain.warm(), "a thinking-level typo shows at launch"
        finally:
            brain._main._thinking = saved_thinking
    finally:
        brain._client = saved_client
        if saved_key is not None:
            os.environ[brain.API_KEY_ENV] = saved_key


def check_default_wiring():
    assert brain._main._model == "gemini-3.8-flash", brain._main._model
    assert brain._main._thinking == "high", brain._main._thinking
    assert not brain._main._lean, "main session uses the full preamble"
    assert brain._auto._model == "gemini-3.5-flash-lite", brain._auto._model
    assert brain._auto._lean, "auto session uses the lean preamble"
    # High thinking on a hard problem sent nothing for up to 147s (measured), so
    # the coding path needs a long timeout; voice keeps a short one.
    assert brain._main._timeout_s == 300, brain._main._timeout_s
    assert brain._auto._timeout_s == 30, brain._auto._timeout_s


def _fenced_code(answer):
    return "\n".join(re.findall(r"```[\w+-]*\n(.*?)```", answer, re.S))


def live():
    """Real API: one coding answer on the strong path, one voiced answer on the fast path."""
    t0 = time.perf_counter()
    out = brain.ask("Write a Python function is_palindrome(s) that ignores case and "
                    "non-alphanumerics.")
    t_main = time.perf_counter() - t0
    code = _fenced_code(out)
    assert "def is_palindrome" in code, "strong path returned no function in a code block"
    numbered = [ln for ln in code.splitlines() if re.match(r"\s*\d+[:.]?\s", ln)]
    assert not numbered, f"code has line numbers, so it won't paste: {numbered[:2]!r}"
    scope = {}
    exec(compile(code, "answer", "exec"), scope)  # the code must paste and run as is
    assert scope["is_palindrome"]("A man, a plan, a canal: Panama"), "pasted code is wrong"
    t0 = time.perf_counter()
    fast = brain.ask("What is 17 times 23? Reply with just the number.", fast=True)
    t_fast = time.perf_counter() - t0
    assert "391" in fast, f"fast path answered {fast[:80]!r}"
    print(f"live: main {t_main:.1f}s ({brain._main._model}/{brain._main._thinking}), "
          f"fast {t_fast:.1f}s ({brain._auto._model})")


CHECKS = [
    check_streaming_skips_thoughts, check_config_main_vs_lean, check_per_session_timeout,
    check_stop_reasons_reported, check_bad_thinking_level_rejected, check_image_and_context,
    check_history_text_only_and_bounded, check_empty_answer_raises_and_keeps_history,
    check_reset_clears_history, check_missing_key_message, check_warm_reports_problems,
    check_default_wiring,
]


def main():
    for check in CHECKS:
        check()
    if "--live" in sys.argv:
        live()
    print("OK")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"FAIL: {e}"); sys.exit(1)
    except Exception as e:
        print(f"FAIL (error): {type(e).__name__}: {e}"); sys.exit(1)
