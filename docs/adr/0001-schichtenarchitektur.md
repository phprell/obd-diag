# ADR 0001: Schichtenarchitektur mit austauschbarem Transport

Status: angenommen (2026-10-07)

## Kontext
Das Tool soll mit USB-, Bluetooth- und später CAN-Adaptern arbeiten und ohne Auto
testbar sein. python-OBD ist noch vor 1.0, die API kann sich ändern.

## Entscheidung
Schichten transport → protocol → services → ui. Die Transport-Schicht ist ein
`typing.Protocol` mit `open()`, `close()`, `write()` und `read_until()`. Fremd-
bibliotheken werden hinter eigenen Schnittstellen gekapselt.

## Folgen
Tests laufen gegen einen Fake-Transport bzw. den ELM327-Emulator. Ein Wechsel von
python-OBD auf einen eigenen Treiber betrifft nur die Protokoll-Schicht.
