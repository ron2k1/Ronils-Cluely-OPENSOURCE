"""Checks for src/answer_format.py. Run with the venv python:
    .venv\\Scripts\\python.exe tests\\test_answer_format.py
Exits 0 + prints OK on success; prints FAIL and exits 1 otherwise."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from answer_format import opens_with_block, tidy  # noqa: E402

ANSWER = """### Problem (say this first):
I need every board where $n$ queens don't attack, in $O(n!)$ time.

```python
cols = set()
price = "$5"  # $x$ inside code stays
```

### Read aloud (line by line):
* `cols = set()` — I'm tracking used columns. That makes the check $O(1)$.
- `        board = [["."] * n for _ in range(n)]` – An $n \\times n$ grid of dots.
`return` — I hand the result back.
  It's the full list.
"""


def check_read_aloud_entries_split_and_spaced():
    out = tidy(ANSWER)
    script = out.split("Read aloud (line by line):", 1)[1]
    assert "`cols = set()`  \nI'm tracking used columns." in script, script
    assert '`board = [["."] * n for _ in range(n)]`  \nAn n × n grid of dots.' in script, \
        "bullet and indentation dropped, dash variant handled"
    assert "`return`  \nI hand the result back.\n  It's the full list." in script, \
        "a wrapped explanation stays with its entry"
    entries = [e for e in script.strip().split("\n\n") if e.strip()]
    assert len(entries) == 3, f"each entry is its own paragraph: {entries!r}"


def check_latex_outside_code_becomes_plain():
    out = tidy(ANSWER)
    assert "where n queens" in out and "in O(n!) time" in out, out
    assert "the check O(1)." in out, out
    assert 'price = "$5"  # $x$ inside code stays' in out, "fenced code untouched"


def check_money_is_not_math():
    text = "It costs $5 or $6 a month, and $10M ARR."
    assert tidy(text) == text, tidy(text)


def check_common_tex_commands():
    text = r"Runs in $O(n \log n)$, needs $k \le n$, uses $n^{2}$ cells, $a \cdot b$."
    assert tidy(text) == "Runs in O(n log n), needs k ≤ n, uses n^2 cells, a · b.", tidy(text)


def check_no_script_no_change():
    text = "Just a plain answer.\n\n- one\n- two"
    assert tidy(text) == text


# What follows the script: a bold pseudo-heading, a JS fence whose template literal
# looks like an entry, a fence with a blank line and a comment in it, then a rule.
# None of it may change.
AFTER_SCRIPT = ("**Read aloud (line by line):**\n`x = 1` — I set x.\n\n"
                "**Approach 2:**\n```js\nconst msg =\n  `hi`\n```\n\n"
                "```python\ndef f(s):\n    a = 1\n\n    # base case\n"
                "    return a\n```\n\n---\nDone.")


def check_text_after_script_is_untouched():
    out = tidy(AFTER_SCRIPT)
    tail = AFTER_SCRIPT.split("I set x.", 1)[1]
    assert out.endswith("I set x." + tail), repr(out)


def check_partial_stream_is_safe():
    for cut in range(0, len(ANSWER), 7):
        tidy(ANSWER[:cut])  # mid-stream text must never raise
    for cut in range(len(AFTER_SCRIPT) + 1):
        part = AFTER_SCRIPT[:cut]
        if "```" in part:  # a fence, closed or still streaming, comes through as is
            out = tidy(part)
            assert out[out.index("```"):] == part[part.index("```"):], repr(part)


def check_tex_groups_keep_meaning():
    text = r"Uses $O(2^{n/2})$ time, $\frac{n(n-1)}{2}$ pairs, $\sqrt{x+1}$ and $\sqrt{n}$."
    assert tidy(text) == "Uses O(2^(n/2)) time, (n(n-1))/2 pairs, √(x+1) and √n.", tidy(text)


def check_block_openers():
    for text in ["### Problem", "```python", "- one", "* one", "+ one", "1. First",
                 "2) Second", "| a | b |", "> quoted", "  ### indented"]:
        assert opens_with_block(text), text
    for text in ["Hello", "1.5 is the answer", "**Bold** start", "*italic*", "$5 total"]:
        assert not opens_with_block(text), text


def check_heading_after_script_ends_it():
    text = ("### Read aloud (line by line):\n`x = 1` — Set x.\n\n### Follow-up\n"
            "`y` — not an entry.")
    out = tidy(text)
    assert out.endswith("### Follow-up\n`y` — not an entry."), out


def check_code_then_explanation_on_next_line():
    text = ("**Read aloud (line by line):**\n- `return res`\n  Hands back every board.\n"
            "`x` is prose, not an entry.")
    out = tidy(text)
    assert out == ("**Read aloud (line by line):**\n\n`return res`  \n  Hands back every board.\n"
                   "`x` is prose, not an entry."), repr(out)


CHECKS = [
    check_code_then_explanation_on_next_line,
    check_read_aloud_entries_split_and_spaced, check_latex_outside_code_becomes_plain,
    check_money_is_not_math, check_common_tex_commands, check_no_script_no_change,
    check_partial_stream_is_safe, check_heading_after_script_ends_it,
    check_text_after_script_is_untouched, check_tex_groups_keep_meaning, check_block_openers,
]

if __name__ == "__main__":
    try:
        for check in CHECKS:
            check()
        print("OK")
    except AssertionError as e:
        print(f"FAIL: {e}"); sys.exit(1)
    except Exception as e:
        print(f"FAIL (error): {type(e).__name__}: {e}"); sys.exit(1)
