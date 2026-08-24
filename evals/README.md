# Adversarial evaluation suites

The three `suite.json` files turn the README evaluation categories into
deterministic attacks on the Alibaba historical replay golden case:

- `citation-accuracy`: immutable snapshot mismatch, out-of-range PDF page, and
  forged text that differs from the curator-verified quote anchor;
- `assumption-mapping`: evidence assigned to the wrong assumption and a missing
  assumption assessment;
- `future-leakage`: evidence after the baseline cutoff and a source after the
  replay analysis cutoff.

A suite passes only when the unchanged golden case passes, the suite declares
at least two mutations, and every mutation is rejected by every check named in
its `expected_failed_checks`. A crash, a no-op mutation, or failure only in an
unrelated check does not count as detection.

## Manifest format

Each manifest contains `schema_version`, `suite_id`, `golden_case`, and a
`mutations` array. Paths are relative to the manifest. A mutation selects one
artifact from the golden case (`base_thesis`, `documents`, `evidence`,
`thesis_diff`, `user_review`, or `accepted_thesis`), optionally selects one
object from a collection with an exact top-level `match`, and addresses the
field with a JSON Pointer. Supported operations are `set`, `delete`, and
`swap`. Every pointer must already exist so typos fail closed.

The runner never writes mutated fixtures. It loads a fresh in-memory copy for
each attack. Run all three README suites from the repository root:

```console
./.venv/bin/python -m thesisos eval-suite \
  evals/citation-accuracy/suite.json \
  evals/assumption-mapping/suite.json \
  evals/future-leakage/suite.json
```

Run the unchanged golden replay separately:

```console
./.venv/bin/python -m thesisos eval-replay \
  evals/historical-replay/alibaba-2024-q4/case.json
```

Both commands emit one deterministic JSON report. A failed replay or suite
returns process exit code 2 while preserving the complete report on stdout, so
CI can distinguish a detected regression from a malformed command or crash.
The same runner is available as a Python API:

```python
from thesisos.adversarial import evaluate_adversarial_suite

report = evaluate_adversarial_suite("evals/citation-accuracy/suite.json")
assert report.passed, report.to_dict()
```
