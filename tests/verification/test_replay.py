"""Echte Mitschnitte (tests/fixtures/traces/field_logs.yaml) im Zusammenhang abgespielt.

``test_traces.py`` prüft jeden Mitschnitt einzeln. Hier laufen die Mitschnitte eines
Nutzer-Logs (gleiches Issue, gleicher Adapter, gleiches Fahrzeug) nacheinander durch
``scan()`` bzw. ``Elm327.protocol()``, byte-genau über ``RawTransport``.

Vollständige Scan-Abläufe (ATZ bis Mode 0A) enthalten die öffentlichen Logs nicht:
python-OBD liest beim Verbinden keine Fehlercodes. Abgespielt werden daher die Abläufe,
die bis zu ihrem Ende belegt sind, nämlich Verbindungsabbrüche bei ``0100``. Nicht
mitgeschnittene Konfigurationsbefehle (``ATE0``, ``ATL0``, ``ATS0``, ``ATH0``, ``ATSP0``)
beantwortet der Fake mit ``OK``, ein fehlendes ``ATRV`` ebenso (``scan`` ignoriert
dann die Spannung). Das ist die einzige Annahme; alles Übrige ist Originaltext.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

from obd_diag.protocol.elm327 import Elm327, ElmError
from obd_diag.protocol.headers import HeaderFormat
from obd_diag.services.diagnostics import scan
from tests.verification.helpers import HeaderRawTransport, RawTransport

FIELD_LOGS = Path(__file__).parent.parent / "fixtures" / "traces" / "field_logs.yaml"
_TRACES: dict[str, dict[str, Any]] = {
    t["id"]: t for t in yaml.safe_load(FIELD_LOGS.read_text(encoding="utf-8"))
}
_CONFIG = ["ATE0", "ATL0", "ATS0", "ATH0", "ATSP0"]


def _raw(trace_id: str, key: str = "raw") -> bytes:
    raw: str = _TRACES[trace_id][key]
    return raw.encode("latin-1")


def _session(*trace_ids: str) -> dict[str, bytes]:
    responses: dict[str, bytes] = {}
    for trace_id in trace_ids:
        responses[_TRACES[trace_id]["command"]] = _raw(trace_id)
    return responses


@pytest.mark.parametrize(
    ("trace_ids", "error"),
    [
        # python-OBD #187: Klon mit FC-Müll nach ATZ, ISO-Bus antwortet nicht
        (("atz-fc-junk", "bus-init-error"), "BUS INIT: ERROR"),
        # python-OBD #164: KWP-Fahrzeug, Bus-Initialisierung ohne Verbindung
        (("atz-single-cr", "atrv-13.6", "bus-init-unable"), "UNABLE TO CONNECT"),
        # python-OBD #173: Initialisierung durch neues Zeichen abgebrochen
        (("atz-blank-lines", "bus-init-stopped"), "STOPPED"),
    ],
)
def test_scan_replay_connection_failures(trace_ids: tuple[str, ...], error: str) -> None:
    transport = RawTransport(_session(*trace_ids))
    with pytest.raises(ElmError, match=error):
        scan(Elm327(transport), None)
    # nur lesend, Abbruch direkt nach 0100: kein ATDPN, kein Mode 03
    assert transport.sent == ["ATZ", *_CONFIG, "ATRV", "0100"]


@pytest.mark.parametrize(
    ("trace_id", "expected"),
    [
        ("bus-init-ok-iso9141", HeaderFormat.LEGACY),  # Audi TT 1998, 48 6B 11 …
        ("bus-init-ok-kwp", HeaderFormat.LEGACY),  # 86 F1 10 …
        ("searching-j1850", HeaderFormat.LEGACY),  # Volvo V40 2003, 48 6B 13 …
        ("searching-can-two-ecus", HeaderFormat.CAN_11),  # 7E9/7E8
        ("kwp-negative-response", HeaderFormat.LEGACY),  # 83 F1 10 7F 01 11
    ],
)
def test_protocol_inferred_from_real_headers(trace_id: str, expected: HeaderFormat) -> None:
    """Meldet ``ATDPN`` nichts Brauchbares (hier angenommen: ``A0``), erkennt
    ``protocol()`` das Protokoll an den echten Header-Zeilen des Mitschnitts."""
    responses = {"0100": _raw(trace_id), "ATDPN": b"A0\r\r>", "ATDP": b"AUTO\r\r>"}
    transport = HeaderRawTransport(responses, {"0100": _raw(trace_id, "headers_on")})
    protocol = Elm327(transport).protocol()
    assert transport.sent == ["0100", "ATDPN", "ATDPN", "ATH1", "0100", "ATH0", "ATDP"]
    assert protocol.inferred is expected
    assert protocol.is_can is expected.is_can
