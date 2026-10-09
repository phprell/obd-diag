"""Realistische Beispiel-Sitzungen für Tests (Export, Speichern, Oberfläche).

``full_session`` setzt jede optionale Angabe, ``minimal_session`` keine. Die
Katalogtexte stammen aus OBDex (CC0) und sind hier eingefroren, damit die Tests ohne
gebauten Katalog laufen; mit ``catalog`` werden sie stattdessen nachgeschlagen.
"""

import dataclasses
from datetime import UTC, datetime, timedelta, timezone

from obd_diag.data.dtc_catalog import Cause, DtcInfo
from obd_diag.protocol.obd import FreezeFrame
from obd_diag.services.diagnostics import DiagnosticCode, DtcKind, DtcLookup, ScanResult
from obd_diag.services.dtc_online import OnlineExplanation
from obd_diag.services.readiness import Monitor, MonitorState, ReadinessStatus
from obd_diag.services.session import Session
from obd_diag.services.vehicle import VinInfo

CEST = timezone(timedelta(hours=2))
CREATED = datetime(2026, 10, 7, 14, 32, 5, tzinfo=CEST)

INFOS = {
    "P0420": DtcInfo(
        code="P0420",
        title="Katalysatorwirkungsgrad unter Schwellwert (Bank 1)",
        description=(
            "Die Sauerstoffspeicherfähigkeit des Hauptkatalysators auf Bank 1 liegt unter "
            "dem OBD-Grenzwert. Das Signal der nachgeschalteten Lambdasonde folgt "
            "zunehmend dem der vorgeschalteten Sonde, was auf reduzierte Wirkung hinweist."
        ),
        causes=(
            Cause("Katalysator gealtert oder vergiftet", "high"),
            Cause("Nachgeschaltete Lambdasonde driftet", "medium"),
            Cause("Abgasleck vor Katalysator", "low"),
        ),
        symptoms=("Motorkontrollleuchte an", "Abgasuntersuchung fehlgeschlagen"),
        mil=True,
        emissions_relevant=True,
        repair_difficulty="hard",
        cost_eur=(600, 2500),
    ),
    "P0133": DtcInfo(
        code="P0133",
        title="Lambdasonde langsames Ansprechverhalten (Bank 1, Sonde 1)",
        description=("Die vorgeschaltete Lambdasonde wechselt zu langsam zwischen fett und mager."),
        causes=(
            Cause("Lambdasonde gealtert", "high"),
            Cause("Sonde teilweise vergiftet (Silikon, Blei, Schwefel)", "medium"),
        ),
        symptoms=("Motorkontrollleuchte an", "Erhöhter Kraftstoffverbrauch"),
        mil=True,
        emissions_relevant=True,
        repair_difficulty="easy",
        cost_eur=(60, 250),
    ),
    "P0300": DtcInfo(
        code="P0300",
        title="Zufällige/mehrfache Zylinder-Verbrennungsaussetzer erkannt",
        description=(
            "Die Motorsteuerung hat Verbrennungsaussetzer erkannt, die sich nicht auf einen "
            "einzelnen Zylinder konzentrieren."
        ),
        causes=(
            Cause("Verschlissene Zündkerzen", "high"),
            Cause("Falschluft, die alle Zylinder betrifft", "high"),
            Cause("Kraftstoffdruck zu niedrig", "medium"),
        ),
        symptoms=("Motorkontrollleuchte an oder blinkt", "Rauer Leerlauf und unrunder Motorlauf"),
        mil=True,
        emissions_relevant=True,
        repair_difficulty="medium",
        cost_eur=(50, 1500),
    ),
}

# Otto-Motor nach einigen Fahrten: Katalysator- und Lambdasonden-Test noch offen.
READINESS = ReadinessStatus(
    mil_on=True,
    dtc_count=2,
    compression_ignition=False,
    monitors=(
        Monitor("misfire", "Verbrennungsaussetzer", MonitorState.COMPLETE),
        Monitor("fuel_system", "Kraftstoffsystem", MonitorState.COMPLETE),
        Monitor("components", "Komponenten", MonitorState.COMPLETE),
        Monitor("catalyst", "Katalysator", MonitorState.INCOMPLETE),
        Monitor("heated_catalyst", "Katalysatorheizung", MonitorState.NOT_SUPPORTED),
        Monitor("evap", "Tankentlüftung", MonitorState.COMPLETE),
        Monitor("secondary_air", "Sekundärluftsystem", MonitorState.NOT_SUPPORTED),
        Monitor("oxygen_sensor", "Lambdasonde", MonitorState.INCOMPLETE),
        Monitor("oxygen_sensor_heater", "Lambdasondenheizung", MonitorState.COMPLETE),
        Monitor("egr", "Abgasrückführung", MonitorState.NOT_SUPPORTED),
    ),
)

FREEZE_FRAME = FreezeFrame(
    dtc="P0300",
    raw={
        "020200": "4202000300",
        "020400": "42040058",
        "020500": "4205007F",
        "020C00": "420C002170",
        "020D00": "420D003F",
    },
    values={"engine_load_pct": 34.5, "coolant_temp_c": 87.0, "rpm": 2140.0, "speed_kmh": 63.0},
)

VEHICLE = VinInfo(
    vin="WVWZZZ1KZ6W123456",
    valid=True,
    checksum_ok=None,
    wmi="WVW",
    manufacturer="Volkswagen",
    country="Deutschland",
    model_year=2006,
    online={"Model": "Golf", "EngineCylinders": "4"},
)


def _info(code: str, catalog: DtcLookup | None) -> DtcInfo | None:
    return catalog.lookup(code, "de") if catalog is not None else INFOS.get(code)


def full_session(catalog: DtcLookup | None = None, *, voltage: float = 12.4) -> Session:
    """Alle Angaben gesetzt: zwei gespeicherte, ein ausstehender, ein permanenter Code
    (einer davon ohne Katalogeintrag), Readiness, Freeze Frame und FIN."""
    codes = [
        DiagnosticCode("P0300", DtcKind.STORED, _info("P0300", catalog)),
        DiagnosticCode("P0420", DtcKind.STORED, _info("P0420", catalog)),
        DiagnosticCode("P0133", DtcKind.PENDING, _info("P0133", catalog)),
        DiagnosticCode("P0420", DtcKind.PERMANENT, _info("P0420", catalog)),
        DiagnosticCode("P1234", DtcKind.PENDING, None),  # herstellerspezifisch, unbekannt
    ]
    scan = ScanResult(
        adapter="ELM327 v1.5", protocol="ISO 15765-4 (CAN 11/500)", voltage=voltage, codes=codes
    )
    return Session(
        created=CREATED,
        scan=scan,
        readiness=READINESS,
        freeze_frame=FREEZE_FRAME,
        vehicle=VEHICLE,
    )


def minimal_session() -> Session:
    """Nichts Optionales: keine Codes, keine Spannung, keine Readiness/FIN/Freeze Frame."""
    return Session(
        created=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        scan=ScanResult(adapter="ELM327 v2.1", protocol="ISO 9141-2", voltage=None),
    )


ONLINE_P1234 = OnlineExplanation(
    text="Camshaft Position Actuator Circuit",
    source="Wal33D/dtc-database (MIT), volkswagen_codes.txt",
    url="https://github.com/Wal33D/dtc-database/blob/abc/data/source-data/volkswagen_codes.txt#L7",
    manufacturer="Volkswagen",
)


def with_online(session: Session) -> Session:
    """``session`` mit Online-Erklärung zu P1234 (wie nach ``--online-codes``)."""
    codes = [
        dataclasses.replace(c, online=ONLINE_P1234) if c.code == "P1234" else c
        for c in session.scan.codes
    ]
    return dataclasses.replace(session, scan=dataclasses.replace(session.scan, codes=codes))
