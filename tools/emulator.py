"""ELM327-emulator mit standardgemäßen Antworten starten, zum Ausprobieren ohne Auto.

    uv run python tools/emulator.py                       # P0133, P0300 gespeichert
    uv run python tools/emulator.py --stored P0420 --pending P0171 --engine-off

Gibt das virtuelle serielle Gerät aus (z. B. /dev/pts/5), das sich in
``obd-diag ... --port`` oder in der Oberfläche eintragen lässt. Beenden mit Strg+C.
Die Abweichungen des Emulators vom Standard korrigiert ``tests/emulator_patches.py``.
"""

import argparse
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.emulator_patches import (
    patch_dtc_count_byte,
    patch_freeze_frame,
    patch_persistent_clear,
    rpm_zero,
)


def _raw(code: str) -> str:
    """``P0133`` -> ``0133`` (zwei Rohbytes als Hex, wie der Emulator sie erwartet)."""
    code = code.strip().upper()
    if len(code) != 5 or code[0] not in "PCBU":
        raise argparse.ArgumentTypeError(f"kein Fehlercode: {code!r}")
    high = ("PCBU".index(code[0]) << 2) | int(code[1], 16)
    return f"{high:X}{code[2:]}"


def _codes(text: str) -> list[str]:
    return [_raw(c) for c in text.split(",") if c.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stored", type=_codes, default=_codes("P0133,P0300"), metavar="CODES")
    parser.add_argument("--pending", type=_codes, default=[], metavar="CODES")
    parser.add_argument("--permanent", type=_codes, default=[], metavar="CODES")
    parser.add_argument(
        "--engine-off", action="store_true", help="Drehzahl 0, damit Löschen erlaubt ist"
    )
    args = parser.parse_args()

    from elm import Elm, obd_message

    mp = pytest.MonkeyPatch()
    mp.setattr(obd_message, "DTC_STORED", args.stored)
    mp.setattr(obd_message, "DTC_PENDING", args.pending)
    mp.setattr(obd_message, "DTC_PERMANENT", args.permanent)
    patch_dtc_count_byte(mp, obd_message)
    patch_freeze_frame(mp, obd_message, args.stored[0] if args.stored else "0000")
    patch_persistent_clear(mp, obd_message)

    emulator = Elm()
    emulator.set_sorted_obd_msg("car")
    if args.engine_off:
        emulator.answer["RPM"] = rpm_zero(obd_message)
    port = emulator.get_pty()
    threading.Thread(target=emulator.run, daemon=True).start()
    time.sleep(0.5)
    print(f"\nEmulator läuft auf {port}  (Strg+C beendet)")
    print(f"  uv run obd-diag diagnose --port {port}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        emulator.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
