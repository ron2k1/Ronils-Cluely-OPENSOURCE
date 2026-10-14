# Changelog

All notable changes to Cluely are recorded here. Format based on Keep a Changelog.
Versioning is informal (personal tool).

## [Unreleased]

## [0.4.0] - 2026-10-14
### Changed
- Answers come from the Gemini API instead of a warm headless `claude` process.
  Typed, screen, and image questions use `gemini-3.8-flash` at thinking `high`;
  voiced auto-answers use `gemini-3.5-flash-lite`. Calls are stateless, and each
  session keeps its last 3 exchanges in memory as text for follow-ups.
- Requires `GEMINI_API_KEY`. The `claude` CLI is no longer used.
- Panel labels say Gemini instead of Claude.
- Code in answers has no line numbers, so it pastes and runs as is. The numbers
  shifted indentation from line 10 on and broke copied code. The read-aloud
  script now quotes each code line instead of saying "Line 4".
- The read-aloud script gives each code line two to three sentences (what it
  does, why it's needed, and how the key lines fit the approach) instead of one.
- `CLUELY_ANSWER_TIMEOUT` now covers typed, screen, and image questions only, and
  defaults to 300s (was 90s). At thinking `high`, a hard problem was measured
  sending nothing for 119 to 147s before its first word.
### Added
- `CLUELY_MODEL`, `CLUELY_THINKING`, `CLUELY_AUTO_THINKING`,
  `CLUELY_HISTORY_TURNS`. `CLUELY_AUTO_MODEL` now names a Gemini model.
- `CLUELY_AUTO_TIMEOUT` (default 30s) for voiced auto-answers, so a stall can't
  hold up the auto-answer queue.
- Launch-time check of the key, both model names, and the thinking levels. A
  missing or rejected key or a mistyped level shows on the status line.
- An answer Gemini cuts off early (for example `RECITATION` or `MAX_TOKENS`) ends
  with a note saying so, so incomplete code isn't mistaken for a full answer.
- `tests/test_brain.py`: offline checks against the SDK's real response types,
  plus `--live` for a real API round-trip.
### Removed
- `CLUELY_CLAUDE_CMD` and `CLUELY_CLAUDE_MODEL`.

## [0.3.0] - 2026-06-20
### Changed
- Public release. The repository is now public.
- Notes are local-only. `notes/` is gitignored and never committed, reversing the
  0.2.0 decision to version it. Meeting transcripts hold other participants' words
  and must not leave your machine.
### Added
- `LICENSE` (Apache-2.0).
- `CLUELY_MY_NAME` so auto-answer can tell a question is aimed at you by name.
  No personal name is hardcoded in the source anymore.
- README setup section: Windows requirements, venv, `pip install`, and the
  `claude` CLI login, plus a data-flow diagram.
### Fixed
- The `claude` CLI is resolved from PATH first, so Cluely works whether the CLI
  came from the native installer, winget, or npm. `CLUELY_CLAUDE_CMD` still
  overrides.
- `scripts/install_shortcut.ps1` derives its base path at runtime instead of a
  hardcoded user path, so the launcher works from any clone location.
### Removed
- Internal design and planning docs are no longer tracked in the repo.

## [0.2.0] - 2026-06-20
### Added
- On-screen power toggle (⏻ OFF/LIVE) and a visible close (×) button on the overlay.
- Console-less Desktop + Start Menu launcher shortcut with a generated app icon.
- This repository, with dated history and this changelog.
### Notes
- Notes versioning was enabled here. It was reversed in 0.3.0: notes are
  local-only and never committed.

## [0.1.0] - 2026-06-19
### Added
- Initial Cluely: capture-invisible PySide6 overlay (SetWindowDisplayAffinity /
  WDA_EXCLUDEFROMCAPTURE), warm headless `claude` backend (no API key), live WASAPI
  loopback + mic transcription via faster-whisper, auto-answer of questions aimed at
  you, on-demand screen vision + image attach, notes tab, and global hotkeys.
