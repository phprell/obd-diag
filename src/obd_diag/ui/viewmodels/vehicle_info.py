"""Steuergeräte (Mode 09) und Testergebnisse (Mode 06) einer Sitzung als Werte für QML."""

from typing import Any

from obd_diag.i18n import tr, trn
from obd_diag.protocol.monitor_tests import (
    STATUS_LABELS,
    TestResult,
    TestStatus,
    mid_name,
    tid_name,
)
from obd_diag.protocol.vehicle_info import EcuInfo
from obd_diag.ui.viewmodels.session_parts import number_text


def ecus_entry(ecus: tuple[EcuInfo, ...] | None) -> dict[str, Any]:
    """Je Steuergerät Name, Kalibrierungs-IDs, CVN und Überwachungszähler."""
    if ecus is None:
        return {"available": False, "ecus": []}
    entries = []
    for ecu in ecus:
        facts = [{"label": tr("Kalibrierungs-ID"), "value": c} for c in ecu.calibration_ids]
        facts += [{"label": tr("CVN"), "value": c} for c in ecu.cvns]
        counters: list[dict[str, Any]] = []
        counters_title = ""
        ipt = ecu.performance
        if ipt is not None:
            counters_title = tr(
                "Überwachungszähler: {cycles} Fahrzyklen, {ignitions} Zündungen"
            ).format(cycles=ipt.obd_conditions, ignitions=ipt.ignition_cycles)
            for counter in ipt.counters:
                ratio = counter.ratio
                counters.append(
                    {
                        "label": tr(counter.name),
                        "value": tr("{completed} von {conditions}").format(
                            completed=counter.completed, conditions=counter.conditions
                        ),
                        "ratio": -1.0 if ratio is None else ratio,
                        "ratioText": "" if ratio is None else number_text(ratio, 2),
                    }
                )
        entries.append(
            {
                "address": ecu.ecu,
                "name": ecu.name or tr("Steuergerät {ecu}").format(ecu=ecu.ecu),
                "facts": facts,
                "countersTitle": counters_title,
                "counters": counters,
            }
        )
    return {"available": True, "ecus": entries}


def _measure(test: TestResult, value: float | None, raw: int) -> str:
    if value is None:
        return tr("{raw} (Rohwert)").format(raw=raw)
    text = number_text(value, test.decimals)
    return f"{text} {test.unit}" if test.unit else text


def results_entry(
    tests: tuple[TestResult, ...] | None, ecus: tuple[EcuInfo, ...] | None = None
) -> dict[str, Any]:
    """Testergebnisse nach Steuergerät gruppiert, mit Zusammenfassung."""
    if tests is None:
        return {"available": False, "count": 0, "failedCount": 0, "summary": "", "groups": []}
    names = {e.ecu: e.name for e in ecus or () if e.name}
    failed = sum(t.status is TestStatus.FAILED for t in tests)
    no_result = sum(t.status is TestStatus.NO_RESULT for t in tests)
    if not tests:
        summary = tr("Das Fahrzeug meldet Mode 06, aber keine Testergebnisse.")
    elif failed:
        summary = trn(
            "{n} Test außerhalb der Grenzwerte.", "{n} Tests außerhalb der Grenzwerte.", failed
        ).format(n=failed)
    else:
        summary = tr("Alle Tests mit Ergebnis liegen innerhalb der Grenzwerte.")
    groups: list[dict[str, Any]] = []
    for test in tests:
        if not groups or groups[-1]["address"] != test.ecu:
            name = names.get(test.ecu, "")
            groups.append(
                {
                    "address": test.ecu,
                    "title": f"{name} ({test.ecu})"
                    if name
                    else tr("Steuergerät {ecu}").format(ecu=test.ecu),
                    "rows": [],
                }
            )
        shown = test.status is not TestStatus.NO_RESULT
        groups[-1]["rows"].append(
            {
                "monitor": mid_name(test.mid),
                "test": tid_name(test.tid),
                "value": _measure(test, test.value, test.value_raw) if shown else "",
                "minimum": _measure(test, test.minimum, test.min_raw) if shown else "",
                "maximum": _measure(test, test.maximum, test.max_raw) if shown else "",
                "limits": tr("Grenzen {low} bis {high}").format(
                    low=_measure(test, test.minimum, test.min_raw),
                    high=_measure(test, test.maximum, test.max_raw),
                )
                if shown
                else "",
                "unit": test.unit,
                "status": test.status.value,
                "statusLabel": tr(STATUS_LABELS[test.status]),
            }
        )
    return {
        "available": True,
        "count": len(tests),
        "failedCount": failed,
        "noResultCount": no_result,
        "summary": summary,
        "groups": groups,
    }
