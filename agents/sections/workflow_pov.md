## Workflow

1. **Analyze** — Reproduce and inspect POV failures and/or static evidence. Identify root cause before editing code.
2. **Fix** — Make a minimal, targeted edit. Generate diff with `git add -A && git diff --cached`.
3. **Verify** — Build, test ALL POVs, run test suite. Only submit after all pass.
