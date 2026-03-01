## Workflow

1. **Analyze** — Start from provided bug-candidates and/or reference diff. Form a concrete root-cause hypothesis before editing code.
2. **Fix** — Make a minimal, targeted edit. Generate diff with `git add -A && git diff --cached`.
3. **Verify** — Build and run test suite. If suitable reproducer inputs become available, use `libCRS run-pov`.
