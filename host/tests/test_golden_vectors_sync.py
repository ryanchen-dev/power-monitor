"""Drift test: the Golden_Vectors must be identical in all three places they live.

The same Frame bytes are written out in docs/packet_format.md (the Packet_Spec),
test/test_frame/test_main.c (Unity), and host/tests/test_protocol.py (pytest).
This test reads all three files as text and fails if they disagree (Req 14.4).

Why parse text instead of generating the vectors from one file: a generator
(for example a script that writes a C header and a Python module) would be an
extra build step to run, document, and explain, and it would have to be kept in
sync itself. Three 25-byte arrays are small enough to copy by hand, and this
test catches the copy going wrong.

Why test_protocol.py is read as text rather than imported: the literal a human
edits is what must match the doc, so that is what gets checked. It also keeps
this test independent of how the fixture is built (if GOLDEN_VECTORS were ever
computed with encode_frame, importing it would compare the encoder with itself).
"""

import re
from pathlib import Path
from typing import Callable

import pytest

from power_monitor.protocol import FRAME_LEN_BYTES, decode_frame

# Located from this file, not the current directory, so the test finds the
# same files whether pytest is run from host/ or from the repo root.
# parents[0] is host/tests, parents[1] is host, parents[2] is the repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_PATH = "docs/packet_format.md"
C_TEST_PATH = "test/test_frame/test_main.c"
PY_FIXTURE_PATH = "host/tests/test_protocol.py"

MIN_VECTOR_COUNT = 2  # Req 14.3: "at least two Golden_Vectors"

# (offset, size in bytes) of every multi-byte field, written out from the
# layout table in docs/packet_format.md rather than taken from protocol.py, so
# this check does not trust the code it is meant to check.
MULTI_BYTE_FIELDS: dict[str, tuple[int, int]] = {
    "seq": (3, 2),
    "timestamp_ms": (5, 4),
    "bus_voltage_mv": (10, 2),
    "current_ua": (12, 4),
    "power_mw": (16, 4),
    "energy_uwh": (20, 4),
}

# A vector is (name, Frame bytes). The doc and the C file name their vectors
# (GV1, GV2, ...); the Python literals have no names, so they get None and are
# identified by position.
Vector = tuple[str | None, bytes]

# "GVn:" at the start of a line, then only hex byte pairs to the end of the
# line. The start anchor skips "GV1" in the table and the prose, and the end
# anchor stops a prose line like "GV1: be ad..." from half-matching.
_DOC_LINE = re.compile(r"^(GV\d+):((?:[ \t]+[0-9A-Fa-f]{2})+)[ \t]*$", re.MULTILINE)

# Only uint8_t arrays named GVn_BYTES, so the GVn_SAMPLE structs never match.
# [^}]* spans the line breaks inside the braces.
_C_ARRAY = re.compile(
    r"static\s+const\s+uint8_t\s+(GV\d+)_BYTES\s*\[[^\]]*\]\s*=\s*\{([^}]*)\}\s*;"
)
# \b on both sides so "0x1234" is not read as the byte 0x12. The optional u
# allows C's unsigned suffix (0x6Bu).
_C_BYTE = re.compile(r"\b0[xX]([0-9A-Fa-f]{2})[uU]?\b")

# Requires a quoted string, so the comment "bytes.fromhex(...)" doesn't match.
_PY_LITERAL = re.compile(r'bytes\.fromhex\(\s*"([^"]*)"\s*\)')


def _require_some(vectors: list[Vector], source: str) -> list[Vector]:
    # Zero vectors must fail, not pass: if someone reformats a file so the
    # pattern stops matching, every source could come back empty, and three
    # empty lists are "identical". The test would then pass while checking
    # nothing.
    if not vectors:
        raise AssertionError(
            f"{source}: found no Golden_Vectors; was the file's format changed?"
        )
    return vectors


def parse_doc_vectors(text: str, source: str) -> list[Vector]:
    """The "GVn: AA 55 ..." lines of the Packet_Spec, in file order."""
    vectors = [(m.group(1), bytes.fromhex(m.group(2))) for m in _DOC_LINE.finditer(text)]
    return _require_some(vectors, source)


def parse_c_vectors(text: str, source: str) -> list[Vector]:
    """The GVn_BYTES arrays of the Unity test, in file order."""
    vectors = [
        (m.group(1), bytes(int(byte, 16) for byte in _C_BYTE.findall(m.group(2))))
        for m in _C_ARRAY.finditer(text)
    ]
    return _require_some(vectors, source)


def parse_python_vectors(text: str, source: str) -> list[Vector]:
    """The bytes.fromhex("...") literals of the pytest fixture, in file order."""
    vectors: list[Vector] = [(None, bytes.fromhex(m.group(1))) for m in _PY_LITERAL.finditer(text)]
    return _require_some(vectors, source)


def _expected_name(index: int) -> str:
    return f"GV{index + 1}"


def _first_difference(a: bytes, b: bytes) -> int:
    for offset, (byte_a, byte_b) in enumerate(zip(a, b)):
        if byte_a != byte_b:
            return offset
    return min(len(a), len(b))  # one is a prefix of the other


def assert_same_vectors(sources: dict[str, list[Vector]]) -> None:
    """Raise AssertionError at the first disagreement, naming the vector and sources.

    The first source is the reference (the doc, since it is authoritative).
    """
    # Names are checked first, so vectors declared out of order are reported
    # as an ordering problem rather than as a confusing byte difference.
    for source, vectors in sources.items():
        for index, (name, _) in enumerate(vectors):
            if name is not None and name != _expected_name(index):
                raise AssertionError(
                    f"{source}: vector {index + 1} is named {name}, "
                    f"expected {_expected_name(index)} (vectors must be GV1, GV2, ... in order)"
                )

    (ref_source, ref_vectors), *others = sources.items()
    for source, vectors in others:
        if len(vectors) != len(ref_vectors):
            raise AssertionError(
                f"{ref_source} has {len(ref_vectors)} vectors but {source} has {len(vectors)}"
            )
        for index, ((_, ref_bytes), (_, other_bytes)) in enumerate(zip(ref_vectors, vectors)):
            if other_bytes != ref_bytes:
                raise AssertionError(
                    f"{_expected_name(index)} differs between {ref_source} and {source} "
                    f"at byte offset {_first_difference(ref_bytes, other_bytes)}:\n"
                    f"  {ref_source}: {ref_bytes.hex(' ').upper()}\n"
                    f"  {source}: {other_bytes.hex(' ').upper()}"
                )


def _lsb_differs_from_msb_in_every_field(frame: bytes) -> bool:
    # Little-endian: the field's first byte is its LSB, its last byte the MSB.
    return all(
        frame[offset] != frame[offset + size - 1]
        for offset, size in MULTI_BYTE_FIELDS.values()
    )


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def load_real_sources() -> dict[str, list[Vector]]:
    """Parse the three real files. The doc comes first, as the reference."""
    return {
        DOC_PATH: parse_doc_vectors(_read(DOC_PATH), DOC_PATH),
        C_TEST_PATH: parse_c_vectors(_read(C_TEST_PATH), C_TEST_PATH),
        PY_FIXTURE_PATH: parse_python_vectors(_read(PY_FIXTURE_PATH), PY_FIXTURE_PATH),
    }


# --- Checks on the real files ------------------------------------------------


def test_all_sources_hold_the_same_vectors() -> None:
    assert_same_vectors(load_real_sources())


# The remaining checks read the doc only: the test above already proves the
# other two sources hold the same bytes.


def test_there_are_at_least_two_vectors() -> None:
    vectors = load_real_sources()[DOC_PATH]
    assert len(vectors) >= MIN_VECTOR_COUNT, f"{DOC_PATH} has only {len(vectors)} vector(s)"


def test_every_vector_is_one_frame_long() -> None:
    for source, vectors in load_real_sources().items():
        for index, (_, frame) in enumerate(vectors):
            assert len(frame) == FRAME_LEN_BYTES, (
                f"{source}: {_expected_name(index)} is {len(frame)} bytes, expected {FRAME_LEN_BYTES}"
            )


def test_some_vector_has_negative_current_and_power_and_flags_set() -> None:
    # Negative values catch sign-extension and signed/unsigned mix-ups, which
    # all-positive vectors can't show (Req 14.3).
    samples = [decode_frame(frame) for _, frame in load_real_sources()[DOC_PATH]]
    assert any(
        s.current_ua < 0 and s.power_mw < 0 and s.status_flags != 0 for s in samples
    ), f"no vector in {DOC_PATH} has negative current_ua and power_mw with status_flags set"


def test_some_vector_has_lsb_different_from_msb_in_every_field() -> None:
    # If a field's LSB equals its MSB (0 = 00 00, or 0x1212), writing it in the
    # wrong byte order produces the same bytes, so the vector can't catch that
    # bug. With LSB != MSB in every field, any byte-order mistake changes the
    # Frame and the Golden_Vector test fails (Req 14.3).
    frames = [frame for _, frame in load_real_sources()[DOC_PATH]]
    assert any(_lsb_differs_from_msb_in_every_field(f) for f in frames), (
        f"no vector in {DOC_PATH} has LSB != MSB in every multi-byte field"
    )


# --- Checks on the parsers and comparison, using small in-memory texts -------
# These show the checks above can fail, without touching the real files.


def test_doc_parser_ignores_table_and_prose() -> None:
    text = (
        "| `seq` | GV1 | GV2 |\n"
        "- **GV1** (normal Sample): GV1: AA 55\n"
        "GV1 is the normal Sample.\n"
        "GV1: normal sample\n"
        "GV1: AA 55 19\n"
        "GV2: 00 01\n"
    )
    assert parse_doc_vectors(text, "doc") == [("GV1", b"\xAA\x55\x19"), ("GV2", b"\x00\x01")]


def test_c_parser_ignores_sample_structs() -> None:
    text = (
        "static const uint8_t GV1_BYTES[PM_FRAME_LEN_BYTES] = {\n"
        "    0xAA, 0x55,\n"
        "    0x19\n"
        "};\n"
        "static const pm_sample_t GV1_SAMPLE = {\n"
        "    .seq = UINT16_C(0x1234),\n"
        "};\n"
    )
    assert parse_c_vectors(text, "c") == [("GV1", b"\xAA\x55\x19")]


def test_python_parser_reads_literals_in_order_and_skips_comments() -> None:
    text = (
        "# Keep one bytes.fromhex(...) literal per vector.\n"
        'a = bytes.fromhex("AA 55")\n'
        'b = bytes.fromhex("00 01")\n'
    )
    assert parse_python_vectors(text, "py") == [(None, b"\xAA\x55"), (None, b"\x00\x01")]


@pytest.mark.parametrize(
    "parser", [parse_doc_vectors, parse_c_vectors, parse_python_vectors]
)
def test_finding_no_vectors_fails_and_names_the_file(
    parser: Callable[[str, str], list[Vector]],
) -> None:
    with pytest.raises(AssertionError, match="some/file.txt: found no Golden_Vectors"):
        parser("GV1 appears here, but no vector does.\n", "some/file.txt")


def test_one_byte_difference_names_vector_sources_and_offset() -> None:
    sources: dict[str, list[Vector]] = {
        "doc": [("GV1", b"\xAA\x55\x19"), ("GV2", b"\xAA\x55\x19")],
        "c": [("GV1", b"\xAA\x55\x19"), ("GV2", b"\xAA\x55\x19")],
        "py": [(None, b"\xAA\x55\x19"), (None, b"\xAA\x55\x18")],
    }
    with pytest.raises(AssertionError, match=r"GV2 differs between doc and py at byte offset 2"):
        assert_same_vectors(sources)


def test_vectors_out_of_order_are_reported() -> None:
    sources: dict[str, list[Vector]] = {
        "doc": [("GV1", b"\x01"), ("GV2", b"\x02")],
        "c": [("GV2", b"\x02"), ("GV1", b"\x01")],
    }
    with pytest.raises(AssertionError, match="c: vector 1 is named GV2, expected GV1"):
        assert_same_vectors(sources)
