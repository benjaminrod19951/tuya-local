"""Exercise full-state replay and reject missing or malformed commands."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.tuya_local.device import TuyaLocalDevice
from custom_components.tuya_local.helpers.ir_codebook import (
    attach_ir_packet,
    load_codebook,
)

TARGET_DEVICE_ID = "synthetic_controller"

STATE = {"1": True, "3": 25, "4": "warm", "5": "low"}
# Synthetic test fixtures, never real appliance commands.
PACKET = {"control": "send_ir", "ver": "3", "key1": {"data": "TEST_ONLY"}}


BASE = {
    "version": 1,
    "enabled": True,
    "trigger_dps": ["1", "3", "4", "5"],
    "key_dps": ["4", "3", "5"],
    "output_dp": "201",
    "off": {"dp": "1", "value": False, "key": "off"},
}


def book(commands):
    return BASE | {"commands": commands}


def test_temperature_packet_contains_requested_state():
    codes = book({"warm|26|low": PACKET})
    result = attach_ir_packet(STATE, {"3": 26}, codes)
    assert result["3"] == 26
    assert json.loads(result["201"]) == PACKET
    assert STATE["3"] == 25


@pytest.mark.parametrize(
    ("changes", "key"),
    [
        ({"1": False}, "off"),
        ({"4": "cold"}, "cold|25|low"),
        ({"5": "high"}, "warm|25|high"),
        ({"1": True, "3": 26, "4": "cold", "5": "auto"}, "cold|26|auto"),
    ],
)
def test_full_state_selection(changes, key):
    assert "201" in attach_ir_packet(STATE, changes, book({key: PACKET}))


def test_missing_code_rejects_without_mutation():
    changes = {"3": 26}
    with pytest.raises(ValueError, match="nothing was queued"):
        attach_ir_packet(STATE, changes, book({}))
    assert changes == {"3": 26}


@pytest.mark.parametrize(
    "packet",
    [
        {},
        {"control": "irlib_download"},
        {"control": "send_ir"},
        {"control": "send_ir", "key1": "x" * 3072},
    ],
)
def test_reject_invalid_packets(packet):
    with pytest.raises(ValueError):
        attach_ir_packet(STATE, {"3": 26}, book({"warm|26|low": packet}))


def test_non_control_and_explicit_ir_are_unchanged():
    assert attach_ir_packet(STATE, {"7": True}, book({})) == {"7": True}
    assert attach_ir_packet(STATE, {"201": "raw"}, book({})) == {"201": "raw"}
    assert attach_ir_packet(STATE, {"3": 26}, None) == {"3": 26}


def test_local_codebook_opt_in(tmp_path):
    path = tmp_path / "codes.json"
    assert load_codebook(str(path)) is None
    path.write_text('{"enabled": false}')
    assert load_codebook(str(path)) is None
    for value in (True, 2):
        path.write_text(json.dumps(book({}) | {"version": value}))
        with pytest.raises(ValueError):
            load_codebook(str(path))
    path.write_text(json.dumps(book({})))
    assert load_codebook(str(path))["commands"] == {}


async def test_device_replay_is_sent_with_state_and_missing_code_never_queues(tmp_path):
    import asyncio

    path = tmp_path / "codes.json"
    codes = book({"warm|26|low": PACKET})
    path.write_text(json.dumps(codes))
    device = object.__new__(TuyaLocalDevice)
    device.dev_id = TARGET_DEVICE_ID
    device.dev_cid = ""
    device._ir_codebook_lock = asyncio.Lock()

    async def executor(fn, *args):
        return fn(*args)

    device._hass = SimpleNamespace(
        config=SimpleNamespace(path=lambda *args: str(path)),
        async_add_executor_job=executor,
    )
    device._get_cached_state = lambda: STATE
    device._add_properties_to_pending_updates = Mock()
    device._debounce_sending_updates = AsyncMock()
    await device.async_set_properties({"3": 26})
    queued = device._add_properties_to_pending_updates.call_args.args[0]
    assert queued["3"] == 26
    assert json.loads(queued["201"]) == PACKET
    device._debounce_sending_updates.assert_awaited_once()
    device._add_properties_to_pending_updates.reset_mock()
    device._debounce_sending_updates.reset_mock()
    with pytest.raises(ValueError, match="nothing was queued"):
        await device.async_set_properties({"3": 27})
    device._add_properties_to_pending_updates.assert_not_called()
    device._debounce_sending_updates.assert_not_awaited()


def test_configurable_layout_and_float_normalization():
    config = {
        "trigger_dps": ["8"],
        "key_dps": ["8"],
        "output_dp": "50",
        "commands": {"26": PACKET},
    }
    assert json.loads(attach_ir_packet({}, {"8": 26.0}, config)["50"]) == PACKET


@pytest.mark.parametrize("value", [None, True, 26.5, float("inf"), "a|b"])
def test_ambiguous_selectors_rejected(value):
    with pytest.raises(ValueError):
        attach_ir_packet(STATE, {"3": value}, book({}))


def test_required_state_and_missing_power():
    config = book({"warm|26|low": PACKET}) | {"required_state": {"11": "c"}}
    with pytest.raises(ValueError, match="requires DP"):
        attach_ir_packet(STATE | {"11": "f"}, {"3": 26}, config)
    assert "201" in attach_ir_packet(STATE | {"11": "c"}, {"3": 26}, config)
    with pytest.raises(ValueError, match="power selector"):
        attach_ir_packet(
            {k: v for k, v in STATE.items() if k != "1"}, {"3": 26}, book({})
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("trigger_dps", []),
        ("trigger_dps", [1]),
        ("key_dps", None),
        ("output_dp", "1"),
        ("commands", []),
        ("off", {"dp": "x"}),
        ("required_state", []),
    ],
)
def test_invalid_configuration(tmp_path, field, value):
    path = tmp_path / "codes.json"
    path.write_text(json.dumps(book({}) | {field: value}))
    with pytest.raises(ValueError):
        load_codebook(str(path))


def test_oversize_and_invalid_json(tmp_path):
    path = tmp_path / "codes.json"
    path.write_text("x" * (4 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="size limit"):
        load_codebook(str(path))
    path.write_text("{invalid")
    with pytest.raises(ValueError):
        load_codebook(str(path))


async def test_subdevice_bypasses_codebook():
    device = object.__new__(TuyaLocalDevice)
    device.dev_cid = "synthetic_child"
    device._add_properties_to_pending_updates = Mock()
    device._debounce_sending_updates = AsyncMock()
    await device.async_set_properties({"3": 26})
    device._add_properties_to_pending_updates.assert_called_once_with({"3": 26})
    device._debounce_sending_updates.assert_awaited_once()
