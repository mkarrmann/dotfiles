# fetch_test_details - Deep Test Debugging

Download full test artifacts (TSAN/ASAN logs, stack traces) when `detail_short` is insufficient:

```bash
# FAILED tests only (recommended)
scripts/fetch_test_details D12345 --status FAILED --limit 5

# Human-readable with previews
scripts/fetch_test_details D12345 --limit 3

# Brief listing without previews
scripts/fetch_test_details D12345 --brief

# JSON output for scripting
scripts/fetch_test_details D12345 --json 2>/dev/null | jq '.tests[].artifacts[] | select(.name == "stdout.log") | .path'
```

**When to use:** When `detail_short` shows "0 sub results", "sanitize_report", or cryptic errors.

**Searching downloaded artifacts:**
```bash
# TSAN failures
grep -A 50 "WARNING: ThreadSanitizer" /tmp/ci-debug-D12345-*/*/stdout.log

# ASAN failures
grep -A 50 "WARNING: AddressSanitizer" /tmp/ci-debug-D12345-*/*/stdout.log

# Python failures
grep -A 30 "AssertionError\|Traceback" /tmp/ci-debug-D12345-*/*/stdout.log

# Rust panics
grep -A 20 "thread .* panicked at" /tmp/ci-debug-D12345-*/*/stdout.log
```

## Understanding Test Artifacts

| Artifact | Contains | Check First? |
|----------|----------|--------------|
| **stdout.log** | Full output, TSAN/ASAN reports, stack traces | YES |
| **stderr.log** | Error output, warnings | Sometimes |
| **test_details.txt** | Summary only (often incomplete) | NO |
| **bootstrap.log** | Test setup logs | Rarely |

> **Important:** For sanitizer failures, `test_details.txt` typically shows only "sanitize_report" - the actual stack traces are in `stdout.log`.

Artifacts are saved to: `/tmp/ci-debug-D<number>-<timestamp>/`

## TestX CLI Reference

```bash
TESTX=~/fbsource/fbcode/tae/testx/scripts/testx

$TESTX --caller ci-signals runs list --diff D12345 -n 10          # List test runs
$TESTX --caller ci-signals results list <run_id>                   # List results
$TESTX --caller ci-signals artifacts get <diagnostic_id> --output-dir /tmp/output  # Download artifacts
```
