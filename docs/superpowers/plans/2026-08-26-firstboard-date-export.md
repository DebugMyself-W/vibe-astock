# First-board Date and Export Implementation Plan

**Goal:** Implement the user-approved date picker and per-session Markdown export.

**Architecture:** Keep automatic eight-day pool discovery as the default. Explicit dates use a validated single-session request and separate cache keys. Completed local analyses remain scoped by trading date and namespace; exports never include partial output.

**Tech Stack:** Python/FastAPI, React/TypeScript, pytest, Playwright.

## Accepted Design

- Date input replaces the displayed trading date; reset returns to automatic discovery.
- Manual dates do not fall back. Missing or mismatched upstream data is unavailable, not zero.
- Date changes cancel analysis work and ignore late list responses. Refresh preserves the selected mode.
- Download one `首板分析_YYYY-MM-DD.md` for all completed analyses of that date, independently of row selection. Include names when available, codes and full Markdown.
- Keep existing five-recorded-date browser retention. Persist optional stock names for future exports; old records remain compatible.
- No credentials are edited. The official key instructions were already provided to the user.

## Execution

1. Extend `TestFirstBoardReasons` in `tests/test_core_logic.py`: explicit-day fetch, invalid/future input, no fallback, cache isolation and automatic fallback. Run the new cases before implementation and confirm failure.
2. Update `vr/firstboard.py` and `vr/app.py`: optional date argument, real calendar validation, strict observed pool for explicit dates, per-mode/date caches, structured empty/unavailable responses. Re-run `pytest tests/test_core_logic.py -k FirstBoard`.
3. Extend `tests/firstboard_browser.cjs`: seeded records for two dates, actual download contents, missing history, request races, historical prompts, and cancelled jobs. Verify the date control is missing before implementing.
4. Update `frontend/src/lib/api.ts`, `pages/FirstBoard.tsx`, `components/ui/DeepDive.tsx`; add pure `lib/firstboard-export.ts`. Keep automatic requests parameter-free; explicit requests use `?date=YYYY-MM-DD`.
5. Run frontend build and browser suite at desktop, tablet and mobile sizes; use mocked model responses to avoid consuming quota. Inspect screenshots and downloaded files.
6. Apply only this task's patch to the actual WSL project, build there, restart only the 8910 service as needed, and verify the live API and UI. Preserve other work and stored analyses. Update README with controls and limits.

No commit, merge or unrelated refactoring is part of this task.
