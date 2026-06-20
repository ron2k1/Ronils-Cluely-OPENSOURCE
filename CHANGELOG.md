# Changelog

All notable changes to Cluely are recorded here. Format based on Keep a Changelog.
Versioning is informal (personal tool).

## [Unreleased]

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
