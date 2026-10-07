"""OBD-II-Dienste über den ELM327 (nur lesend)."""

from obd_diag.protocol.dtc_decode import parse_dtc_response
from obd_diag.protocol.elm327 import Elm327, ElmError

# Modes, die Fehlercodes liefern: gespeichert, ausstehend, permanent.
DTC_MODES = (0x03, 0x07, 0x0A)


def read_dtcs(elm: Elm327, mode: int, *, can: bool) -> list[str]:
    """Fehlercodes aller Steuergeräte für Mode 03, 07 oder 0A.

    ``NO DATA`` heißt: kein Code gespeichert, also eine leere Liste.
    """
    if mode not in DTC_MODES:
        raise ValueError(f"Mode {mode:02X} liefert keine Fehlercodes")
    cmd = f"{mode:02X}"
    response = elm.query(cmd)
    if response is None:
        return []
    try:
        return parse_dtc_response(response, mode, can=can)
    except ValueError as e:
        raise ElmError(f"{cmd}: {e}") from e
