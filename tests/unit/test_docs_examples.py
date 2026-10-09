"""Die Byte-Beispiele der Dokumentation (``docs/technik``) stimmen mit dem Code überein.

Wer ein Beispiel auf den Seiten ändert, ändert es hier mit. Außerdem: Die erzeugten
Tabellen (``tools/docs_tables.py``) decken jede Freigabe und jeden Live-Wert ab.
"""

import re
from pathlib import Path

import pytest

from obd_diag.protocol.dtc_decode import decode_dtc, parse_dtc_messages, parse_dtc_response
from obd_diag.protocol.frames import FrameSequenceError, split_messages
from obd_diag.protocol.headers import HeaderFormat, header_format, parse_header_response
from obd_diag.protocol.pids import PIDS, parse_supported
from obd_diag.services.readiness import MonitorState, combine_readiness, decode_readiness
from obd_diag.services.vehicle import parse_vin_response
from tests.command_spec import load_spec
from tools.docs_tables import command_tables, formula, pid_table, write_tables

DOCS = Path(__file__).resolve().parents[2] / "docs"


def _page(name: str) -> str:
    return (DOCS / "technik" / name).read_text(encoding="utf-8")


def test_single_frame_rpm() -> None:
    assert "410C1AF8" in _page("antwortformate.md")
    (message,) = split_messages("410C1AF8")
    assert message[:2] == b"\x41\x0c"
    assert PIDS["rpm"].decode(message[2:4]) == 1726


def test_multi_frame_without_headers() -> None:
    response = "00A\n0: 430404200133\n1: 0300C100"
    assert response in _page("antwortformate.md")
    assert parse_dtc_response(response, 0x03, can=True) == ["P0420", "P0133", "P0300", "U0100"]


def test_missing_frame_is_detected() -> None:
    with pytest.raises(FrameSequenceError):
        split_messages("00A\n0: 430404200133\n2: 0300C100")


def test_multi_frame_with_headers() -> None:
    response = "7E8 10 0A 43 04 04 20 01 33\n7E9 04 43 01 01 71\n7E8 21 03 00 C1 00 00 00 00"
    assert response in _page("antwortformate.md")
    messages = parse_header_response(response)
    assert [m.ecu for m in messages] == [0x7E8, 0x7E9]
    assert parse_dtc_messages([messages[0].data], 0x03, can=True) == [
        "P0420",
        "P0133",
        "P0300",
        "U0100",
    ]
    assert parse_dtc_messages([messages[1].data], 0x03, can=True) == ["P0171"]


@pytest.mark.parametrize(
    ("line", "fmt"),
    [
        ("7E8 06 41 00 BE 3F A8 13", HeaderFormat.CAN_11),
        ("18 DA F1 10 06 41 00 BE 3F A8 13", HeaderFormat.CAN_29),
        ("48 6B 10 41 00 BE 3E B8 11 FA", HeaderFormat.LEGACY),
    ],
)
def test_header_formats(line: str, fmt: HeaderFormat) -> None:
    assert line in _page("antwortformate.md")
    assert header_format(line) is fmt
    (message,) = parse_header_response(line)
    assert message.data[:2] == b"\x41\x00"


@pytest.mark.parametrize(
    ("response", "can", "codes"),
    [
        ("430204200133", True, ["P0420", "P0133"]),
        ("4300", True, []),
        ("43042001330000", False, ["P0420", "P0133"]),
    ],
)
def test_count_byte_table(response: str, can: bool, codes: list[str]) -> None:
    assert f"`{response}`" in _page("antwortformate.md")
    assert parse_dtc_response(response, 0x03, can=can) == codes


def test_supported_pids_example() -> None:
    assert "4100BE3FA813" in _page("dienste.md")
    supported = parse_supported(0x00, bytes.fromhex("BE3FA813"))
    assert {0x01, 0x03, 0x04, 0x05, 0x06, 0x07} <= supported
    assert 0x02 not in supported and 0x08 not in supported


def test_dtc_bytes_examples() -> None:
    assert decode_dtc(0x04, 0x20) == "P0420"
    assert decode_dtc(0xC1, 0x00) == "U0100"
    assert "4202000420" in _page("dienste.md")


def test_vin_example() -> None:
    response = "014\n0: 490201575657\n1: 5A5A5A314B5A36\n2: 57313233343536"
    assert response in _page("dienste.md")
    assert parse_vin_response(response) == "WVWZZZ1KZ6W123456"


def test_every_live_value_has_a_formula() -> None:
    table = pid_table()
    for spec in PIDS.values():
        text = formula(spec.decode)
        assert text and "d[" not in text and "_" not in text, (spec.key, text)
        assert f"`{spec.key}`" in table


def test_formulas_match_values() -> None:
    """Stichprobe: übersetzte Lambda-Formeln stimmen mit dem Ergebnis überein."""
    assert formula(PIDS["rpm"].decode) == "(256A + B) / 4"
    assert formula(PIDS["torque_point2"].decode) == "C - 125"
    assert formula(PIDS["maf_b"].decode).startswith("(256D + E) / 32")


def test_command_table_lists_every_spec_entry() -> None:
    table = command_tables()
    spec = load_spec()
    for entry in spec.allowed:
        cmd = entry.command if entry.command is not None else entry.pattern
        assert f"`{cmd}`" in table
        assert f"S. {entry.page}" in table
    for forbidden in spec.forbidden:
        if forbidden.command:
            assert f"`{forbidden.command}`" in table
    assert len(re.findall(r"\*\*ja\*\*", table)) == 1  # nur 04 schreibt


def test_write_tables_is_idempotent(tmp_path: Path) -> None:
    first = write_tables(tmp_path)
    stamps = [p.stat().st_mtime_ns for p in first]
    second = write_tables(tmp_path)
    assert [p.stat().st_mtime_ns for p in second] == stamps


def test_real_car_page_matches_trace() -> None:
    """Die Zeilen der W177-Seite stehen so im Regressions-Mitschnitt."""
    trace = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "traces"
        / "mercedes_w177"
        / "diagnose.log"
    ).read_text(encoding="utf-8")
    page = _page("mitschnitt-w177.md")
    for line in trace.splitlines():
        if ">> 0" in line and "0142" not in line:  # Anfragen ans Fahrzeug
            request = line.split(">> ")[1]
            assert f">> {request}" in page, request
    assert "4301D218\\r4300\\r4300\\r4300" in page
    assert "2:4A303030303030" in page and "WDD1770031J000000" in page


def test_real_car_supported_pids() -> None:
    page = _page("mitschnitt-w177.md")
    assert "`98 18 00 01`" in page and "`98 18 00 11`" in page
    assert parse_supported(0x00, bytes.fromhex("98180001")) == {1, 4, 5, 0x0C, 0x0D, 0x20}
    assert parse_supported(0x00, bytes.fromhex("98180011")) == {1, 4, 5, 0x0C, 0x0D, 0x1C, 0x20}
    assert parse_supported(0x60, bytes.fromhex("05190001")) == {0x66, 0x68, 0x6C, 0x6D, 0x70, 0x80}
    assert parse_supported(0x80, bytes.fromhex("09004000")) == {0x85, 0x88, 0x92}
    offered = {0x68, 0x6C, 0x6D, 0x70, 0x85, 0x88, 0x92}
    assert not offered & {spec.pid for spec in PIDS.values()}  # „liest obd-diag noch nicht“


def test_real_car_values() -> None:
    assert parse_dtc_response("4301D218", 0x03, can=True) == ["U1218"]
    assert PIDS["control_voltage"].decode(bytes.fromhex("2EDC")) == pytest.approx(11.996)
    assert PIDS["coolant_temp"].decode(bytes.fromhex("4D")) == 37
    assert PIDS["coolant_temp"].decode(bytes.fromhex("3B")) == 19
    response = "014\n0:490201574444\n1:31373730303331\n2:4A303030303030"
    assert parse_vin_response(response) == "WDD1770031J000000"


def test_real_car_readiness() -> None:
    page = _page("mitschnitt-w177.md")
    lines = ["000EEB20", "00040000", "000C0200", "010C0000"]
    for line in lines:
        assert " ".join(line[i : i + 2] for i in range(0, 8, 2)) in page
    statuses = [decode_readiness(bytes.fromhex(line)) for line in lines]
    assert [s.compression_ignition for s in statuses] == [True, False, True, True]
    assert [s.dtc_count for s in statuses] == [0, 0, 0, 1]
    combined = combine_readiness(statuses)
    assert not combined.mil_on and combined.dtc_count == 1
    open_monitors = [m.key for m in combined.monitors if m.state is MonitorState.INCOMPLETE]
    assert open_monitors == ["exhaust_gas_sensor"]
    assert combined.monitors[0].state is MonitorState.NOT_SUPPORTED  # Aussetzer
