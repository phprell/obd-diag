# Fehlercodes online erklären

Der Offline-Katalog ({doc}`fehlercodes`) kennt die genormten Codes (SAE J2012), aber
kaum herstellerspezifische wie `P1xxx` oder `U1xxx`. Für solche Codes kann obd-diag
auf Wunsch eine kurze Erklärung aus dem Netz holen. Das ist **standardmäßig aus**.

```sh
obd-diag diagnose --port /dev/ttyUSB0 --online-codes
obd-diag scan --port /dev/ttyUSB0 --online-codes
```

In der Oberfläche: Menü *Optionen* → „Fehlercodes online erklären“. Die Einstellung
wird je Nutzer gespeichert und gilt ab dem nächsten Scan.

## Was angezeigt wird

Nachgeschlagen werden nur Codes **ohne** Katalogtext. Zu jedem Treffer steht der Text
der Quelle (englisch), die Datei und ein Link auf die Zeile in der Quelle:

```text
Gespeichert:
  P1220  (keine Beschreibung im Katalog)
         Online, ungeprüft: Fuel Quantity Actuator Y231
         Quelle: Wal33D/dtc-database (MIT), mercedes_codes.txt, https://github.com/…#L2
         Im Web suchen: https://duckduckgo.com/?q=P1220+Mercedes-Benz
```

Die Erklärung landet auch in der gespeicherten Sitzung (Feld `online` am Code) und im
PDF-Bericht, dort als „Online-Erklärung (ungeprüft)“ mit Quelle.

:::{warning}
**Ungeprüft.** Die Texte stammen aus einer frei verfügbaren Sammlung, deren Herkunft
nicht belegt ist, und sind oft knapp. Sie ersetzen weder die Unterlagen des Herstellers
noch eine Werkstatt. obd-diag zeigt sie deshalb immer getrennt vom Katalogtext und mit
Quelle.
:::

## Woher die Texte kommen

Quelle ist [Wal33D/dtc-database](https://github.com/Wal33D/dtc-database)
(MIT-Lizenz, die Nutzung und Weitergabe erlaubt), fest auf einen Commit gepinnt. Sie
enthält je Hersteller eine Textdatei (z. B. `mercedes_codes.txt`) und je Codefamilie
eine allgemeine (`p_codes.txt`, `u_codes.txt` …).

- **Herstellerspezifische Codes** werden nur in der Datei des Herstellers gesucht, den
  die FIN nennt ({doc}`../technik/fin`). Derselbe Code bedeutet bei einem anderen
  Hersteller etwas anderes: `U1218` ist bei Ford ein Fehler im J1850-Bus der
  Außenbeleuchtung; am Mercedes kann er etwas ganz anderes bedeuten. Ohne FIN (`scan`)
  oder bei einem Hersteller ohne eigene Datei bleiben sie deshalb ohne Erklärung.
- **Genormte Codes** kommen aus der Herstellerdatei, falls sie dort stehen, sonst aus
  der allgemeinen Datei.
- Die Sammlung ist klein: Für Mercedes-Benz enthält sie rund 30 Codes, alle `P1xxx`.
  Für viele Codes bleibt es deshalb bei „Im Web suchen“.

## Was ins Netz geht

- obd-diag lädt **ganze Dateien** der Quelle von `raw.githubusercontent.com`. Der Server
  erfährt nur, welche Datei gebraucht wird (z. B. die Mercedes-Datei), weder den
  Fehlercode noch die FIN.
- Jede Datei wird einmal geladen und unter `$XDG_CACHE_HOME/obd-diag/dtc-online/`
  (Standard `~/.cache/obd-diag/dtc-online/`) abgelegt. Weil der Commit fest ist, ändert
  sie sich nie; danach geht für sie nichts mehr ins Netz.
- Ohne Netz oder bei Fehlern läuft die Diagnose normal weiter, die Codes haben dann
  einfach keine Online-Erklärung.
- „Im Web suchen“ (Schaltfläche in der Oberfläche, Link in der Kommandozeile) ruft
  obd-diag nie selbst ab. Erst wer den Link im Browser öffnet, schickt Code und
  Hersteller an die Suchmaschine. Die Schaltfläche gibt es auch ohne Online-Erklärung.

Unabhängig davon schlägt `--online-vin` die FIN bei NHTSA vPIC nach
({doc}`sitzungen`); beide Schalter sind getrennt.
