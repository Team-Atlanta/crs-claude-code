## Workflow

1. **Analyze** — Start from the provided bug-candidates and/or diff files. Explore code in `{source_dir}` to understand the vulnerability. Form a concrete root-cause hypothesis before editing code.
2. **Setup** — Download a clean copy of the target source: `libCRS download-source target-source {work_dir}/clean-src`. This is the canonical tree for creating patches.
3. **Fix** — Edit the vulnerability in `{work_dir}/clean-src`. Generate diff with `cd {work_dir}/clean-src && git add -A && git diff --cached > {work_dir}/patch.diff`.
4. **Verify** — Build and run test suite. If reproducer inputs were provided at startup, use `libCRS run-pov`.
