"""Every function and method in power_monitor has full type hints (Req 13.8).

Each host/power_monitor/*.py file is parsed with `ast` and every `def` /
`async def` is checked: all parameters must be annotated (except a first
`self`/`cls`), and the return type must be annotated. Failures name the file,
line, and function.

Why `ast` instead of importing the modules and using `inspect`: importing runs
module-level code. That would pull in matplotlib or pyserial as a side effect,
and importing `__main__.py` would run `raise SystemExit(main())`, which starts
the tool. `ast.parse` only reads the source text, so nothing is executed.
"""

import ast
from pathlib import Path

# Located from this file, not the current directory, so the test checks the
# same package whether pytest runs from host/ or the repo root.
# parents[0] is host/tests, parents[1] is host.
PACKAGE_DIR = Path(__file__).resolve().parents[1] / "power_monitor"

# Modules that must be found. Why an empty or partial scan must fail: if the
# directory moved or the glob broke, the loop below would check zero files,
# find zero violations, and pass while checking nothing. Naming the expected
# modules (instead of a file count) also says which one went missing.
EXPECTED_MODULES = {
    "protocol",
    "parser",
    "simulator",
    "link_monitor",
    "csv_logger",
    "cli",
    "live_plot",
}

# A first positional parameter with one of these names is exempt: Python
# fills it in (the instance or the class), so annotating it adds nothing.
IMPLICIT_FIRST_PARAMS = {"self", "cls"}

FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


def find_missing_annotations(tree: ast.AST, filename: str) -> list[str]:
    """Return one message per missing annotation in `tree`.

    `ast.walk` visits every node, so nested functions, methods, and Protocol
    methods are all covered. Lambdas are `ast.Lambda` nodes, not FunctionDef,
    so they are skipped (Python has no syntax to annotate them).
    """
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            violations.extend(_check_function(node, filename))
    return violations


def count_functions(tree: ast.AST) -> int:
    """Count the functions the checker visits, so a checker that visits
    nothing can't pass silently."""
    return sum(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(tree)
    )


def _check_function(node: FunctionNode, filename: str) -> list[str]:
    prefix = f"{filename}:{node.lineno} {node.name}"
    args = node.args
    positional = args.posonlyargs + args.args

    # (display name, arg node) for every parameter kind Python has.
    params: list[tuple[str, ast.arg]] = []
    for index, arg in enumerate(positional):
        # Only the FIRST positional parameter may be an unannotated self/cls;
        # a `self` anywhere else is just an ordinary parameter name.
        if index == 0 and arg.arg in IMPLICIT_FIRST_PARAMS:
            continue
        params.append((arg.arg, arg))
    if args.vararg is not None:
        params.append((f"*{args.vararg.arg}", args.vararg))
    params.extend((arg.arg, arg) for arg in args.kwonlyargs)
    if args.kwarg is not None:
        params.append((f"**{args.kwarg.arg}", args.kwarg))

    violations = [
        f"{prefix}: parameter '{name}' has no annotation"
        for name, arg in params
        if arg.annotation is None
    ]
    if node.returns is None:
        violations.append(f"{prefix}: no return annotation")
    return violations


def _check_source(source: str) -> list[str]:
    return find_missing_annotations(ast.parse(source), "example.py")


# --- The real package -------------------------------------------------------


def test_all_power_monitor_functions_are_annotated() -> None:
    files = sorted(PACKAGE_DIR.glob("*.py"))
    found = {path.stem for path in files}
    missing_modules = EXPECTED_MODULES - found
    assert not missing_modules, (
        f"modules not found in {PACKAGE_DIR}: {sorted(missing_modules)}"
    )

    violations: list[str] = []
    functions_checked = 0
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        functions_checked += count_functions(tree)
        violations.extend(find_missing_annotations(tree, path.name))

    assert functions_checked > 0, "no functions were checked"
    # All violations are listed, one per line, so one run shows everything
    # that needs fixing rather than just the first.
    assert not violations, "missing type hints:\n" + "\n".join(violations)


# --- The checker on small examples -----------------------------------------


def test_flags_missing_parameter_annotation() -> None:
    assert _check_source("def f(x) -> int:\n    return x\n") == [
        "example.py:1 f: parameter 'x' has no annotation"
    ]


def test_flags_missing_return_annotation() -> None:
    assert _check_source("def f(x: int):\n    return x\n") == [
        "example.py:1 f: no return annotation"
    ]


def test_flags_unannotated_varargs_and_kwargs() -> None:
    assert _check_source("def f(*args, **kwargs) -> None:\n    pass\n") == [
        "example.py:1 f: parameter '*args' has no annotation",
        "example.py:1 f: parameter '**kwargs' has no annotation",
    ]


def test_flags_positional_only_and_keyword_only() -> None:
    assert _check_source("def f(a, /, *, b) -> None:\n    pass\n") == [
        "example.py:1 f: parameter 'a' has no annotation",
        "example.py:1 f: parameter 'b' has no annotation",
    ]


def test_accepts_self_and_cls_as_first_parameter() -> None:
    source = (
        "class C:\n"
        "    def method(self, x: int) -> int:\n"
        "        return x\n"
        "    @classmethod\n"
        "    def make(cls) -> 'C':\n"
        "        return cls()\n"
    )
    assert _check_source(source) == []


def test_does_not_exempt_self_that_is_not_first() -> None:
    assert _check_source("def f(x: int, self) -> None:\n    pass\n") == [
        "example.py:1 f: parameter 'self' has no annotation"
    ]


def test_flags_nested_function() -> None:
    source = (
        "def outer() -> None:\n"
        "    def inner(y):\n"
        "        return y\n"
    )
    assert _check_source(source) == [
        "example.py:2 inner: parameter 'y' has no annotation",
        "example.py:2 inner: no return annotation",
    ]


def test_ignores_lambdas() -> None:
    assert _check_source("def f() -> None:\n    g = lambda x: x\n") == []


def test_reports_every_violation_in_one_function() -> None:
    assert _check_source("def f(a, b):\n    pass\n") == [
        "example.py:1 f: parameter 'a' has no annotation",
        "example.py:1 f: parameter 'b' has no annotation",
        "example.py:1 f: no return annotation",
    ]


def test_counts_functions() -> None:
    tree = ast.parse("def a() -> None:\n    def b() -> None:\n        pass\n")
    assert count_functions(tree) == 2
