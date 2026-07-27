"""Quick smoke test """
import sys, os
sys.stdout.reconfigure(encoding="utf-8")
from evaluate import FLOW_CASES, run_flow_case

cases = FLOW_CASES
print(f"Running {len(cases)} flow cases...\n")
results = []
for c in cases:
    r = run_flow_case(c)
    results.append(r)
    mark = "PASS" if r.passed else "FAIL"
    detail = f"  -> {r.detail}" if not r.passed else ""
    print(f"  [{mark}] {c.name}{detail}")

passed = sum(1 for r in results if r.passed)
print(f"\n{passed}/{len(results)} flow cases passed")
if passed < len(results):
    sys.exit(1)
