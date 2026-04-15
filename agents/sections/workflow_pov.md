## Workflow

1. **Analyze** — Reproduce and inspect POV failures and/or static evidence. Explore code in `{source_dir}` to understand the vulnerability. Identify root cause before editing code.
2. **Setup** — Download a clean copy of the target source: `libCRS download-source target-source {work_dir}/clean-src`. This is the canonical tree for creating patches.
3. **Fix** — Edit the vulnerability in `{work_dir}/clean-src`. Generate diff with `cd {work_dir}/clean-src && git add -A && git diff --cached > {work_dir}/patch.diff`.
4. **Verify** — Build, test ALL POVs, run test suite. Only submit after all pass.
