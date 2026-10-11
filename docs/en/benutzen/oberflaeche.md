# User interface

```sh
uv run obd-diag-gui
```

Choose port and baud rate at the top (the list shows adapters that were found, a path
can also be typed in) and press “Connect & Scan”. This reads trouble codes, readiness,
freeze frame and VIN in one pass, read-only. The vehicle (manufacturer and VIN) then
appears at the top right, below it five tabs. Adapter, protocol and battery voltage
(red when the voltage is low) are shown at the bottom.

The user interface speaks English and German: *Options → Sprache / Language*
({doc}`sprache`). The pictures on this page come from the emulator
(`tools/docs_screenshots.py --lang en`) and follow the light or dark theme of this
website.

## Trouble codes

```{image} ../_static/screenshots/fehlercodes-light.png
:alt: Trouble codes tab: codes by type on the left, explanation on the right
:class: only-light
```

```{image} ../_static/screenshots/fehlercodes-dark.png
:alt: Trouble codes tab: codes by type on the left, explanation on the right
:class: only-dark
```


On the left the codes, grouped into stored, pending and permanent; on the right the
explanation of the selected code with causes and their likelihood, symptoms and cost
range from the offline catalog ({doc}`fehlercodes`). If a code is missing from the
catalog, “Search the web” opens a web search in the browser; with “Explain trouble
codes online” an unverified short explanation appears there as well
({doc}`fehlercodes-online`).

## Readiness

```{image} ../_static/screenshots/readiness-light.png
:alt: Readiness tab: overall verdict and monitors
:class: only-light
```

```{image} ../_static/screenshots/readiness-dark.png
:alt: Readiness tab: overall verdict and monitors
:class: only-dark
```


“All tests complete” (green) or “Not all tests complete” (yellow) with the incomplete
tests, check engine light, engine type and each monitor as complete, incomplete or not
supported. This is deliberately not an emissions test verdict ({doc}`sitzungen`).

## Freeze frame

```{image} ../_static/screenshots/freeze-frame-light.png
:alt: Freeze frame tab: values when the code was stored
:class: only-light
```

```{image} ../_static/screenshots/freeze-frame-dark.png
:alt: Freeze frame tab: values when the code was stored
:class: only-dark
```


The triggering code and the values when the code was stored: engine load, coolant
temperature, engine speed, speed.

## Vehicle

```{image} ../_static/screenshots/fahrzeug-light.png
:alt: Vehicle tab: VIN and decoding
:class: only-light
```

```{image} ../_static/screenshots/fahrzeug-dark.png
:alt: Vehicle tab: VIN and decoding
:class: only-dark
```


VIN, manufacturer, country, model year and check digit ({doc}`../technik/fin`). “Look
up VIN online (NHTSA)” (menu *Options* or here) is off by default; when switched on,
only the VIN goes to the US authority NHTSA at the next scan.

## Live data

```{image} ../_static/screenshots/live-daten-light.png
:alt: Live data tab: tiles with value and history curve
:class: only-light
```

```{image} ../_static/screenshots/live-daten-dark.png
:alt: Live data tab: tiles with value and history curve
:class: only-dark
```


Choose values (the ones the vehicle supports appear after the first start), interval
0.5, 1 or 2 s, optionally “Record (CSV)”, then Start. One tile per value with the
current value, the smallest and largest value seen and a curve of the last 120 values.
While live data is running, scan, clearing and export are locked, because only one
action runs on the adapter at a time ({doc}`live-daten`).

## Menus and buttons

| Action | Shortcut | What happens |
| --- | --- | --- |
| Save session | Ctrl+S | store the diagnosis as JSON, the path is shown |
| Open session … | Ctrl+O | view a saved session only; clearing is not possible then |
| Report as PDF … | Ctrl+P | write a PDF report in the background |
| Export CSV … | – | one row per trouble code |
| Options → Explain trouble codes online | – | codes without a catalog text get an unverified short explanation ({doc}`fehlercodes-online`) |
| Options → Record adapter trace | – | every action writes a trace ({doc}`sitzungen`) |
| Options → Theme | – | like the system, light or dark |
| Options → Sprache / Language | – | Deutsch or English, applies immediately ({doc}`sprache`) |
| Clear trouble codes … | – | currently locked (grey, the tooltip gives the reason) |

Settings are stored per user in `~/.config/obd-diag/obd-diag.conf`. Every action opens
the port, works in a background thread and closes it again; the user interface stays
responsive meanwhile.

## Trying it against the emulator

```sh
uv run python tools/emulator.py   # shows the pty, e.g. /dev/pts/5
uv run obd-diag-gui               # enter /dev/pts/5 in the port field, connect
```
