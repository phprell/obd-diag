"""Adapter-Check: Version, Befehlssatz, Urteil und UDS-Eignung aus den Adapter-Antworten.

Die Versionen, seit denen es die geprüften Befehle gibt, stehen in der Versionsliste des
ELM327-Datenblatts (ELM327DSJ S. 90 bis 91): ``ATPPS`` v1.1, ``ATIGN``/``ATRD`` v1.4,
``ATAMC`` v2.0. Antwortformate nach dem Datenblatt: ``ATI`` „ELM327 v2.1“ (S. 18), ``AT@1``
„OBDII to RS232 Interpreter“ (S. 28), ``ATPPS`` „hh:vv N/F“ (S. 68), ``ATIGN`` „ON“/„OFF“
(S. 19).
"""

import json

import pytest

from obd_diag import cli, i18n
from obd_diag.cli import main
from obd_diag.protocol.elm327 import Elm327
from obd_diag.services import adapter_check
from obd_diag.services.adapter_check import (
    PROBE_TIMEOUT,
    AdapterKind,
    UdsSuitability,
    check_adapter,
    kind_text,
    parameters_text,
    report_to_dict,
    uds_text,
    version_text,
    voltage_text,
)
from obd_diag.transport import TransportError, TransportTimeout
from tests.fakes import ADAPTER_PPS_DEFAULT, ADAPTER_V15, ADAPTER_V21, FakeTransport

# Beispiel aus dem Datenblatt (S. 68, Auszug): nur PP 01 eingeschaltet, Wert 00
PPS_EXAMPLE = ADAPTER_PPS_DEFAULT.replace("01:FF F", "01:00 N")
GENUINE = ADAPTER_V21
CLONE_15 = ADAPTER_V15


def _check(responses: dict[str, str]) -> adapter_check.AdapterReport:
    return check_adapter(Elm327(FakeTransport(responses)))


def test_genuine_v21_matches_and_suits_uds() -> None:
    report = _check(GENUINE)
    assert report.identification == "ELM327 v2.1"
    assert report.description == "OBDII to RS232 Interpreter"
    assert report.version == "2.1"
    assert report.version_known
    assert report.command_set == "2.0"
    assert report.missing == ()
    assert report.kind is AdapterKind.MATCHES
    assert report.uds is UdsSuitability.LIKELY
    assert report.voltage == 12.6
    assert report.parameters == ()
    assert kind_text(report) == "passt zur gemeldeten Version (Befehlssatz wie v2.0)"
    assert parameters_text(report) == "alle auf Werkseinstellung"


def test_v15_is_a_clone_but_likely_suits_uds() -> None:
    report = _check(CLONE_15)
    assert report.version == "1.5"
    assert not report.version_known  # gibt es laut Versionsliste nicht
    assert report.command_set == "1.4"
    assert report.missing == ()  # ATAMC kam erst mit v2.0
    assert report.kind is AdapterKind.CLONE
    assert report.uds is UdsSuitability.LIKELY
    assert "Nachbau" in version_text(report)
    assert kind_text(report) == "Nachbau, Befehlssatz wie v1.4"
    assert uds_text(report).startswith("voraussichtlich ja")


def test_fake_v21_without_v20_commands_is_a_clone() -> None:
    report = _check({**GENUINE, "ATAMC": "?"})
    assert report.version_known
    assert report.missing == ("ATAMC",)
    assert report.kind is AdapterKind.CLONE
    assert kind_text(report) == "Nachbau, Befehlssatz wie v1.4; es fehlt ATAMC"
    assert report.uds is UdsSuitability.LIKELY


def test_clone_without_v14_commands_is_uncertain_for_uds() -> None:
    report = _check({**CLONE_15, "ATRD": "?"})
    assert report.command_set == "1.1"
    assert report.missing == ("ATRD",)  # v1.5 läge über v1.4
    assert report.uds is UdsSuitability.POSSIBLE
    assert "v1.1" in uds_text(report)


def test_basic_commands_only_is_unlikely_for_uds() -> None:
    report = _check({**CLONE_15, "ATPPS": "?", "ATIGN": "?", "ATRD": "?"})
    assert report.command_set == "1.0"
    assert report.uds is UdsSuitability.UNLIKELY
    assert report.parameters is None
    assert parameters_text(report) == "nicht lesbar (ATPPS)"


def test_supported_command_set_stops_at_the_first_gap() -> None:
    # ATAMC ja, ATPPS nein: belegt ist nur v1.0, auch wenn spätere Befehle antworten
    report = _check({**GENUINE, "ATPPS": "?"})
    assert report.command_set == "1.0"
    assert report.missing == ("ATPPS",)


def test_missing_v10_commands_give_no_command_set() -> None:
    report = _check({**GENUINE, "AT@1": "?"})
    assert report.command_set is None
    assert report.description is None
    assert report.uds is UdsSuitability.UNLIKELY
    assert "Grundbefehle" in kind_text(report)


def test_unknown_version_falls_back_to_the_reset_id() -> None:
    report = _check({**GENUINE, "ATZ": "OBDII v1.4b", "ATI": "?"})
    assert report.identification == "OBDII v1.4b"
    assert report.version == "1.4b"
    assert report.version_known


def test_no_version_at_all() -> None:
    report = _check({**GENUINE, "ATZ": "OBDII", "ATI": "OBDII"})
    assert report.version is None
    assert report.kind is AdapterKind.UNKNOWN
    assert report.missing == ()
    assert version_text(report) == "keine Versionsangabe erkennbar"


@pytest.mark.parametrize(
    ("ident", "version", "known"),
    [
        ("ELM327 v1.4b", "1.4b", True),
        ("ELM327 v1.4a", "1.4a", False),  # „Elm Electronics never made a v1.4a“
        ("ELM327 v1.3a", "1.3a", True),
        ("ELM327 v2.3", "2.3", True),  # neuer als das Datenblatt
        ("ELM327 V1.5", "1.5", False),
    ],
)
def test_versions_against_the_datasheet_list(ident: str, version: str, known: bool) -> None:
    report = _check({**GENUINE, "ATI": ident})
    assert report.version == version
    assert report.version_known is known


def test_newer_version_than_datasheet_is_checked_like_v21() -> None:
    report = _check({**GENUINE, "ATI": "ELM327 v2.3", "ATAMC": "?"})
    assert report.missing == ("ATAMC",)
    assert "neuer als das Datenblatt v2.1" in version_text(report)


def test_older_genuine_version_need_not_know_newer_commands() -> None:
    report = _check({**GENUINE, "ATI": "ELM327 v1.3", "ATIGN": "?", "ATRD": "?", "ATAMC": "?"})
    assert report.missing == ()
    assert report.kind is AdapterKind.MATCHES
    assert report.uds is UdsSuitability.POSSIBLE


def test_enabled_parameters_are_listed() -> None:
    report = _check({**GENUINE, "ATPPS": PPS_EXAMPLE})
    assert report.parameters == (("01", "00"),)
    assert parameters_text(report).startswith("geändert: PP 01 = 00;")


def test_unreadable_parameter_summary() -> None:
    assert _check({**GENUINE, "ATPPS": "OK"}).parameters is None


@pytest.mark.parametrize(
    ("reply", "text"),
    [
        ("12.6V", "12,6 V"),
        ("11.2V", "11,2 V: niedrig, Batterie laden"),
        ("0.2V", "0,2 V: Adapter steckt vermutlich nicht im Auto"),
        ("?", "nicht lesbar (ATRV)"),
        ("XYZ", "nicht lesbar (ATRV)"),
    ],
)
def test_voltage_text(reply: str, text: str) -> None:
    assert voltage_text(_check({**GENUINE, "ATRV": reply})) == text


def test_rejected_and_failing_probes_count_as_unsupported() -> None:
    report = _check({**GENUINE, "ATIGN": "STOPPED", "ATRD": ""})
    assert not report.results[4].supported
    assert not report.results[5].supported


class _SilentOn(FakeTransport):
    """Antwortet auf einen Befehl gar nicht; merkt sich die Wartezeit."""

    def __init__(self, silent: str, responses: dict[str, str]) -> None:
        super().__init__(responses)
        self.silent = silent
        self.timeouts: dict[str, float] = {}

    def read_until(self, terminator: bytes, timeout: float) -> bytes:
        self.timeouts[self.sent[-1]] = timeout
        if self.sent[-1] == self.silent:
            self._pending = b""
            raise TransportTimeout("keine Antwort")
        return super().read_until(terminator, timeout)


def test_unanswered_probe_counts_as_unsupported_with_short_timeout() -> None:
    transport = _SilentOn("ATAMC", GENUINE)
    elm = Elm327(transport)
    report = check_adapter(elm)
    assert not report.results[-1].supported
    assert transport.timeouts["ATAMC"] == PROBE_TIMEOUT
    assert transport.timeouts["ATZ"] == elm.timeout > PROBE_TIMEOUT  # wieder zurückgestellt


def test_lost_connection_aborts() -> None:
    class Unplugged(FakeTransport):
        def write(self, data: bytes) -> None:
            if data.startswith(b"ATPPS"):
                raise TransportError("Verbindung zu /dev/ttyUSB0 unterbrochen")
            super().write(data)

    elm = Elm327(Unplugged(GENUINE))
    with pytest.raises(TransportError):
        check_adapter(elm)
    assert elm.timeout > PROBE_TIMEOUT


def test_report_as_dict() -> None:
    data = report_to_dict(_check({**CLONE_15, "ATPPS": PPS_EXAMPLE}))
    assert data["version"] == "1.5"
    assert data["kind"] == "clone"
    assert data["uds"] == "likely"
    assert data["commands"]["ATAMC"] is None
    assert data["commands"]["ATIGN"] == "ON"
    assert data["parameters_on"] == {"01": "00"}
    json.dumps(data)


def test_texts_in_english() -> None:
    i18n.set_language("en")
    report = _check(CLONE_15)
    assert kind_text(report) == "clone, command set like v1.4"
    assert uds_text(report).startswith("likely yes")
    assert voltage_text(report) == "12.6 V"


# --- Kommandozeile -----------------------------------------------------------------


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    responses = dict(CLONE_15)
    monkeypatch.setattr(cli, "SerialTransport", lambda port, baud: FakeTransport(responses))
    return responses


def test_cli_adapter(adapter: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["adapter"]) == 0
    out, err = capsys.readouterr()
    assert err == ""
    assert "Adapter:        ELM327 v1.5\n" in out
    assert "Urteil:         Nachbau, Befehlssatz wie v1.4\n" in out
    assert "Bordspannung:   12,6 V\n" in out
    assert "Für UDS (v0.3): voraussichtlich ja" in out
    assert "  ATAMC: nein (seit v2.0: Aktivitätszähler)\n" in out
    assert "Hinweis: Geschlossen aus dem Befehlssatz" in out


def test_cli_adapter_json(adapter: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["adapter", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["identification"] == "ELM327 v1.5"
    assert data["command_set"] == "1.4"


def test_cli_adapter_english(adapter: dict[str, str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["adapter", "--lang", "en"]) == 0
    out = capsys.readouterr().out
    assert "Verdict:" in out
    assert "Commands checked:" in out


def test_cli_adapter_lost_connection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["adapter", "--port", "/dev/does-not-exist"]) == 1
    assert "Fehler:" in capsys.readouterr().err
