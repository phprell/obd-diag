# Sitzungen, Bericht und Mitschnitt

## Vollständige Diagnose

```sh
obd-diag diagnose --port /dev/ttyUSB0                   # Tabelle
obd-diag diagnose --port /dev/ttyUSB0 --save            # Sitzung speichern, Pfad auf stderr
obd-diag diagnose --port /dev/ttyUSB0 --pdf bericht.pdf --csv codes.csv
obd-diag diagnose --port /dev/ttyUSB0 --online-vin      # FIN zusätzlich bei NHTSA vPIC
```

`diagnose` liest in einem Durchgang, nur lesend:

1. Scan (Adapter, Protokoll, Bordspannung, Codes aus Mode 03/07/0A). Schlägt er fehl,
   bricht `diagnose` ab.
2. Readiness (Mode 01 PID 01): Motorkontrollleuchte, gemeldete Codezahl, Motorart
   (Otto/Diesel) und je Monitor „abgeschlossen“, „nicht abgeschlossen“ oder „nicht
   unterstützt“, dazu „Alle Tests abgeschlossen: ja/nein“.
3. Freeze Frame (Mode 02, Frame 00): auslösender Code, Last, Kühlmitteltemperatur,
   Drehzahl, Geschwindigkeit.
4. FIN (Mode 09 PID 02) mit Offline-Dekodierung ({doc}`../technik/fin`).

Antwortet das Fahrzeug auf einen der Teile 2 bis 4 nicht, fehlt nur dieser Teil.

:::{note}
„Alle Tests abgeschlossen“ ist bewusst **keine AU-Bewertung**. Seit 2018 gehört zur AU
bei allen OBD-Fahrzeugen wieder die Endrohrmessung; welche offenen Monitore toleriert
werden, hängt vom Fahrzeug und vom AU-Gerät ab.
:::

## Sitzungen

Eine Sitzung (Scan mit Klartexten, Readiness, Freeze Frame, FIN, Zeitpunkt) wird als
JSON unter `~/.local/share/obd-diag/sessions/session-JJJJMMTT-HHMMSS.json` gespeichert.
Vorhandene Dateien werden nie überschrieben (dann `…-2.json`). Die Datei trägt
`"format": "obd-diag-session"` und `"version": 1`; fremde oder neuere Formate lehnt das
Laden ab.

## PDF-Bericht und CSV

```sh
obd-diag export ~/.local/share/obd-diag/sessions/session-20261007-143205.json \
    --pdf bericht.pdf --csv fehlercodes.csv
```

- **PDF** (DIN A4, Deutsch): Fahrzeug, Adapter, Protokoll, Bordspannung,
  Kurzübersicht, Readiness, Fehlercodes mit Erklärung, Ursachen, Symptomen und
  Kostenrahmen, Freeze Frame. Schrift: DejaVu Sans, Liberation Sans oder Noto Sans,
  falls installiert (eingebettet), sonst Helvetica.
- **CSV**: eine Zeile pro Fehlercode (Code, Art, Titel, Beschreibung, Ursachen,
  Symptome, MIL, Abgasrelevant, Reparaturaufwand, Kosten, Datum, FIN). UTF-8 mit BOM
  und `;`, damit ein deutsches Excel sie per Doppelklick richtig öffnet.

## Mitschnitt (`--trace`)

Alle Befehle mit Adapter schneiden mit `--trace` jede gesendete und empfangene Zeile
mit Zeitstempel mit:

```sh
obd-diag diagnose --port /dev/ttyUSB0 --trace            # ~/.local/share/obd-diag/traces/
obd-diag diagnose --port /dev/ttyUSB0 --trace auto.log   # eigene Datei
```

```text
# obd-diag 0.0.1 Mitschnitt 2026-10-07T16:09:33+02:00 /dev/ttyUSB0 38400 Baud
    0.000 >> ATZ\r
    0.508 << ATZ\r\r\rELM327 v1.5\r\r>
    3.013 >> 03\r
    3.016 << 00A\r0: 430404200133\r1: 0300C100\r\r>
```

`>>` ist gesendet, `<<` empfangen, `!!` ein Fehler; Steuerzeichen stehen als `\r`
bzw. `\xNN`. Wie die Antwort in der letzten Zeile zu lesen ist, erklärt
{doc}`../technik/antwortformate`. In der Oberfläche schaltet „Optionen →
Adapter-Mitschnitt aufzeichnen“ das für jede Aktion ein. Der Mitschnitt enthält ggf. die
FIN und bleibt lokal.
