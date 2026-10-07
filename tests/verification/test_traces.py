"""Echte Adapter-Antworten (Datenblatt, Nutzer-Mitschnitte) durch den ganzen Stapel.

Die Mitschnitte liegen in tests/fixtures/traces/*.yaml, mit Quelle je Eintrag. Jeder
Mitschnitt läuft über ``Elm327.command`` (byte-genau, mit Prompt) und dann je nach
Erwartung über ``read_dtcs``, ``split_messages``, ``read_pid`` usw. Zusätzlich wird jeder
Mitschnitt mit Echo und mit ``\\r\\n`` (ATL1) wiederholt, wie es Adapter liefern, die
ATE0/ATL0 ignorieren.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from obd_diag.protocol.elm327 import Elm327, ElmError, NoDataError, UnknownCommandError
from obd_diag.protocol.frames import split_messages
from obd_diag.protocol.obd import read_dtcs, read_pid, read_rpm
from tests.verification.helpers import RawTransport

TRACES = Path(__file__).parent.parent / "fixtures" / "traces"
_ERRORS: dict[str, type[ElmError]] = {
    "ElmError": ElmError,
    "NoDataError": NoDataError,
    "UnknownCommandError": UnknownCommandError,
}


def _load(required: str = "raw") -> list[Any]:
    params = []
    for path in sorted(TRACES.glob("*.yaml")):
        for trace in yaml.safe_load(path.read_text(encoding="utf-8")):
            if required in trace:
                params.append(pytest.param(trace, id=f"{path.stem}:{trace['id']}"))
    return params


def _variants(trace: dict[str, Any]) -> list[bytes]:
    """Original, mit Echo davor, mit CR LF; Rohbytes stehen als Latin-1 im YAML."""
    raw: bytes = trace["raw"].encode("latin-1")
    echo = trace["command"].encode("ascii") + b"\r"
    variants = [raw, raw.replace(b"\r", b"\r\n")]
    if not raw.startswith(echo):
        variants.append(echo + raw)
    return variants


def _compact(hex_text: str) -> bytes:
    return bytes.fromhex(hex_text.replace(" ", ""))


def _check(trace: dict[str, Any], raw: bytes) -> None:
    cmd: str = trace["command"]
    transport = RawTransport({cmd: raw})
    elm = Elm327(transport)
    if "error" in trace:
        with pytest.raises(_ERRORS[trace["error"]]):
            elm.command(cmd)
    elif "text" in trace:
        assert elm.command(cmd) == trace["text"]
    elif "version" in trace:
        assert elm.initialize() == trace["version"]
    elif "voltage" in trace:
        assert elm.voltage() == trace["voltage"]
    elif "codes" in trace:
        assert read_dtcs(elm, int(cmd, 16), can=trace["can"]) == trace["codes"]
    elif "messages" in trace:
        assert split_messages(elm.command(cmd)) == [_compact(m) for m in trace["messages"]]
    elif "split_error" in trace:
        with pytest.raises(ValueError):
            split_messages(elm.command(cmd))
    elif "vin" in trace:
        (message,) = split_messages(elm.command(cmd))
        assert message[:3] == bytes((0x49, 0x02, 0x01))
        assert message[3:].decode("ascii") == trace["vin"]
    elif "pid" in trace:
        expected = trace["pid"]["data"]
        data = read_pid(elm, trace["pid"]["pid"])
        assert data == (None if expected is None else _compact(expected))
    elif "rpm" in trace:
        assert read_rpm(elm) == trace["rpm"]
    else:
        pytest.fail(f"Mitschnitt {trace['id']} ohne Erwartung")


@pytest.mark.parametrize("trace", _load())
def test_trace(trace: dict[str, Any]) -> None:
    for raw in _variants(trace):
        _check(trace, raw)


def _headers_off(line: str) -> str:
    """Rechnet eine Zeile mit Headern (ATH1) eindeutig nach ATH0 um, siehe field_logs.yaml."""
    can = re.match(r"^7E[8-F] ([0-9A-F]{2}) (.*?)( ?)$", line)
    if can:
        length = int(can.group(1), 16)
        assert length <= 7, "nur Einzel-Frames sind eindeutig umzurechnen"
        data = can.group(2).split()
        assert len(data) >= length
        return " ".join(data[:length]) + can.group(3)
    legacy = re.match(r"^((?:[0-9A-F]{2} ){4,}[0-9A-F]{2})( ?)$", line)
    if legacy:
        data = legacy.group(1).split()
        return " ".join(data[3:-1]) + legacy.group(2)
    return line


@pytest.mark.parametrize("trace", _load("headers_on"))
def test_headers_off_conversion(trace: dict[str, Any]) -> None:
    original = trace["headers_on"].split(">")[0]
    converted = "\r".join(_headers_off(line) for line in original.split("\r"))
    assert converted + ">" == trace["raw"]


def test_conversion_rule_matches_datasheet() -> None:
    # ELM327DS S. 44: dieselben Antworten mit und ohne Header
    assert _headers_off("48 6B 10 41 00 BE 3E B8 11 FA") == "41 00 BE 3E B8 11"
    assert _headers_off("48 6B 18 41 00 80 10 80 00 C0") == "41 00 80 10 80 00"


def test_double_prompt_leaves_stale_prompt() -> None:
    """Mitschnitt python-OBD #173: zweiter Prompt nach STOPPED.

    Dokumentiert das Verhalten: der überzählige Prompt wird als (leere) Antwort auf den
    nächsten Befehl gelesen. Die Transport-Schicht verwirft alte Eingaben nicht.
    """
    transport = RawTransport({"0100": b"41 00 BE 3F A8 13 \rSTOPPED\r\r>\r>", "ATDPN": b"A6\r\r>"})
    elm = Elm327(transport)
    with pytest.raises(ElmError, match="STOPPED"):
        elm.command("0100")
    assert elm.command("ATDPN") == ""  # gehört eigentlich noch zu 0100
    assert elm.command("ATRV") == "A6"  # und ab hier ist alles um eine Antwort verschoben
