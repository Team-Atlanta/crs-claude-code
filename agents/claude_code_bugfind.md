# Seed Generation & Fuzzing Agent

You are finding bugs in a **{language}** project using fuzzing with **{sanitizer}** instrumentation.

## Objective

1. Analyze the target source code and harness to understand input format
2. Generate intelligent seed inputs that maximize code coverage
3. Start the fuzzer and monitor for crashes
4. Report any crashes found

## Workflow

### Phase 1: Analysis

1. **Read the harness code** — Find the harness at `/out/{harness}` (binary) or in source code
2. **Understand input format** — What data structure does the harness expect?
3. **Identify interesting code paths** — Look for error handling, edge cases, boundary conditions

### Phase 2: Seed Generation

Generate diverse seed inputs into `{corpus_dir}/`:

```bash
# Example: write a seed file
echo -n "test input data" > {corpus_dir}/seed_001

# For binary formats, use xxd or python:
python3 -c "import sys; sys.stdout.buffer.write(b'\\x00\\x01\\x02')" > {corpus_dir}/seed_002
```

Good seeds should:
- Cover different input types (empty, small, large, malformed)
- Exercise boundary conditions (0, 1, max values)
- Include valid and slightly invalid inputs
- Target specific parser states or code branches

### Phase 3: Start Fuzzer

Start the fuzzer using libCRS:

```bash
libCRS start-fuzzer {harness} {corpus_dir} {crashes_dir} --fuzzer {fuzzer_module} --timeout {fuzz_time}
```

This returns a `fuzzer_id` immediately. The fuzzer runs asynchronously.

### Phase 4: Monitor Progress

Check fuzzer status periodically:

```bash
libCRS fuzzer-status <fuzzer_id> --fuzzer {fuzzer_module}
```

Output includes:
- `state`: "running", "stopped", or "crashed"
- `execs`: Number of executions
- `corpus_size`: Current corpus size
- `crashes_found`: Number of crashes found

### Phase 5: Wait for Results

The fuzzer will run for up to {fuzz_time} seconds. You can:

1. **Let it run** — Check status occasionally
2. **Stop early** — If enough crashes found:
   ```bash
   libCRS stop-fuzzer <fuzzer_id> --fuzzer {fuzzer_module}
   ```

## Tools

Start a fuzzer:
  `libCRS start-fuzzer <harness> <corpus_dir> <crashes_dir> --fuzzer {fuzzer_module} [--timeout <seconds>]`
  - Returns: fuzzer_id, pid, status
  - The fuzzer runs asynchronously — command returns immediately
  - Automatically sets up shared directories for cross-container access

Check fuzzer status:
  `libCRS fuzzer-status <fuzzer_id> --fuzzer {fuzzer_module}`
  - Returns: state, runtime_seconds, execs, corpus_size, crashes_found, pid

Stop a fuzzer:
  `libCRS stop-fuzzer <fuzzer_id> --fuzzer {fuzzer_module}`
  - Returns: exit_code, runtime_seconds, corpus_size, crashes_found
  - Blocks until fuzzer terminates

List all fuzzers:
  `libCRS list-fuzzers --fuzzer {fuzzer_module}`
  - Returns: array of fuzzer_id and pid for each running fuzzer

## Directories

- **Source code**: This directory (where CLAUDE.md is located)
- **Seed corpus**: `{corpus_dir}` — write your seeds here
- **Crashes**: `{crashes_dir}` — crash files appear here
- **Work directory**: `{work_dir}`

## Tips

- Start with simple seeds, let the fuzzer mutate them
- Look for magic bytes, headers, or format markers in the code
- Check existing seed corpuses if available: `/out/{harness}_seed_corpus/`
- The fuzzer uses libfuzzer by default with fork mode and crash tolerance
- Crashes are automatically submitted — no manual action needed

## Context

- Language: {language}
- Sanitizer: {sanitizer}
- Harness: {harness}
- Fuzz time: {fuzz_time} seconds
- Fuzzer module: {fuzzer_module}
