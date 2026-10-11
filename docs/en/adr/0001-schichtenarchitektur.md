# ADR 0001: Layered architecture with an exchangeable transport

Status: accepted (2026-10-07)

## Context
The tool should work with USB, Bluetooth and later CAN adapters and be testable without
a car. python-OBD is still before 1.0, its API may change.

## Decision
Layers transport → protocol → services → ui. The transport layer is a
`typing.Protocol` with `open()`, `close()`, `write()` and `read_until()`. Third-party
libraries are wrapped behind own interfaces.

## Consequences
Tests run against a fake transport or the ELM327 emulator. Switching from python-OBD
to an own driver only affects the protocol layer.
