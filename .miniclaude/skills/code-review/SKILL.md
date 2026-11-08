---
name: code-review
description: Review a file in the workspace for bugs, edge cases and clarity. Use when asked to review, check or critique code.
---

# Code review

Work in this order. Do not skip step 1.

1. `read_file` the target. If it imports local modules, read those too.
2. Run it with `run_bash` if it is safe to run, and note what actually happens.
3. Check, in this order of importance:
   - **Correctness**: off-by-one, wrong comparison, unhandled empty input, integer division.
   - **Crashes**: index out of range, None/key errors, division by zero, unclosed files.
   - **Clarity**: names that lie, dead code, a function doing two jobs.
4. Report findings as a numbered list. For each one give: the line number, what
   breaks, and a concrete input that triggers it. No vague advice.
5. Do not rewrite the file unless asked. Review means review.
