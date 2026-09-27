"""Generate the realrepo_v1 suite: five 3-prompt sessions on a real 2,000-file repo (sympy 1.14.0).

The repo itself isn't copied into this tree: tasks point at a pinned checkout (``repo_src``) and a
virtualenv with sympy's test dependencies (``python``). Set them up once:

    git clone --depth 1 --branch sympy-1.14.0 https://github.com/sympy/sympy.git D:/ArbiterBench/repos/sympy-1.14.0
    python -m venv D:/ArbiterBench/venvs/sympy
    D:/ArbiterBench/venvs/sympy/Scripts/python -m pip install pytest mpmath hypothesis
    python -m bench.make_realrepo_v1

Each session asks for three changes in sequence, like a user following up in one conversation; the
last prompt sometimes changes what an earlier one asked for. Hidden tests check the final state,
one test per requirement. Solutions are the upstream files with the reference change applied.
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import yaml

OUT = Path(__file__).resolve().parent / "tasks_realrepo_v1"
REPO = Path("D:/ArbiterBench/repos/sympy-1.14.0")
PYTHON = "D:/ArbiterBench/venvs/sympy/Scripts/python.exe"


def edit(rel: str, *reps: tuple[str, str]) -> tuple[str, str]:
    text = (REPO / rel).read_text("utf-8")
    for a, b in reps:
        assert text.count(a) >= 1, (rel, a)
        text = text.replace(a, b)
    return rel, text


def dedent(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


TASKS = []

# ------------------------------------------------------------------ 1. iterables: key -> keyfunc
TASKS.append({
    "id": "rr_iter_keyfunc", "category": "multi_turn",
    "prompts": [
        "Give `common_prefix` and `common_suffix` (sympy.utilities.iterables) a keyword-only `key` argument: two "
        "elements match when key(a) == key(b). The result is still a slice of the first sequence, and without "
        "`key` nothing changes.",
        "Do the same for `runs` in that module: with `key`, `op` is applied to key(element) values, but the runs "
        "still hold the original elements.",
        "Change of plan: call the argument `keyfunc` in all three functions, like `sift` does. Passing `key=` must "
        "still work for now, but emit a DeprecationWarning.",
    ],
    "requirements": {"prefix_keyfunc": "test_prefix_keyfunc", "suffix_keyfunc": "test_suffix_keyfunc",
                     "default_unchanged": "test_default_unchanged", "runs_keyfunc": "test_runs_keyfunc",
                     "key_alias_deprecated": "test_key_alias_deprecated"},
    "hidden": dedent('''
        import pytest
        from sympy.utilities.iterables import common_prefix, common_suffix, runs


        def test_prefix_keyfunc():
            assert common_prefix(['A', 'b', 'C'], ['a', 'B', 'd'], keyfunc=str.lower) == ['A', 'b']


        def test_suffix_keyfunc():
            assert common_suffix(['x', 'B', 'c'], ['y', 'b', 'C'], keyfunc=str.lower) == ['B', 'c']


        def test_default_unchanged():
            assert common_prefix([1, 2, 3], [1, 2, 5]) == [1, 2]
            assert common_suffix([1, 2, 3], [9, 2, 3]) == [2, 3]
            assert runs([0, 1, 2, 2, 1]) == [[0, 1, 2], [2], [1]]


        def test_runs_keyfunc():
            assert runs(['a', 'B', 'c', 'A'], keyfunc=str.lower) == [['a', 'B', 'c'], ['A']]


        def test_key_alias_deprecated():
            with pytest.warns(DeprecationWarning):
                assert common_prefix(['A'], ['a'], key=str.lower) == ['A']
            with pytest.warns(DeprecationWarning):
                assert common_suffix(['A'], ['a'], key=str.lower) == ['A']
            with pytest.warns(DeprecationWarning):
                assert runs(['a', 'B'], key=str.lower) == [['a', 'B']]
        '''),
    "solution": [edit(
        "sympy/utilities/iterables.py",
        ("def common_prefix(*seqs):", dedent('''
            def _keyfunc(keyfunc, key, name):
                if key is not None:
                    import warnings
                    warnings.warn("%s(key=...) is deprecated: use keyfunc=..." % name, DeprecationWarning,
                                  stacklevel=3)
                    if keyfunc is None:
                        keyfunc = key
                return keyfunc or (lambda x: x)


            def common_prefix(*seqs, keyfunc=None, key=None):''').rstrip()),
        ("def common_suffix(*seqs):", "def common_suffix(*seqs, keyfunc=None, key=None):"),
        ("    if not all(seqs):\n        return []",
         "    f = _keyfunc(keyfunc, key, 'common_prefix/suffix')\n    if not all(seqs):\n        return []"),
        ("        if not all(seqs[j][i] == seqs[0][i] for j in range(len(seqs))):",
         "        if not all(f(seqs[j][i]) == f(seqs[0][i]) for j in range(len(seqs))):"),
        ("def runs(seq, op=gt):", "def runs(seq, op=gt, *, keyfunc=None, key=None):"),
        ("    cycles = []\n    seq = iter(seq)",
         "    f = _keyfunc(keyfunc, key, 'runs')\n    cycles = []\n    seq = iter(seq)"),
        ("        if op(ei, run[-1]):", "        if op(f(ei), f(run[-1])):"),
    )],
})

# ------------------------------------------------------------------ 2. ntheory: digital roots
TASKS.append({
    "id": "rr_digital_root", "category": "multi_turn",
    "prompts": [
        "Add a `digital_root(n, b=10)` function to sympy's number theory package: repeatedly sum the base-b digits "
        "of n until a single digit is left (digital_root(0) == 0). It should be importable with "
        "`from sympy.ntheory import digital_root`.",
        "digital_root should keep the sign of n (digital_root(-38) == -2), and reject bases below 2 with the same "
        "ValueError that `digits` raises.",
        "Also add `multiplicative_digital_root(n, b=10)` beside it: repeatedly multiply the base-b digits until one "
        "digit is left, keeping the sign the same way, and export it the same way.",
    ],
    "requirements": {"dr_basic": "test_dr_basic", "dr_base": "test_dr_base", "dr_negative": "test_dr_negative",
                     "dr_bad_base": "test_dr_bad_base", "mdr": "test_mdr", "mdr_base": "test_mdr_base",
                     "exports": "test_exports"},
    "hidden": dedent('''
        import pytest
        from sympy.ntheory import digital_root, multiplicative_digital_root


        def test_dr_basic():
            assert [digital_root(n) for n in (38, 0, 9875, 7)] == [2, 0, 2, 7]


        def test_dr_base():
            assert digital_root(255, 16) == 15 and digital_root(0b1011, 2) == 1


        def test_dr_negative():
            assert digital_root(-38) == -2


        def test_dr_bad_base():
            with pytest.raises(ValueError, match="b must be greater than 1"):
                digital_root(5, 1)


        def test_mdr():
            assert [multiplicative_digital_root(n) for n in (39, 0, 10, 77, -39)] == [4, 0, 0, 8, -4]


        def test_mdr_base():
            assert multiplicative_digital_root(0o77, 8) == 6


        def test_exports():
            import sympy.ntheory as nt
            assert "digital_root" in nt.__all__ and "multiplicative_digital_root" in nt.__all__
        '''),
    "solution": [
        edit("sympy/ntheory/digits.py", ("def is_palindromic(n, b=10):", dedent('''
            def digital_root(n, b=10):
                """Repeatedly sum the base-``b`` digits of ``n`` until one digit is left; the sign of
                ``n`` is kept."""
                n, b = as_int(n), as_int(b)
                if b < 2:
                    raise ValueError("b must be greater than 1")
                x = abs(n)
                while x >= b:
                    x = sum(digits(x, b)[1:])
                return -x if n < 0 else x


            def multiplicative_digital_root(n, b=10):
                """Repeatedly multiply the base-``b`` digits of ``n`` until one digit is left; the sign
                of ``n`` is kept."""
                n, b = as_int(n), as_int(b)
                if b < 2:
                    raise ValueError("b must be greater than 1")
                x = abs(n)
                while x >= b:
                    p = 1
                    for d in digits(x, b)[1:]:
                        p *= d
                    x = p
                return -x if n < 0 else x


            def is_palindromic(n, b=10):''').rstrip())),
        edit("sympy/ntheory/__init__.py",
             ("from .digits import count_digits, digits, is_palindromic",
              "from .digits import count_digits, digits, is_palindromic, digital_root, multiplicative_digital_root"),
             ("    'is_palindromic',",
              "    'is_palindromic',\n    'digital_root',\n    'multiplicative_digital_root',")),
    ],
})

# ------------------------------------------------------------------ 3. Permutation: involution, fixed points
TASKS.append({
    "id": "rr_perm_fixed", "category": "multi_turn",
    "prompts": [
        "Add an `is_involution` property to SymPy's Permutation: True when applying the permutation twice gives "
        "the identity (the identity itself counts).",
        "Also add a `fixed_points()` method that returns a sorted list of the points the permutation leaves in "
        "place.",
        "Change of plan for fixed points: make `fixed_points` a property that returns a tuple instead of a method, "
        "like `cycle_structure` is a property. Nothing else in sympy should break.",
    ],
    "requirements": {"involution": "test_involution", "fixed_points_values": "test_fixed_points_values",
                     "fixed_points_tuple_property": "test_fixed_points_tuple_property"},
    "hidden": dedent('''
        from sympy.combinatorics import Permutation


        def test_involution():
            assert Permutation([1, 0, 2]).is_involution is True
            assert Permutation([1, 2, 0]).is_involution is False
            assert Permutation(3).is_involution is True
            assert Permutation([[0, 1], [2, 3]]).is_involution is True


        def test_fixed_points_values():
            assert Permutation([0, 2, 1, 3]).fixed_points == (0, 3)
            assert Permutation([1, 2, 0]).fixed_points == ()
            assert Permutation(2).fixed_points == (0, 1, 2)


        def test_fixed_points_tuple_property():
            fp = Permutation([0, 2, 1]).fixed_points
            assert isinstance(fp, tuple) and not callable(fp)
        '''),
    "solution": [edit("sympy/combinatorics/permutations.py",
                      ("    @property\n    def cycle_structure(self):", dedent('''
            @property
            def is_involution(self):
                """True if applying the permutation twice gives the identity."""
                a = self.array_form
                return all(a[a[i]] == i for i in range(len(a)))

            @property
            def fixed_points(self):
                """The points the permutation leaves in place, in order, as a tuple."""
                return tuple(i for i, j in enumerate(self.array_form) if i == j)

            @property
            def cycle_structure(self):''').rstrip().replace("\n", "\n    ").replace("    \n", "\n")
        .join(["    ", ""])))],
})

# ------------------------------------------------------------------ 4. IntegerPartition
TASKS.append({
    "id": "rr_int_partition", "category": "multi_turn",
    "prompts": [
        "Make `len()` work on SymPy's IntegerPartition: it should give the number of parts.",
        "Add an `is_self_conjugate` property to IntegerPartition: True when the partition equals its conjugate.",
        "Extend `IntegerPartition.as_ferrers`: add a `sep` argument (default a newline) placed between the rows, "
        "and raise ValueError if `char` is an empty string.",
    ],
    "requirements": {"len_parts": "test_len_parts", "self_conjugate": "test_self_conjugate",
                     "ferrers_sep": "test_ferrers_sep", "ferrers_default": "test_ferrers_default",
                     "ferrers_empty_char": "test_ferrers_empty_char"},
    "hidden": dedent('''
        import pytest
        from sympy.combinatorics.partitions import IntegerPartition as IP


        def test_len_parts():
            assert len(IP([3, 1, 1])) == 3 and len(IP([5])) == 1


        def test_self_conjugate():
            assert IP([3, 2, 1]).is_self_conjugate and IP([2, 2]).is_self_conjugate and IP([1]).is_self_conjugate
            assert not IP([3, 1]).is_self_conjugate


        def test_ferrers_sep():
            assert IP([3, 1]).as_ferrers(sep="|") == "###|#"
            assert IP([2, 1]).as_ferrers("*", sep=" ") == "** *"


        def test_ferrers_default():
            assert IP([3, 1]).as_ferrers() == "###\\n#"


        def test_ferrers_empty_char():
            with pytest.raises(ValueError):
                IP([3, 1]).as_ferrers("")
        '''),
    "solution": [edit(
        "sympy/combinatorics/partitions.py",
        ("    def as_ferrers(self, char='#'):", "    def as_ferrers(self, char='#', sep='\\n'):"),
        ('        return "\\n".join([char*i for i in self.partition])', dedent('''
                if not char:
                    raise ValueError("char must be a non-empty string")
                return sep.join([char*i for i in self.partition])

            def __len__(self):
                return len(self.partition)

            @property
            def is_self_conjugate(self):
                """True if the partition equals its conjugate."""
                return list(self.partition) == self.conjugate''').rstrip().replace("\n", "\n    ")
         .join(["    ", ""])),
    )],
})

# ------------------------------------------------------------------ 5. ordinal words
WORDS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth", "eleventh",
         "twelfth", "thirteenth", "fourteenth", "fifteenth", "sixteenth", "seventeenth", "eighteenth", "nineteenth",
         "twentieth"]
TASKS.append({
    "id": "rr_ordinal_words", "category": "multi_turn",
    "prompts": [
        "Add a `words=False` option to SymPy's `ordinal` helper: with words=True, 1 through 12 are spelled out "
        "('first', 'second', ..., 'twelfth'); other numbers keep the numeric form ('13th').",
        "With words=True, negative numbers get 'minus ' in front ('minus third'), and zero is 'zeroth'.",
        "Put the word table in a module-level dict called ORDINAL_WORDS (int -> word) so other code can reuse it, "
        "and extend it through 20 ('twentieth').",
    ],
    "requirements": {"words_1_12": "test_words_1_12", "words_13_20": "test_words_13_20",
                     "numeric_default": "test_numeric_default", "negative": "test_negative", "zero": "test_zero",
                     "table": "test_table"},
    "hidden": dedent('''
        from sympy.utilities import misc
        from sympy.utilities.misc import ordinal


        def test_words_1_12():
            assert [ordinal(i, words=True) for i in (1, 2, 3, 5, 8, 9, 12)] == [
                "first", "second", "third", "fifth", "eighth", "ninth", "twelfth"]


        def test_words_13_20():
            assert ordinal(13, words=True) == "thirteenth" and ordinal(20, words=True) == "twentieth"
            assert ordinal(21, words=True) == "21st"


        def test_numeric_default():
            assert (ordinal(1), ordinal(12), ordinal(0), ordinal(-3)) == ("1st", "12th", "0th", "-3rd")


        def test_negative():
            assert ordinal(-3, words=True) == "minus third" and ordinal(-25, words=True) == "-25th"


        def test_zero():
            assert ordinal(0, words=True) == "zeroth"


        def test_table():
            assert misc.ORDINAL_WORDS[1] == "first" and misc.ORDINAL_WORDS[20] == "twentieth"
            assert len(misc.ORDINAL_WORDS) >= 20
        '''),
    "solution": [edit(
        "sympy/utilities/misc.py",
        ("def ordinal(num):\n", "ORDINAL_WORDS = {" + ", ".join(f"{i + 1}: {w!r}" for i, w in enumerate(WORDS))
         + "}\n\n\ndef ordinal(num, words=False):\n"),
        ("    n = as_int(num)\n    k = abs(n) % 100", dedent('''
                n = as_int(num)
                if words:
                    if n == 0:
                        return 'zeroth'
                    if abs(n) in ORDINAL_WORDS:
                        return ('minus ' if n < 0 else '') + ORDINAL_WORDS[abs(n)]
                k = abs(n) % 100''').rstrip().replace("\n", "\n    ").join(["    ", ""])),
    )],
})


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    for t in TASKS:
        d = OUT / t["id"]
        (d / "hidden").mkdir(parents=True)
        (d / "hidden" / "test_hidden.py").write_text(t["hidden"], encoding="utf-8", newline="\n")
        for rel, text in t["solution"]:
            p = d / "solution" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8", newline="\n")
        meta = {"id": t["id"], "category": t["category"], "prompts": t["prompts"],
                "requirements": t["requirements"], "repo_src": REPO.as_posix(), "python": PYTHON,
                "integrity": "modified", "pre_index": True,
                "notes": "sympy 1.14.0 (2,033 files); hidden tests check the state after all prompts"}
        (d / "task.yaml").write_text(yaml.safe_dump(meta, sort_keys=False, width=110), encoding="utf-8")
    print(f"wrote {len(TASKS)} tasks to {OUT}")


if __name__ == "__main__":
    main()
