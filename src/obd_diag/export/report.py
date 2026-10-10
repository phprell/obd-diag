"""PDF-Bericht und CSV einer Diagnosesitzung in der eingestellten Sprache (``i18n``).

CSV: eine Zeile pro Fehlercode, UTF-8 mit BOM; Trennzeichen auf Deutsch ``;``, auf
Englisch ``,``. So öffnet Excel die Datei per Doppelklick richtig (Umlaute, Spalten);
LibreOffice erkennt beides ebenfalls.

PDF: DIN A4 mit ReportLab (BSD-Lizenz). Schrift: eine eingebettete TrueType-Schrift
des Systems (DejaVu Sans, Liberation Sans oder Noto Sans), damit der Bericht überall
gleich aussieht; fehlen alle, Helvetica aus dem PDF-Standardumfang (WinAnsi, enthält
Umlaute, € und Gedankenstrich, wird aber nicht eingebettet).
"""

import csv
import functools
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from obd_diag import __version__
from obd_diag.data.dtc_catalog import DtcInfo
from obd_diag.i18n import N_, csv_delimiter, language, tr
from obd_diag.services.diagnostics import LOW_VOLTAGE, DiagnosticCode, DtcKind
from obd_diag.services.readiness import ALL_COMPLETE_LABEL, AU_NOTE, MonitorState, ReadinessStatus
from obd_diag.services.session import Session
from obd_diag.services.vehicle import VPIC_FIELDS, checksum_text, model_year_text

# Bezeichnungen deutsch (Quelltexte), bei der Ausgabe mit ``tr`` übersetzt
KIND_LABELS = {
    DtcKind.STORED: N_("Gespeichert"),
    DtcKind.PENDING: N_("Ausstehend"),
    DtcKind.PERMANENT: N_("Permanent"),
}
_KIND_HEADINGS = {
    DtcKind.STORED: (N_("Gespeicherte Fehlercodes"), N_("Mode 03: bestätigte Fehler")),
    DtcKind.PENDING: (
        N_("Ausstehende Fehlercodes"),
        N_("Mode 07: im aktuellen Fahrzyklus erkannt"),
    ),
    DtcKind.PERMANENT: (
        N_("Permanente Fehlercodes"),
        N_("Mode 0A: lassen sich nicht löschen, das Steuergerät entfernt sie selbst"),
    ),
}
LIKELIHOOD_LABELS = {"high": N_("hoch"), "medium": N_("mittel"), "low": N_("niedrig")}
DIFFICULTY_LABELS = {
    "easy": N_("einfach"),
    "medium": N_("mittel"),
    "hard": N_("schwer"),
    "shop_only": N_("nur Werkstatt"),
}
MONITOR_STATE_LABELS = {
    MonitorState.COMPLETE: N_("abgeschlossen"),
    MonitorState.INCOMPLETE: N_("nicht abgeschlossen"),
    MonitorState.NOT_SUPPORTED: N_("nicht unterstützt"),
}
# Schlüssel aus FreezeFrame.values: Bezeichnung, Einheit, Nachkommastellen
FREEZE_LABELS: dict[str, tuple[str, str, int]] = {
    "engine_load_pct": (N_("Motorlast"), "%", 1),
    "coolant_temp_c": (N_("Kühlmitteltemperatur"), "°C", 0),
    "rpm": (N_("Drehzahl"), N_("1/min"), 0),
    "speed_kmh": (N_("Geschwindigkeit"), "km/h", 0),
}
SOURCE_NOTE = N_("Fehlercode-Texte: OBDex (CC0)")

# --- Zahlen und Texte in der eingestellten Sprache ---


def _label(labels: dict[str, str], key: str) -> str:
    """Übersetzte Bezeichnung zu ``key``, sonst ``key`` selbst."""
    return tr(labels[key]) if key in labels else key


def _number(value: float, decimals: int = 0) -> str:
    """Deutsch mit Tausenderpunkt und Dezimalkomma, Englisch mit Komma und Punkt."""
    text = f"{value:,.{decimals}f}"
    if language() != "de":
        return text
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".")


def _cost(cost: tuple[int, int] | None) -> str:
    if cost is None:
        return ""
    low, high = cost
    if low == high:
        return f"{_number(low)} €"
    return f"{_number(low)}–{_number(high)} €"  # noqa: RUF001


def _yes_no(value: bool | None) -> str:
    return "" if value is None else (tr("ja") if value else tr("nein"))


def _voltage(value: float | None) -> str:
    return tr("unbekannt") if value is None else f"{_number(value, 1)} V"


def _freeze_value(key: str, value: float) -> tuple[str, str]:
    label, unit, decimals = FREEZE_LABELS.get(key, (key, "", 2))
    shown = _number(value, decimals)
    return tr(label), f"{shown} {tr(unit)}" if unit else shown


# --- CSV ---

# Kopfzeile (deutsch; ``csv_columns`` übersetzt)
CSV_COLUMNS = (
    N_("Code"),
    N_("Art"),
    N_("Titel"),
    N_("Beschreibung"),
    N_("Ursachen"),
    N_("Symptome"),
    "MIL",
    N_("Abgasrelevant"),
    N_("Reparaturaufwand"),
    N_("Kosten"),
    N_("Kosten von (EUR)"),
    N_("Kosten bis (EUR)"),
    N_("Datum"),
    N_("FIN"),
)


def csv_columns() -> list[str]:
    """Kopfzeile der CSV in der eingestellten Sprache."""
    return [tr(column) for column in CSV_COLUMNS]


def _csv_row(code: DiagnosticCode, created: str, vin: str) -> list[str]:
    info = code.info
    if info is None:
        texts = ["", "", "", "", "", "", "", "", "", ""]
    else:
        causes = " | ".join(
            f"{c.label} ({_label(LIKELIHOOD_LABELS, c.likelihood)})" for c in info.causes
        )
        low, high = ("", "") if info.cost_eur is None else map(str, info.cost_eur)
        difficulty = info.repair_difficulty or ""
        texts = [
            info.title,
            info.description or "",
            causes,
            " | ".join(info.symptoms),
            _yes_no(info.mil),
            _yes_no(info.emissions_relevant),
            _label(DIFFICULTY_LABELS, difficulty),
            _cost(info.cost_eur),
            low,
            high,
        ]
    return [code.code, tr(KIND_LABELS[code.kind]), *texts, created, vin]


def export_csv(session: Session, path: Path) -> None:
    """Eine Zeile pro Fehlercode (Code, Art, Titel, ...), UTF-8 mit BOM für Excel.

    Trennzeichen ``;`` (Deutsch) bzw. ``,`` (Englisch), Zeilenende CRLF. Mehrere
    Ursachen/Symptome stehen durch `` | `` getrennt in einer Zelle, die
    Wahrscheinlichkeit in Klammern. „Datum“ und „FIN“ wiederholen sich in jeder Zeile,
    damit sich CSVs mehrerer Sitzungen aneinanderhängen lassen. Ohne Fehlercodes
    enthält die Datei nur die Kopfzeile.
    """
    created = session.created.isoformat(timespec="seconds")
    vin = session.vehicle.vin if session.vehicle is not None else ""
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=csv_delimiter(), lineterminator="\r\n")
        writer.writerow(csv_columns())
        for code in session.scan.codes:
            writer.writerow(_csv_row(code, created, vin))


# --- Schrift ---

_FONT_CANDIDATES = (
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf"),
    ("NotoSans-Regular.ttf", "NotoSans-Bold.ttf"),
)
# Ersatz für Zeichen, die der eingebetteten Schrift fehlen
_FALLBACK_CHARS = {
    "–": "-",  # noqa: RUF001
    "—": "-",
    "€": "EUR",
    "„": '"',
    "“": '"',
    "”": '"',
    "‚": "'",  # noqa: RUF001
    "‘": "'",  # noqa: RUF001
    "’": "'",  # noqa: RUF001
    "…": "...",
    "≥": ">=",
    "≤": "<=",
    "→": "->",
}


def _font_dirs() -> list[Path]:
    home = Path.home()
    data_home = os.environ.get("XDG_DATA_HOME", "")
    user = Path(data_home) if os.path.isabs(data_home) else home / ".local" / "share"
    return [
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        user / "fonts",
        home / ".fonts",
    ]


def find_font_files() -> tuple[Path, Path] | None:
    """Normal- und Fettschnitt einer passenden TrueType-Schrift, sonst ``None``."""
    wanted = {name for pair in _FONT_CANDIDATES for name in pair}
    found: dict[str, Path] = {}
    for directory in _font_dirs():
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.ttf"):
            if path.name in wanted:
                found.setdefault(path.name, path)
    for regular, bold in _FONT_CANDIDATES:
        if regular in found and bold in found:
            return found[regular], found[bold]
    return None


@dataclass(frozen=True)
class _Fonts:
    regular: str
    bold: str
    clean: Callable[[str], str]  # ersetzt Zeichen, die die Schrift nicht kennt


@functools.cache
def _register_ttf(regular: Path, bold: Path) -> _Fonts | None:
    try:
        reg_font = TTFont("ObdDiagSans", str(regular))
        pdfmetrics.registerFont(reg_font)
        pdfmetrics.registerFont(TTFont("ObdDiagSans-Bold", str(bold)))
    except Exception:  # kaputte oder nicht unterstützte Schriftdatei: Helvetica nehmen
        return None
    glyphs = set(reg_font.face.charToGlyph)

    def clean(text: str) -> str:
        if all(ord(ch) in glyphs for ch in text):
            return text
        return "".join(ch if ord(ch) in glyphs else _FALLBACK_CHARS.get(ch, "?") for ch in text)

    return _Fonts("ObdDiagSans", "ObdDiagSans-Bold", clean)


def _fonts() -> _Fonts:
    files = find_font_files()
    fonts = _register_ttf(*files) if files is not None else None
    # Helvetica: ReportLab bildet Zeichen außerhalb von WinAnsi selbst ab.
    return fonts or _Fonts("Helvetica", "Helvetica-Bold", lambda text: text)


# --- PDF ---

_INK = colors.HexColor("#1F2933")
_MUTED = colors.HexColor("#616E7C")
_ACCENT = colors.HexColor("#1F4E79")
_RULE = colors.HexColor("#CBD2D9")
_HEAD_BG = colors.HexColor("#EEF2F6")
_ZEBRA = colors.HexColor("#F7F9FB")
_GOOD = colors.HexColor("#067647")
_WARN = colors.HexColor("#B54708")
_BAD = colors.HexColor("#B42318")
_BAD_BG = colors.HexColor("#FEF3F2")
_KIND_COLORS = {DtcKind.STORED: _BAD, DtcKind.PENDING: _WARN, DtcKind.PERMANENT: _ACCENT}
_STATE_COLORS = {
    MonitorState.COMPLETE: _GOOD,
    MonitorState.INCOMPLETE: _BAD,
    MonitorState.NOT_SUPPORTED: _MUTED,
}

_MARGIN_X = 20 * mm
_MARGIN_TOP = 18 * mm
_MARGIN_BOTTOM = 24 * mm
_WIDTH = A4[0] - 2 * _MARGIN_X
_BOX_PAD = 9  # Einzug der Fehlercode-Kästen hinter dem farbigen Rand
_INNER = _WIDTH - _BOX_PAD
_TABLE_WIDTH = 135 * mm  # Messwert- und Monitortabellen
_LIKELIHOOD_COLORS = {"high": _BAD, "medium": _WARN, "low": _MUTED}


def _hex(color: colors.Color) -> str:
    return "#" + color.hexval()[2:]


class _Builder:
    """Baut die Flowables des Berichts; alle Texte laufen durch ``_p`` (escaped)."""

    def __init__(self, fonts: _Fonts) -> None:
        self.fonts = fonts
        base = ParagraphStyle(
            "base", fontName=fonts.regular, fontSize=9.5, leading=13, textColor=_INK
        )
        self.styles = {
            "base": base,
            "title": ParagraphStyle(
                "title", base, fontName=fonts.bold, fontSize=20, leading=24, textColor=_ACCENT
            ),
            "subtitle": ParagraphStyle("subtitle", base, textColor=_MUTED, fontSize=10),
            "h1": ParagraphStyle(
                "h1",
                base,
                fontName=fonts.bold,
                fontSize=13,
                leading=16,
                textColor=_ACCENT,
                spaceBefore=12,
                spaceAfter=5,
            ),
            "h2": ParagraphStyle(
                "h2", base, fontName=fonts.bold, fontSize=11, leading=14, spaceBefore=8
            ),
            "note": ParagraphStyle("note", base, fontSize=8.5, leading=11, textColor=_MUTED),
            "label": ParagraphStyle("label", base, textColor=_MUTED),
            "bold": ParagraphStyle("bold", base, fontName=fonts.bold),
            "code": ParagraphStyle(
                "code", base, fontName=fonts.bold, fontSize=11, leading=14, textColor=_INK
            ),
            "small": ParagraphStyle("small", base, fontSize=8.5, leading=11.5),
            "bullet": ParagraphStyle(
                "bullet", base, fontSize=8.5, leading=11.5, leftIndent=8, bulletIndent=0
            ),
            "right": ParagraphStyle("right", base, alignment=TA_RIGHT),
        }

    def _p(self, text: str, style: str = "base", *, markup: str = "") -> Paragraph:
        """Absatz aus Klartext; ``markup`` ist bereits gültiges ReportLab-Markup."""
        return Paragraph(markup + escape(self.fonts.clean(text)), self.styles[style])

    def _colored(
        self, text: str, color: colors.Color, *, bold: bool = False, style: str = "base"
    ) -> Paragraph:
        font = self.fonts.bold if bold else self.fonts.regular
        markup = (
            f'<font name="{font}" color="{_hex(color)}">{escape(self.fonts.clean(text))}</font>'
        )
        return Paragraph(markup, self.styles[style])

    def _kv_table(self, rows: list[tuple[str, Any]], label_width: float) -> Table:
        cells = [
            [self._p(label, "label"), value if isinstance(value, Flowable) else self._p(value)]
            for label, value in rows
        ]
        table = Table(cells, colWidths=[label_width, None], hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                ]
            )
        )
        return table

    def _grid(self, rows: list[list[Paragraph]], widths: list[float]) -> Table:
        """Tabelle mit Kopfzeile und abwechselnd hinterlegten Zeilen."""
        table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
        style: list[Any] = [
            ("BACKGROUND", (0, 0), (-1, 0), _HEAD_BG),
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, _RULE),
            ("LINEBELOW", (0, -1), (-1, -1), 0.8, _RULE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        style += [("BACKGROUND", (0, i), (-1, i), _ZEBRA) for i in range(2, len(rows), 2)]
        table.setStyle(TableStyle(style))
        return table

    # -- Abschnitte --

    def header(self, session: Session) -> list[Flowable]:
        created = session.created
        when = created.strftime(tr("%d.%m.%Y, %H:%M Uhr"))
        rule = Table([[""]], colWidths=[_WIDTH], rowHeights=[2])
        rule.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 1.5, _ACCENT)]))
        return [
            self._p(tr("OBD-Diagnosebericht"), "title"),
            Spacer(1, 2),
            self._p(tr("Ausgelesen am {when}").format(when=when), "subtitle"),
            Spacer(1, 6),
            rule,
        ]

    def vehicle_and_connection(self, session: Session) -> list[Flowable]:
        v = session.vehicle
        vehicle: list[Flowable] = [self._p(tr("Fahrzeug"), "h1")]
        if v is None:
            vehicle.append(
                self._p(
                    tr("Fahrzeug-Identifikation (FIN) nicht verfügbar."),
                    "note",
                )
            )
        else:
            unknown = tr("unbekannt")
            rows: list[tuple[str, Any]] = [(tr("FIN"), self._p(v.vin, "bold"))]
            if not v.valid:
                rows.append(("", self._colored(tr("FIN ungültig (Länge oder Zeichen)"), _BAD)))
            elif v.checksum_ok is False:
                rows.append((tr("Prüfziffer"), self._colored(checksum_text(v), _WARN)))
            else:
                rows.append((tr("Prüfziffer"), checksum_text(v)))
            rows.append((tr("Hersteller"), tr(v.manufacturer) if v.manufacturer else unknown))
            rows.append((tr("Land"), tr(v.country) if v.country else unknown))
            year = model_year_text(v)
            if year is not None:
                rows.append((tr("Modelljahr"), year))
            for key in ("Model", "EngineCylinders", "DisplacementL", "FuelTypePrimary"):
                if key in v.online:
                    rows.append((tr(VPIC_FIELDS[key]), v.online[key]))
            vehicle.append(self._kv_table(rows, 26 * mm))

        scan = session.scan
        voltage: Any = _voltage(scan.voltage)
        if scan.low_voltage:
            voltage = self._colored(
                tr("{voltage} (niedrig)").format(voltage=voltage), _BAD, bold=True
            )
        connection: list[Flowable] = [
            self._p(tr("Verbindung"), "h1"),
            self._kv_table(
                [
                    (tr("Adapter"), scan.adapter),
                    (tr("Protokoll"), scan.protocol),
                    (tr("Bordspannung"), voltage),
                ],
                28 * mm,
            ),
        ]
        half = _WIDTH / 2
        outer = Table([[vehicle, connection]], colWidths=[half, half])
        outer.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (0, -1), 8),
                    ("RIGHTPADDING", (1, 0), (1, -1), 0),
                ]
            )
        )
        result: list[Flowable] = [outer]
        if scan.low_voltage:
            result += [Spacer(1, 8), self.low_voltage_box()]
        return result

    def low_voltage_box(self) -> Table:
        text = tr(
            "Bordspannung unter {voltage} V: Ergebnisse können "
            "unzuverlässig sein. Batterie laden oder Ladegerät anschließen."
        ).format(voltage=_number(LOW_VOLTAGE, 1))
        warning = escape(tr("Warnung:"))
        box = Table(
            [[self._p(text, markup=f'<font name="{self.fonts.bold}">{warning} </font>')]],
            colWidths=[_WIDTH],
        )
        box.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), _BAD_BG),
                    ("LINEBEFORE", (0, 0), (0, -1), 3, _BAD),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        return box

    def summary(self, session: Session) -> list[Flowable]:
        counts = {kind: 0 for kind in DtcKind}
        for c in session.scan.codes:
            counts[c.kind] += 1
        r = session.readiness
        cells: list[tuple[str, Paragraph]] = [
            (tr(KIND_LABELS[kind]), self._colored(str(counts[kind]), _INK, bold=True))
            for kind in DtcKind
        ]
        if r is not None:
            mil = (tr("an"), _BAD) if r.mil_on else (tr("aus"), _GOOD)
            cells.append((tr("Kontrollleuchte"), self._colored(*mil, bold=True)))
            complete = (tr("ja"), _GOOD) if r.all_complete else (tr("nein"), _WARN)
            cells.append((tr(ALL_COMPLETE_LABEL), self._colored(*complete, bold=True)))
        width = _WIDTH / len(cells)
        table = Table(
            [[self._p(label, "note") for label, _ in cells], [value for _, value in cells]],
            colWidths=[width] * len(cells),
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), _HEAD_BG),
                    ("LINEAFTER", (0, 0), (-2, -1), 0.8, colors.white),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, 0), 6),
                    ("BOTTOMPADDING", (0, -1), (-1, -1), 6),
                ]
            )
        )
        return [Spacer(1, 12), table]

    def readiness(self, readiness: ReadinessStatus | None) -> list[Flowable]:
        out: list[Flowable] = [self._p(tr("Readiness (Eigendiagnosen Abgassystem)"), "h1")]
        if readiness is None:
            out.append(self._p(tr("Readiness-Status nicht verfügbar."), "note"))
            return out
        engine = (
            tr("Diesel (Monitore für Diesel-Motoren)")
            if readiness.compression_ignition
            else tr("Otto (Monitore für Otto-Motoren)")
        )
        open_count = sum(m.state is MonitorState.INCOMPLETE for m in readiness.monitors)
        ready = (
            self._colored(tr("ja"), _GOOD, bold=True)
            if readiness.all_complete
            else self._colored(tr("nein, {count} offen").format(count=open_count), _WARN, bold=True)
        )
        out.append(
            self._kv_table(
                [
                    (tr(ALL_COMPLETE_LABEL), ready),
                    (tr("Kontrollleuchte (MIL)"), tr("an") if readiness.mil_on else tr("aus")),
                    (tr("Gemeldete Fehlercodes"), str(readiness.dtc_count)),
                    (tr("Motorart"), engine),
                ],
                48 * mm,
            )
        )
        out.append(Spacer(1, 6))
        rows = [[self._p(tr("Monitor"), "bold"), self._p(tr("Status"), "bold")]]
        rows += [
            [
                self._p(tr(m.name)),
                self._colored(tr(MONITOR_STATE_LABELS[m.state]), _STATE_COLORS[m.state]),
            ]
            for m in readiness.monitors
        ]
        out.append(self._grid(rows, [_TABLE_WIDTH - 45 * mm, 45 * mm]))
        out.append(Spacer(1, 4))
        out.append(
            self._p(
                tr(
                    "Nach dem Löschen von Fehlercodes stehen die Monitore wieder auf „nicht "
                    "abgeschlossen“; sie schließen erst nach mehreren Fahrten ab."
                )
                + " "
                + tr(AU_NOTE),
                "note",
            )
        )
        return out

    def _list_column(self, heading: str, items: list[Flowable]) -> list[Flowable]:
        return [self._p(heading, "label"), Spacer(1, 1), *items]

    def _causes(self, info: DtcInfo, width: float) -> Table:
        """Ursachen mit Wahrscheinlichkeit als eigene, bündige Spalte."""
        rows = [
            [
                self._colored(
                    _label(LIKELIHOOD_LABELS, c.likelihood),
                    _LIKELIHOOD_COLORS.get(c.likelihood, _MUTED),
                    style="small",
                ),
                self._p(c.label, "small"),
            ]
            for c in info.causes
        ]
        table = Table(rows, colWidths=[13 * mm, width - 13 * mm], hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        return table

    def code_block(self, code: DiagnosticCode, seen: dict[str, DtcKind]) -> Table:
        """Ein Code als Kasten mit farbigem Rand (Farbe nach Art)."""
        info = code.info
        color = _hex(_KIND_COLORS[code.kind])
        header = self._p(
            info.title if info is not None else tr("Keine Beschreibung im Katalog"),
            "code",
            markup=f'<font color="{color}">{escape(code.code)}</font>&nbsp;&nbsp;&nbsp;',
        )
        body: list[Flowable] = [header]
        earlier = seen.get(code.code)
        if earlier is not None and info is not None:
            body.append(
                self._p(
                    tr("Erklärung siehe oben unter „{kind}“.").format(
                        kind=tr(KIND_LABELS[earlier])
                    ),
                    "note",
                )
            )
        elif info is not None:
            body += self._details(info)
        else:
            if code.online is not None and earlier is None:
                body.append(
                    self._p(
                        code.online.text,
                        markup=f"<b>{escape(tr('Online-Erklärung (ungeprüft):'))}</b> ",
                    )
                )
                source = tr("Quelle: {source}, {url}").format(
                    source=code.online.source, url=code.online.url
                )
                body.append(self._p(source, "note"))
            body.append(
                self._p(
                    tr(
                        "Vermutlich herstellerspezifischer Code; Bedeutung in den "
                        "Unterlagen des Herstellers nachschlagen."
                    ),
                    "note",
                )
            )
        seen.setdefault(code.code, code.kind)
        box = Table([[body]], colWidths=[_WIDTH])
        box.setStyle(
            TableStyle(
                [
                    ("LINEBEFORE", (0, 0), (0, -1), 2.5, _KIND_COLORS[code.kind]),
                    ("LEFTPADDING", (0, 0), (-1, -1), _BOX_PAD),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 1),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        return box

    def _details(self, info: DtcInfo) -> list[Flowable]:
        out: list[Flowable] = []
        if info.description:
            out += [Spacer(1, 2), self._p(info.description)]
        facts: list[tuple[str, str]] = []
        if info.mil is not None:
            facts.append((tr("Kontrollleuchte"), _yes_no(info.mil)))
        if info.emissions_relevant is not None:
            facts.append((tr("Abgasrelevant"), _yes_no(info.emissions_relevant)))
        if info.repair_difficulty:
            label = _label(DIFFICULTY_LABELS, info.repair_difficulty)
            facts.append((tr("Reparaturaufwand"), label))
        if info.cost_eur is not None:
            facts.append((tr("Kosten (Richtwert)"), _cost(info.cost_eur)))
        if facts:
            table = Table(
                [[self._p(k, "note") for k, _ in facts], [self._p(v, "bold") for _, v in facts]],
                colWidths=[_INNER / 4] * len(facts),
                hAlign="LEFT",
            )
            table.setStyle(
                TableStyle(
                    [
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                        ("TOPPADDING", (0, 0), (-1, -1), 0),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                    ]
                )
            )
            out += [Spacer(1, 5), table]
        gap = 12
        half = (_INNER - gap) / 2
        columns: list[list[Flowable]] = []
        if info.causes:
            columns.append(
                self._list_column(
                    tr("Mögliche Ursachen (Wahrscheinlichkeit)"), [self._causes(info, half)]
                )
            )
        if info.symptoms:
            bullets: list[Flowable] = [
                Paragraph(escape(self.fonts.clean(s)), self.styles["bullet"], bulletText="•")
                for s in info.symptoms
            ]
            columns.append(self._list_column(tr("Symptome"), bullets))
        if columns:
            lists = Table([columns], colWidths=[half + gap, half][: len(columns)], hAlign="LEFT")
            lists.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (0, -1), gap),
                        ("RIGHTPADDING", (1, 0), (-1, -1), 0),
                        ("TOPPADDING", (0, 0), (-1, -1), 0),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                    ]
                )
            )
            out += [Spacer(1, 6), lists]
        return out

    def codes(self, session: Session) -> list[Flowable]:
        title = self._p(tr("Fehlercodes"), "h1")
        if not session.scan.codes:
            return [title, self._p(tr("Keine Fehlercodes gespeichert."))]
        out: list[Flowable] = []
        pending_title: list[Flowable] = [title]  # bleibt beim ersten Code
        seen: dict[str, DtcKind] = {}
        for kind in DtcKind:
            codes = [c for c in session.scan.codes if c.kind is kind]
            if not codes:
                continue
            heading, explanation = _KIND_HEADINGS[kind]
            # Überschrift nie allein am Seitenende: mit dem ersten Code zusammenhalten
            intro: list[Flowable] = [
                *pending_title,
                self._p(f"{tr(heading)} ({len(codes)})", "h2"),
                self._p(tr(explanation), "note"),
                Spacer(1, 6),
            ]
            for i, code in enumerate(codes):
                block = [self.code_block(code, seen), Spacer(1, 10)]
                out.append(KeepTogether(intro + block if i == 0 else block))
            pending_title = []
        return out

    def freeze_frame(self, session: Session) -> list[Flowable]:
        out: list[Flowable] = [self._p("Freeze Frame", "h1")]
        ff = session.freeze_frame
        if ff is None or (ff.dtc is None and not ff.values):
            out.append(self._p(tr("Freeze Frame nicht verfügbar."), "note"))
            return out
        out.append(
            self._p(
                tr(
                    "Betriebszustand des Motors in dem Moment, in dem der Fehlercode gesetzt wurde."
                ),
                "note",
            )
        )
        out.append(Spacer(1, 4))
        if ff.dtc is not None:
            titles = {c.code: c.info.title for c in session.scan.codes if c.info is not None}
            trigger = ff.dtc + (f" – {titles[ff.dtc]}" if ff.dtc in titles else "")  # noqa: RUF001
            out.append(self._kv_table([(tr("Ausgelöst durch"), self._p(trigger, "bold"))], 40 * mm))
            out.append(Spacer(1, 4))
        if ff.values:
            rows = [
                [
                    self._p(tr("Messwert"), "bold"),
                    Paragraph(
                        escape(tr("Wert")),
                        ParagraphStyle("wert", self.styles["right"], fontName=self.fonts.bold),
                    ),
                ]
            ]
            order = list(FREEZE_LABELS)
            keys = sorted(ff.values, key=lambda k: (order.index(k) if k in order else 99, k))
            for key in keys:
                label, value = _freeze_value(key, ff.values[key])
                rows.append([self._p(label), self._p(value, "right")])
            out.append(self._grid(rows, [_TABLE_WIDTH - 45 * mm, 45 * mm]))
        return out


class _NumberedCanvas(Canvas):
    """Zeichnet die Fußzeile erst am Ende, damit „Seite x von y“ möglich ist."""

    def __init__(self, *args: Any, fonts: _Fonts, **kwargs: Any) -> None:
        self.fonts = fonts
        kwargs.setdefault("initialFontName", fonts.regular)
        super().__init__(*args, **kwargs)
        self._pages: list[dict[str, Any]] = []

    def showPage(self) -> None:  # ReportLab-API
        self._pages.append(dict(self.__dict__))
        self._startPage()  # type: ignore[attr-defined]

    def save(self) -> None:
        total = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            self._footer(total)
            super().showPage()
        super().save()

    def _footer(self, total: int) -> None:
        y = 12 * mm
        self.saveState()
        self.setStrokeColor(_RULE)
        self.setLineWidth(0.6)
        self.line(_MARGIN_X, y + 4 * mm, A4[0] - _MARGIN_X, y + 4 * mm)
        self.setFont(self.fonts.regular, 7.5)
        self.setFillColor(_MUTED)
        footer = tr("Erstellt mit obd-diag {version}; {source}").format(
            version=__version__, source=tr(SOURCE_NOTE)
        )
        self.drawString(_MARGIN_X, y, self.fonts.clean(footer))
        page = tr("Seite {page} von {total}").format(page=self.getPageNumber(), total=total)
        self.drawRightString(A4[0] - _MARGIN_X, y, page)
        self.restoreState()


def export_pdf(session: Session, path: Path) -> None:
    """Bericht: Fahrzeug, Adapter/Protokoll/Spannung, Fehlercodes mit Erklärung,
    Freeze Frame, Readiness. In der eingestellten Sprache, DIN A4.

    Reihenfolge: Kopf, Fahrzeug und Verbindung, Kurzübersicht, Readiness,
    Fehlercodes nach Art, Freeze Frame. Fehlende Angaben stehen als „nicht verfügbar“.
    """
    fonts = _fonts()
    b = _Builder(fonts)
    story: list[Flowable] = [
        *b.header(session),
        *b.vehicle_and_connection(session),
        *b.summary(session),
        # Readiness und Freeze Frame sind kürzer als eine Seite: nie mitten darin umbrechen
        KeepTogether(b.readiness(session.readiness)),
        *b.codes(session),
        KeepTogether(b.freeze_frame(session)),
    ]
    doc = BaseDocTemplate(
        str(path),
        pagesize=A4,
        leftMargin=_MARGIN_X,
        rightMargin=_MARGIN_X,
        topMargin=_MARGIN_TOP,
        bottomMargin=_MARGIN_BOTTOM,
        title=tr("OBD-Diagnosebericht"),
        author=f"obd-diag {__version__}",
        subject=session.vehicle.vin if session.vehicle is not None else "",
        creator=f"obd-diag {__version__}",
        lang="de-DE" if language() == "de" else "en",
    )
    # Rahmen ohne Innenabstand: Tabellen mit Breite _WIDTH schließen bündig mit Text ab.
    frame = Frame(
        _MARGIN_X,
        _MARGIN_BOTTOM,
        _WIDTH,
        A4[1] - _MARGIN_TOP - _MARGIN_BOTTOM,
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
    )
    doc.addPageTemplates([PageTemplate(id="seite", frames=[frame])])
    doc.build(story, canvasmaker=functools.partial(_NumberedCanvas, fonts=fonts))
