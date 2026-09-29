"""Generate the hard_v1 suite: tasks gpt-6-luna may fail, to test luna-first routing with escalation.

Seven debugging tasks and one algorithmic feature on the real repos (sympy 1.14.0, date-fns 4.1.0).
Each debugging task plants one small bug in library code (``setup/``, part of the workspace's first
commit) and describes only the symptom. For three of them the repo's own tests don't catch the bug
(checked when the suite was written); for the others a repo test fails somewhere near it. Hidden tests
compare against brute force or known values. Solutions are the original upstream files.

    python -m bench.make_hard_v1
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import yaml

OUT = Path(__file__).resolve().parent / "tasks_hard_v1"
SYMPY = Path("D:/ArbiterBench/repos/sympy-1.14.0")
DATEFNS = Path("D:/ArbiterBench/repos/date-fns-4.1.0")
SYMPY_PY = "D:/ArbiterBench/venvs/sympy/Scripts/python.exe"


def dedent(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


def plant(repo: Path, rel: str, old: str, new: str) -> tuple[str, str, str]:
    """(path, buggy text, original text)."""
    text = (repo / rel).read_text("utf-8")
    assert text.count(old) == 1, (rel, old)
    return rel, text.replace(old, new), text


def distinct_partitions_ref(n_max: int) -> list[int]:
    q = [1] + [0] * n_max
    for k in range(1, n_max + 1):
        for n in range(n_max, k - 1, -1):
            q[n] += q[n - k]
    return q


Q = distinct_partitions_ref(400)

TASKS = [
    {"id": "hd_prevprime", "repo": SYMPY, "caught_by_repo_tests": False,
     "prompts": ["`prevprime(n)` (sympy.ntheory) sometimes skips a prime: for some n above a million it returns a "
                 "prime smaller than the largest prime below n. Find the cause, fix it, and add a regression test."],
     "bug": plant(SYMPY, "sympy/ntheory/generate.py",
                  "        n = nn - 1\n        if isprime(n):\n            return n\n        n -= 4\n    else:\n"
                  "        n = nn + 1",
                  "        n = nn - 1\n        if isprime(n):\n            return n\n        n -= 6\n    else:\n"
                  "        n = nn + 1"),
     "requirements": {"prevprime_correct": "test_prevprime_correct"},
     "hidden": dedent('''
        from sympy import isprime, prevprime


        def test_prevprime_correct():
            for n in list(range(10**6, 10**6 + 3000)) + [10**9 + 9, 10**12 + 40]:
                q = n - 1
                while not isprime(q):
                    q -= 1
                assert prevprime(n) == q, n
     ''')},
    {"id": "hd_least_rotation", "repo": SYMPY, "caught_by_repo_tests": True,
     "prompts": ["`least_rotation` in sympy.utilities.iterables sometimes returns an index whose rotation is not the "
                 "lexicographically smallest one (random short lists of 0, 1 and 2 show it). Fix it."],
     "bug": plant(SYMPY, "sympy/utilities/iterables.py",
                  "            if key(sj) < key(S[k+i+1]):\n                k = j-i-1",
                  "            if key(sj) < key(S[k+i+1]):\n                k = j-i"),
     "requirements": {"least_rotation_minimal": "test_least_rotation_minimal"},
     "hidden": dedent('''
        import random

        from sympy.utilities.iterables import least_rotation


        def test_least_rotation_minimal():
            rng = random.Random(1)
            for _ in range(3000):
                x = [rng.randint(0, 2) for _ in range(rng.randint(1, 9))]
                best = min(x[i:] + x[:i] for i in range(len(x)))
                k = least_rotation(x)
                assert x[k:] + x[:k] == best, x
     ''')},
    {"id": "hd_sqrt_mod", "repo": SYMPY, "caught_by_repo_tests": True,
     "prompts": ["`sqrt_mod(a, p)` (sympy.ntheory) returns wrong square roots for some primes p that are 1 mod 8, "
                 "p = 73 for example. Fix it."],
     "bug": plant(SYMPY, "sympy/ntheory/residue_ntheory.py",
                  "    if p % 12 == 5:\n        # Legendre symbol (3/p) == -1 if p % 12 in [5, 7]\n        d = 3",
                  "    if p % 12 == 1:\n        # Legendre symbol (3/p) == -1 if p % 12 in [5, 7]\n        d = 3"),
     "requirements": {"sqrt_mod_roots": "test_sqrt_mod_roots"},
     "hidden": dedent('''
        from sympy.ntheory import isprime, sqrt_mod


        def test_sqrt_mod_roots():
            for p in [q for q in range(3, 3000) if isprime(q) and q % 8 == 1]:
                for a in (2, 3, 5, 7, 10, 11):
                    r = sqrt_mod(a, p)
                    assert r is None or r * r % p == a % p, (a, p, r)
            assert sqrt_mod(2, 73) is not None
     ''')},
    {"id": "hd_divisor_count", "repo": SYMPY, "caught_by_repo_tests": True,
     "prompts": ["`divisor_count(n, modulus)` (sympy.ntheory) gives wrong counts for some n and moduli: it should "
                 "count the divisors of n that are divisible by modulus. Fix it."],
     "bug": plant(SYMPY, "sympy/ntheory/factor_.py",
                  "        n, r = divmod(n, modulus)\n        if r:\n            return 0",
                  "        n, r = divmod(n, modulus)\n        if r > 1:\n            return 0"),
     "requirements": {"divisor_count_modulus": "test_divisor_count_modulus"},
     "hidden": dedent('''
        from sympy import divisor_count, divisors


        def test_divisor_count_modulus():
            for n in range(1, 300):
                for m in (2, 3, 5, 7):
                    assert divisor_count(n, m) == sum(1 for d in divisors(n) if d % m == 0), (n, m)
     ''')},
    {"id": "hd_binary_partitions", "repo": SYMPY, "caught_by_repo_tests": True,
     "prompts": ["`binary_partitions(n)` in sympy.utilities.iterables yields wrong partitions for some n (they don't "
                 "all sum to n, or some are missing). Fix it."],
     "bug": plant(SYMPY, "sympy/utilities/iterables.py",
                  "    last_num = len(partition) - 1 - (n & 1)",
                  "    last_num = len(partition) - 1 - (n & 2 and 1)"),
     "requirements": {"binary_partitions_all": "test_binary_partitions_all"},
     "hidden": dedent('''
        from sympy.utilities.iterables import binary_partitions


        def count(n, largest):
            if n == 0:
                return 1
            return sum(count(n - p, p) for p in (1 << k for k in range(n.bit_length() + 1)) if p <= min(n, largest))


        def test_binary_partitions_all():
            for n in range(1, 40):
                parts = [tuple(p) for p in binary_partitions(n)]
                assert all(sum(p) == n for p in parts), n
                assert len(set(parts)) == count(n, n), n
     ''')},
    {"id": "hd_week_of_month", "repo": DATEFNS, "caught_by_repo_tests": True,
     "prompts": ["`getWeekOfMonth` returns 2 for the first day of some months when `weekStartsOn` is set (it should "
                 "always be week 1 for the 1st). Fix it."],
     "bug": plant(DATEFNS, "src/getWeekOfMonth/index.ts",
                  "if (lastDayOfFirstWeek <= 0) lastDayOfFirstWeek += 7;",
                  "if (lastDayOfFirstWeek < 0) lastDayOfFirstWeek += 7;"),
     "requirements": {"first_is_week_one": "first_is_week_one"},
     "hidden": dedent('''
        import { expect, it } from "vitest";
        import { getWeekOfMonth } from "../index.js";

        it("first_is_week_one", () => {
          for (let m = 0; m < 24; m++)
            for (const ws of [0, 1, 6] as const)
              expect(getWeekOfMonth(new Date(2024, m, 1), { weekStartsOn: ws })).toBe(1);
          expect(getWeekOfMonth(new Date(2024, 8, 8), { weekStartsOn: 0 })).toBe(2);
        });
     ''')},
    {"id": "hd_interval_duration", "repo": DATEFNS, "caught_by_repo_tests": True,
     "prompts": ["`intervalToDuration` gets the days wrong for intervals that span more than a year and some months "
                 "(Jan 10 2020 to Apr 15 2023 should be 3 years, 3 months, 5 days). Fix it."],
     "bug": plant(DATEFNS, "src/intervalToDuration/index.ts",
                  "const remainingDays = add(remainingMonths, { months: duration.months });",
                  "const remainingDays = add(start, { months: duration.months });"),
     "requirements": {"years_months_days": "years_months_days"},
     "hidden": dedent('''
        import { expect, it } from "vitest";
        import { intervalToDuration } from "../index.js";

        it("years_months_days", () => {
          expect(intervalToDuration({ start: new Date(2020, 0, 10), end: new Date(2023, 3, 15) }))
            .toEqual({ years: 3, months: 3, days: 5 });
          expect(intervalToDuration({ start: new Date(2021, 5, 1, 8), end: new Date(2022, 7, 20, 10, 30) }))
            .toEqual({ years: 1, months: 2, days: 19, hours: 2, minutes: 30 });
        });
     ''')},
    {"id": "hf_distinct_partitions", "repo": SYMPY, "caught_by_repo_tests": None,
     "prompts": ["Add `distinct_partitions(n)` to sympy.ntheory (importable as `from sympy.ntheory import "
                 "distinct_partitions`): the number of partitions of n into distinct parts (OEIS A000009; "
                 "distinct_partitions(0) == 1, distinct_partitions(7) == 5). It has to be fast: n = 20000 must take "
                 "well under a second."],
     "bug": None,
     "requirements": {"small_values": "test_small_values", "fast_large": "test_fast_large"},
     "hidden": dedent(f'''
        import time

        from sympy.ntheory import distinct_partitions

        Q = {Q!r}


        def test_small_values():
            assert [distinct_partitions(n) for n in range(len(Q))] == Q


        def test_fast_large():
            t = time.perf_counter()
            v = distinct_partitions(20000)
            assert time.perf_counter() - t < 2.0
            assert int(v) > 10**100 and int(v) % 10**6 == int(distinct_partitions(20000)) % 10**6

     ''')},
]


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    for t in TASKS:
        d = OUT / t["id"]
        sympy = t["repo"] == SYMPY
        (d / "hidden").mkdir(parents=True)
        (d / "hidden" / ("test_hidden.py" if sympy else "test.ts")).write_text(t["hidden"], encoding="utf-8",
                                                                               newline="\n")
        if t["bug"]:
            rel, buggy, original = t["bug"]
            for sub, text in (("setup", buggy), ("solution", original)):
                p = d / sub / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text, encoding="utf-8", newline="\n")
        meta = {"id": t["id"], "category": "debug" if t["bug"] else "feature", "prompts": t["prompts"],
                "requirements": t["requirements"], "repo_src": t["repo"].as_posix()}
        if sympy:
            meta["python"] = SYMPY_PY
        else:
            meta["link"] = {"node_modules": "D:/ArbiterBench/deps/date-fns/node_modules"}
            meta["hidden_runner"] = "vitest"
        meta.update({"integrity": "modified", "pre_index": True,
                     "notes": f"hard_v1; planted bug caught by the repo's own tests: {t['caught_by_repo_tests']}"})
        (d / "task.yaml").write_text(yaml.safe_dump(meta, sort_keys=False, width=110), encoding="utf-8")
    print(f"wrote {len(TASKS)} tasks to {OUT}")


if __name__ == "__main__":
    main()
