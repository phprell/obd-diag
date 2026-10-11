# Ohne Auto ausprobieren

Der Demo-Modus zeigt obd-diag mit einem eingebauten Beispielfahrzeug, ganz ohne Adapter
und ohne Auto: zum Kennenlernen, für Vorführungen und für Bildschirmfotos.

```sh
uv run obd-diag-gui --demo          # Oberfläche, Port „demo“ ist schon gewählt
uv run obd-diag diagnose --demo     # Kommandozeile; gleich wie --port demo
uv run obd-diag live --demo
```

In der Oberfläche steht das Demo-Fahrzeug außerdem immer als letzter Eintrag in der
Port-Liste („demo“). Es wird nie von selbst gewählt, auch wenn kein Adapter steckt.
Solange „demo“ gewählt ist, sagt ein blauer Hinweis über der Liste, dass die Daten nicht
vom eigenen Auto stammen. Die Kommandozeile gibt denselben Hinweis aus, und als Adapter
erscheint überall `ELM327 v1.5 (Demo)`, auch in gespeicherten Sitzungen, PDF und CSV.

```{image} ../_static/screenshots/demo-fehlercodes-light.png
:alt: Oberfläche im Demo-Modus: Port demo, Hinweis über der Liste, Code U1218
:class: only-light
```

```{image} ../_static/screenshots/demo-fehlercodes-dark.png
:alt: Oberfläche im Demo-Modus: Port demo, Hinweis über der Liste, Code U1218
:class: only-dark
```

## Woher die Daten stammen

Das Demo-Fahrzeug ist der Mercedes A 180 d (W177) aus den Tests am echten Auto
({doc}`erster-test`), mit laufendem Motor im Stand. Seine Antworten stammen aus den
Mitschnitten dieser Tests:

- vier Steuergeräte, CAN 29 Bit/500 kBit/s
- ein gespeicherter Code, U1218 (herstellerspezifisch), mit Freeze Frame
- Readiness eines Diesels, der Abgassensor-Test ist noch offen
- FIN `WDD1770031J000000` (Seriennummer geschwärzt wie in den Mitschnitten)
- Live-Daten im Leerlauf: elf aufgezeichnete Runden, die sich wiederholen

Nicht aufgezeichnet sind die Werte zu PID 21, 30, 31, 49 und 66 (Strecke mit
Warnleuchte, Warmläufe und Strecke seit dem Löschen, Fahrpedal, Luftmasse). Das Auto
meldet sie als unterstützt; im Demo-Modus stehen dort erfundene, plausible Werte. Der
Gebrauchtwagen-Check ist deshalb „unauffällig“ (40 Warmläufe und 3000 km seit dem Löschen).

```{image} ../_static/screenshots/demo-live-daten-light.png
:alt: Live-Daten im Demo-Modus: Drehzahl um 860/min, Kühlmittel 62 °C
:class: only-light
```

```{image} ../_static/screenshots/demo-live-daten-dark.png
:alt: Live-Daten im Demo-Modus: Drehzahl um 860/min, Kühlmittel 62 °C
:class: only-dark
```

## Was dabei passiert

Der Demo-Modus ersetzt nur den Adapter: Statt über USB antwortet ein simulierter ELM327
(`obd_diag.transport.demo`) Byte für Byte so, wie es der echte am W177 getan hat. Treiber,
Freigabeliste, Auswertung und Oberfläche laufen also genau wie am Auto, auch der
Mitschnitt mit `--trace`. Anfragen, für die es keine Daten gibt, beantwortet er mit
`NO DATA`.

Löschen geht im Demo-Modus nicht: Es ist ohnehin gesperrt, und selbst freigegeben würde
es an der Vorbedingung „Motor aus“ scheitern, denn der Motor des Demo-Fahrzeugs läuft.
