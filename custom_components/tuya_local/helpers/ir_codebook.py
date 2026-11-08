"""Opt-in local IR packet lookup without cloud credentials or device identifiers."""

import json
from pathlib import Path

MAX_CODEBOOK_BYTES = 4 * 1024 * 1024
MAX_PACKET_BYTES = 3072


def load_codebook(path: str) -> dict | None:
    """Read a local codebook. Missing and disabled files preserve stock behavior."""
    try:
        with Path(path).open(encoding="utf-8") as source:
            text = source.read(MAX_CODEBOOK_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(text.encode("utf-8")) > MAX_CODEBOOK_BYTES:
        raise ValueError("IR codebook exceeds the size limit")
    book = json.loads(text)
    if not isinstance(book, dict):
        raise ValueError("IR codebook must be an object")
    if book.get("enabled") is not True:
        return None
    if type(book.get("version")) is not int or book["version"] != 1:
        raise ValueError("Unsupported IR codebook version")
    for name in ("trigger_dps", "key_dps"):
        if not isinstance(book.get(name), list) or not book[name]:
            raise ValueError(f"IR codebook requires {name}")
        if any(not isinstance(dp, str) or not dp.isdecimal() for dp in book[name]):
            raise ValueError(f"IR codebook {name} must contain DP ID strings")
    output = book.get("output_dp")
    if not isinstance(output, str) or not output.isdecimal():
        raise ValueError("IR codebook requires an output_dp string")
    if output in book["trigger_dps"]:
        raise ValueError("IR output_dp must not trigger replay")
    if not isinstance(book.get("commands"), dict):
        raise ValueError("IR codebook requires commands")
    required = book.get("required_state", {})
    if not isinstance(required, dict):
        raise ValueError("IR required_state must be an object")
    off = book.get("off")
    if off is not None and (
        not isinstance(off, dict)
        or not isinstance(off.get("dp"), str)
        or not off["dp"].isdecimal()
        or "value" not in off
        or not isinstance(off.get("key"), str)
    ):
        raise ValueError("Invalid IR off selector")
    return book


def _key_value(value):
    """Use stable JSON scalar values; match integer temperatures sent as floats."""
    if isinstance(value, str):
        if "|" in value:
            raise ValueError("IR selector values cannot contain '|'")
        return value
    if type(value) in (int, float):
        if isinstance(value, float) and not value.is_integer():
            raise ValueError("IR selector numbers must be finite integers")
        return str(int(value))
    raise ValueError("IR selector must be a string or integer")


def attach_ir_packet(state: dict, changes: dict, book: dict | None) -> dict:
    """Resolve the post-update state and validate before anything is queued."""
    if book is None:
        return changes
    changes = {str(key): value for key, value in changes.items()}
    output = book["output_dp"]
    if output in changes or not set(book["trigger_dps"]).intersection(changes):
        return changes
    target = {str(key): value for key, value in state.items()} | changes
    for dp, expected in book.get("required_state", {}).items():
        if dp not in target or target[dp] != expected:
            raise ValueError(f"IR codebook requires DP {dp} to be {expected!r}")
    off = book.get("off")
    if off and off["dp"] not in target:
        raise ValueError("Missing IR power selector")
    if off and target[off["dp"]] == off["value"]:
        key = off["key"]
    else:
        try:
            key = "|".join(_key_value(target[dp]) for dp in book["key_dps"])
        except KeyError as err:
            raise ValueError(f"Missing IR selector DP {err.args[0]}") from err
    packet = book["commands"].get(key)
    if not isinstance(packet, dict) or packet.get("control") != "send_ir":
        raise ValueError(f"No send_ir packet for {key!r}; nothing was queued")
    if not isinstance(packet.get("key1"), (str, dict)) or not packet["key1"]:
        raise ValueError("IR packet requires key1")
    payload = json.dumps(
        packet, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    )
    if len(payload.encode("utf-8")) > MAX_PACKET_BYTES:
        raise ValueError("IR packet exceeds the size limit")
    return changes | {output: payload}
