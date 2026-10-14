"""Tidy a model answer for the overlay's markdown panel.

The panel (QTextEdit.setMarkdown) shows LaTeX as raw dollar signs, and a
read-aloud entry written as "`code` — explanation" runs together into one dense
line. tidy() fixes both on the text side: inline math outside code becomes plain
text, and each read-aloud entry becomes its own paragraph with the code line above
its explanation. It is safe on partial, mid-stream text.
"""
import re

# Fenced blocks (closed, or still streaming to the end of the text) and inline code.
_CODE = re.compile(r"(```.*?(?:```|\Z)|`[^`\n]*`)", re.S)
# $...$ or $$...$$ hugging non-space on both sides, so "$5 or $6" stays money.
_MATH = re.compile(r"\${1,2}(?=\S)([^$\n]{1,80}?)(?<=\S)\${1,2}(?!\w)")


def _group(s: str) -> str:
    """Parenthesize a multi-token group so dropping its braces keeps the meaning."""
    return s if re.fullmatch(r"\w+", s) else f"({s})"


_TEX = [
    (r"\\(?:text|mathrm|mathit|mathbf|operatorname)\{([^{}]*)\}", r"\1"),
    (r"([\^_])\{([^{}]*)\}", lambda m: m[1] + _group(m[2])),  # 2^{n/2} -> 2^(n/2)
    (r"\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}", lambda m: f"{_group(m[1])}/{_group(m[2])}"),
    (r"\\sqrt\{([^{}]*)\}", lambda m: "√" + _group(m[1])),
    (r"\\times\b", "×"), (r"\\cdot\b", "·"), (r"\\leq?\b", "≤"), (r"\\geq?\b", "≥"),
    (r"\\neq?\b", "≠"), (r"\\(?:to|rightarrow)\b", "→"), (r"\\infty\b", "∞"),
    (r"\\l?dots\b", "..."), (r"\\sqrt\b", "√"), (r"\\[,;:! ]", " "),
    (r"\\([A-Za-z]+)", r"\1"), (r"[{}]", ""),
]
_SCRIPT_HEAD = re.compile(r"^\s*(?:#+|\*\*)?\s*read aloud", re.I)
_FENCE = re.compile(r"^\s*(?:```|~~~)")
# "`code` — explanation", or "`code`" alone with the explanation on the next line.
_ENTRY = re.compile(r"^\s*(?:(?:[-*]|\d+[.)])\s+)?`([^`\n]+)`(?:\s*[—–:-]+\s*(.*?))?\s*$")
# A heading, fence, list, table, or quote: markdown only sees it at the start of a line.
_BLOCK_START = re.compile(r"\s*(?:#|```|~~~|[-*+]\s|\d+[.)]\s|\||>)")


def _plain(math: str) -> str:
    for pattern, repl in _TEX:
        math = re.sub(pattern, repl, math)
    return math


def _plain_math(text: str) -> str:
    parts = _CODE.split(text)  # the capture group puts code at odd indices
    return "".join(p if i % 2 else _MATH.sub(lambda m: _plain(m.group(1)), p)
                   for i, p in enumerate(parts))


def _split_script(text: str) -> str:
    out: list[str] = []
    in_script = in_fence = False
    for line in text.split("\n"):
        fence = bool(_FENCE.match(line))
        in_fence ^= fence
        if fence or in_fence:  # fenced code always passes through as is
            out.append(line)
            continue
        if not in_script:
            out.append(line)
            in_script = bool(_SCRIPT_HEAD.match(line))
            continue
        if line.lstrip().startswith("#"):  # a later heading ends the script
            in_script = False
        entry = _ENTRY.match(line) if in_script else None
        if entry:
            if out and out[-1].strip():
                out.append("")
            # Two trailing spaces = a hard break: code line, then the explanation under it.
            out.append(f"`{entry.group(1).strip()}`  ")
            if entry.group(2):
                out.append(entry.group(2))
        else:
            out.append(line)  # wrapped explanations, blank lines, and the rest stay put
    return "\n".join(out)


def opens_with_block(text: str) -> bool:
    """True when the answer starts with something markdown only sees at the start of
    a line, so the panel must put it on its own line under the speaker label."""
    return bool(_BLOCK_START.match(text))


def tidy(text: str) -> str:
    return _split_script(_plain_math(text))
