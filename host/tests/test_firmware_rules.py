"""Static source rules for the firmware (Req 1.5, 8.3, 15.1, 16.3, 16.4).

Scans every .c, .cpp, and .h file under src/ and lib/pm_logic/src/ and checks:

- no float or double (Req 16.4: integer math only);
- no malloc, calloc, realloc, free(, new, or delete (Req 16.3: static allocation only);
- no Arduino headers (Arduino.h, Wire.h, HardwareSerial.h) in lib/pm_logic (Req 15.1);
- <Wire.h> included only in src/ina219_driver.cpp (Req 1.5);
- Serial used only in src/uart_driver.cpp (Req 8.3);
- no print or println anywhere (Req 8.3: only Frames go on the UART).

Each rule is a function of (path, text), so the tests below can plant a
violation in a small synthetic source and show the rule fires, without
touching the repo. The real-repo tests apply the same functions to the real
files.

The native build would also fail if a logic file included an Arduino header,
because those headers don't exist on the Mac. So the Arduino-header rule is
partly a duplicate. It earns its place as a readable error: it names the rule
and the line ("lib/pm_logic must not include Arduino headers") instead of a
bare "Wire.h: file not found" from the compiler.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

# Located from this file, not the current directory, so the scan finds the
# same files whether pytest is run from host/ or from the repo root.
# parents[0] is host/tests, parents[1] is host, parents[2] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]

SCAN_DIRS = ("src", "lib/pm_logic/src")
SOURCE_SUFFIXES = (".c", ".cpp", ".h")
LOGIC_DIR_PREFIX = "lib/pm_logic/"

WIRE_ALLOWED_FILE = "src/ina219_driver.cpp"
SERIAL_ALLOWED_FILE = "src/uart_driver.cpp"
ARDUINO_HEADERS = frozenset({"Arduino.h", "Wire.h", "HardwareSerial.h"})

# The files the scan must find, written out by name rather than globbed, so a
# missing or renamed file fails the coverage test instead of quietly shrinking
# the list it is compared against.
REQUIRED_FILES = frozenset({
    "src/main.cpp",
    "src/uart_driver.cpp",
    "src/ina219_driver.cpp",
    "lib/pm_logic/src/pm_crc8.c",
    "lib/pm_logic/src/pm_energy.c",
    "lib/pm_logic/src/pm_frame.c",
    "lib/pm_logic/src/pm_power.c",
    "lib/pm_logic/src/pm_sampler.c",
    "lib/pm_logic/src/pm_scaling.c",
    "lib/pm_logic/src/pm_time.c",
})


# --- Comment and literal stripping ---------------------------------------------
#
# Why strip first: the rules are about code, not prose. This project's comments
# explain the rules themselves ("no float anywhere", "Only ina219_driver.cpp
# includes <Wire.h>", "Serial.write() accepted fewer bytes"), and a string
# literal like "float" is data, not a type. Without stripping, the files that
# document the rules best would fail them.


def strip_comments(text: str, *, keep_literals: bool = False) -> str:
    """Return `text` with C/C++ comments removed (and literals, unless kept).

    - `//` comments are removed up to the end of the line; the newline stays.
    - `/* */` comments become one space (so `a/**/b` doesn't fuse into `ab`),
      plus every newline they contained, so line numbers stay correct.
    - String and character literals become `""` and `''` unless
      keep_literals is True. Escapes are honored, so `"a\\"b"` and `'"'` are
      each one literal, and `//` or `/*` inside a literal is not a comment.

    A small state machine rather than a regex: the four contexts (code,
    comment, string, character) decide what each other's delimiters mean, and
    one pass that tracks the current context gets every case right.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            end = text.find("\n", i)
            i = n if end == -1 else end   # the newline itself is kept as code
        elif c == "/" and nxt == "*":
            end = text.find("*/", i + 2)
            body = text[i + 2 : n if end == -1 else end]
            out.append(" " + "\n" * body.count("\n"))
            i = n if end == -1 else end + 2
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1   # skip the escaped character
            j = min(j + 1, n)                       # include the closing quote
            literal = text[i:j]
            if keep_literals:
                out.append(literal)
            else:
                out.append(c + c + "\n" * literal.count("\n"))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


# --- Violations and rules -------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    matched: str
    rule: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: found {self.matched!r}"


Rule = Callable[[str, str], list[Violation]]


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _find_pattern(path: str, code: str, pattern: re.Pattern[str], rule: str) -> list[Violation]:
    return [
        Violation(path, _line_of(code, m.start()), m.group(0), rule)
        for m in pattern.finditer(code)
    ]


# Why word boundaries (\b): a plain substring search would match `new` inside
# `renew` or `new_value`, `free` inside `freeze`, `double` inside `doubled`,
# `Serial` inside `HardwareSerial`, and `print` inside `blueprint` or
# `printf`. \b only matches where a word character meets a non-word
# character, so each pattern matches the whole identifier and nothing else.
# `_` counts as a word character, so `new_value` doesn't match either.
_FLOAT_DOUBLE = re.compile(r"\b(?:float|double)\b")
_ALLOCATION = re.compile(r"\b(?:malloc|calloc|realloc|new|delete)\b|\bfree\s*\(")
_SERIAL = re.compile(r"\bSerial\b")
_PRINT = re.compile(r"\bprint(?:ln)?\b")   # not println?, which means "printl" + optional "n"

# #include with either delimiter. Group 1 is the header path.
_INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*[<"]([^>"\n]+)[>"]', re.MULTILINE)


def find_float_double(path: str, text: str) -> list[Violation]:
    return _find_pattern(path, strip_comments(text), _FLOAT_DOUBLE, "no float or double")


def find_dynamic_allocation(path: str, text: str) -> list[Violation]:
    return _find_pattern(path, strip_comments(text), _ALLOCATION, "no dynamic allocation")


def find_serial_outside_uart_driver(path: str, text: str) -> list[Violation]:
    if path == SERIAL_ALLOWED_FILE:
        return []
    return _find_pattern(
        path, strip_comments(text), _SERIAL, f"Serial only in {SERIAL_ALLOWED_FILE}"
    )


def find_print(path: str, text: str) -> list[Violation]:
    return _find_pattern(path, strip_comments(text), _PRINT, "no print or println")


def includes(text: str) -> list[tuple[int, str]]:
    """(line, header path) for every #include, both <...> and "..." forms.

    Why this keeps string literals: stripping literals would also erase the
    path in `#include "Wire.h"` (it is a quoted string to the stripper), so a
    quoted include of an Arduino header would slip past. Comments are still
    removed, so a comment that mentions `#include <Wire.h>` doesn't count.
    """
    code = strip_comments(text, keep_literals=True)
    return [(_line_of(code, m.start()), m.group(1).strip()) for m in _INCLUDE.finditer(code)]


def _header_name(include_path: str) -> str:
    # "utility/Wire.h" and "Wire.h" are the same header for these rules.
    return include_path.replace("\\", "/").rsplit("/", 1)[-1]


def find_arduino_headers_in_logic(path: str, text: str) -> list[Violation]:
    if not path.startswith(LOGIC_DIR_PREFIX):
        return []
    return [
        Violation(path, line, header, "lib/pm_logic must not include Arduino headers")
        for line, header in includes(text)
        if _header_name(header) in ARDUINO_HEADERS
    ]


def find_wire_include_outside_driver(path: str, text: str) -> list[Violation]:
    if path == WIRE_ALLOWED_FILE:
        return []
    return [
        Violation(path, line, header, f"Wire.h only in {WIRE_ALLOWED_FILE}")
        for line, header in includes(text)
        if _header_name(header) == "Wire.h"
    ]


ALL_RULES: list[Rule] = [
    find_float_double,
    find_dynamic_allocation,
    find_arduino_headers_in_logic,
    find_wire_include_outside_driver,
    find_serial_outside_uart_driver,
    find_print,
]


# --- Scanning the repo ------------------------------------------------------------


def scan_sources() -> dict[str, str]:
    """Repo-relative POSIX path -> file text, for every firmware source file."""
    sources: dict[str, str] = {}
    for scan_dir in SCAN_DIRS:
        for file in sorted((REPO_ROOT / scan_dir).rglob("*")):
            if file.is_file() and file.suffix in SOURCE_SUFFIXES:
                rel = file.relative_to(REPO_ROOT).as_posix()
                sources[rel] = file.read_text(encoding="utf-8")
    return sources


def check_scan_coverage(found: set[str]) -> None:
    """Raise AssertionError if the scan missed any required file.

    Why an empty or partial scan must fail: every rule passes trivially on
    zero files. If SCAN_DIRS pointed at a moved directory, or REPO_ROOT were
    wrong, the scan would find nothing and the whole module would go green
    while checking nothing.
    """
    missing = sorted(REQUIRED_FILES - found)
    if missing:
        raise AssertionError(f"firmware scan did not find: {', '.join(missing)}")


def _format(violations: list[Violation]) -> str:
    return "\n".join(str(v) for v in violations)


# --- Stripper tests -----------------------------------------------------------------


def test_strip_line_comment_keeps_newline() -> None:
    assert strip_comments("int a; // float\nint b;\n") == "int a; \nint b;\n"


def test_strip_block_comment_spanning_lines_keeps_line_count() -> None:
    text = "int a; /* float\n double\n */ int b;\n"
    stripped = strip_comments(text)
    assert "float" not in stripped and "double" not in stripped
    assert stripped.count("\n") == text.count("\n")
    assert stripped.rstrip().endswith("int b;")


def test_strip_keeps_code_after_block_comment_on_same_line() -> None:
    assert strip_comments("/* note */ float x;\n") == "  float x;\n"


def test_strip_block_comment_does_not_fuse_tokens() -> None:
    assert strip_comments("a/**/b") == "a b"


def test_strip_removes_float_string_literal() -> None:
    assert strip_comments('const char *s = "float";\n') == 'const char *s = "";\n'


def test_strip_handles_escaped_quote_in_string() -> None:
    # One literal "a\"b"; the code after it must survive.
    assert strip_comments('s = "a\\"b"; float x;') == 's = ""; float x;'


def test_strip_handles_double_quote_char_literal() -> None:
    # '"' must not open a string that swallows the rest of the line.
    assert strip_comments("c = '\"'; float x;") == "c = ''; float x;"


def test_strip_ignores_comment_markers_inside_string() -> None:
    assert strip_comments('u = "http://x"; float y;') == 'u = ""; float y;'
    assert strip_comments('u = "/* not a comment"; float y;') == 'u = ""; float y;'


def test_strip_keep_literals_keeps_strings_but_removes_comments() -> None:
    text = '#include "Wire.h" // comment\n'
    assert strip_comments(text, keep_literals=True) == '#include "Wire.h" \n'


# --- Rule tests on synthetic sources ---------------------------------------------------


def test_float_double_rule_fires_and_reports_line() -> None:
    text = "int a;\nfloat x;\ndouble y;\n"
    found = find_float_double("src/x.cpp", text)
    assert [(v.line, v.matched) for v in found] == [(2, "float"), (3, "double")]
    assert "src/x.cpp:2:" in str(found[0]) and "'float'" in str(found[0])


def test_float_double_rule_ignores_near_misses_comments_and_strings() -> None:
    text = 'int floating, doubled; /* float */ const char *s = "double";\n'
    assert find_float_double("src/x.cpp", text) == []


@pytest.mark.parametrize("code", [
    "p = malloc(4);",
    "p = calloc(1, 4);",
    "p = realloc(p, 8);",
    "free(p);",
    "free (p);",
    "T *t = new T;",
    "delete t;",
])
def test_allocation_rule_fires(code: str) -> None:
    assert len(find_dynamic_allocation("src/x.cpp", code)) == 1


def test_allocation_rule_ignores_near_misses() -> None:
    text = "pm_frame_next_seq(s); renew(); new_value = 1; free_bytes(b); freeze(); deleted = 0;\n"
    assert find_dynamic_allocation("src/x.cpp", text) == []


def test_arduino_header_rule_fires_in_logic_for_both_include_forms() -> None:
    text = '#include <stdint.h>\n#include <Arduino.h>\n#include "Wire.h"\n#  include <HardwareSerial.h>\n'
    found = find_arduino_headers_in_logic("lib/pm_logic/src/pm_x.c", text)
    assert [(v.line, v.matched) for v in found] == [
        (2, "Arduino.h"), (3, "Wire.h"), (4, "HardwareSerial.h"),
    ]


def test_arduino_header_rule_ignores_src_and_commented_includes() -> None:
    assert find_arduino_headers_in_logic("src/main.cpp", "#include <Arduino.h>\n") == []
    commented = "/* #include <Arduino.h> */\n// #include \"Wire.h\"\n"
    assert find_arduino_headers_in_logic("lib/pm_logic/src/pm_x.c", commented) == []


def test_wire_include_rule_fires_outside_driver() -> None:
    found = find_wire_include_outside_driver("src/main.cpp", '#include "Wire.h"\n')
    assert [(v.line, v.matched) for v in found] == [(1, "Wire.h")]
    found = find_wire_include_outside_driver("lib/pm_logic/src/pm_x.c", "#include <Wire.h>\n")
    assert len(found) == 1


def test_wire_include_rule_allows_driver() -> None:
    assert find_wire_include_outside_driver(WIRE_ALLOWED_FILE, "#include <Wire.h>\n") == []


def test_serial_rule_fires_outside_uart_driver() -> None:
    found = find_serial_outside_uart_driver("src/main.cpp", "void f() {\n  Serial.write(b, n);\n}\n")
    assert [(v.line, v.matched) for v in found] == [(2, "Serial")]


def test_serial_rule_allows_uart_driver_and_ignores_near_misses() -> None:
    assert find_serial_outside_uart_driver(SERIAL_ALLOWED_FILE, "Serial.begin(1);\n") == []
    text = "HardwareSerial *h; int SERIAL_8N1_x; /* Serial */ s = \"Serial\";\n"
    assert find_serial_outside_uart_driver("src/main.cpp", text) == []


def test_print_rule_fires_everywhere() -> None:
    text = "Serial.print(1);\nSerial.println(2);\n"
    found = find_print(SERIAL_ALLOWED_FILE, text)
    assert [(v.line, v.matched) for v in found] == [(1, "print"), (2, "println")]


def test_print_rule_ignores_near_misses() -> None:
    assert find_print("src/x.cpp", "blueprint(); printf_like(); sprint(); /* print */\n") == []


def test_coverage_check_fails_on_empty_scan() -> None:
    with pytest.raises(AssertionError, match="did not find"):
        check_scan_coverage(set())


def test_coverage_check_fails_on_partial_scan_and_names_missing_file() -> None:
    partial = set(REQUIRED_FILES) - {"lib/pm_logic/src/pm_time.c"}
    with pytest.raises(AssertionError, match="pm_time.c"):
        check_scan_coverage(partial)


# --- The real firmware sources ------------------------------------------------------------


def test_scan_finds_every_required_file() -> None:
    check_scan_coverage(set(scan_sources()))


@pytest.mark.parametrize("rule", ALL_RULES, ids=lambda rule: rule.__name__)
def test_firmware_sources_follow_rule(rule: Rule) -> None:
    sources = scan_sources()
    check_scan_coverage(set(sources))   # a vacuous pass is not a pass
    violations = [v for path, text in sources.items() for v in rule(path, text)]
    assert not violations, "\n" + _format(violations)
