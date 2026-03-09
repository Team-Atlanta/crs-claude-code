## Workflow

1. **Analyze** — Start from the provided bug-candidates and/or diff files. Form a concrete root-cause hypothesis before editing code.
2. **Fix** — Make a minimal, targeted edit. Generate diff with `git add -A && git diff --cached`.
3. **Verify** — Build and run test suite. If reproducer inputs were provided at startup, use `libCRS run-pov`.
