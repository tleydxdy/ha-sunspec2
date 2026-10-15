"""The Smart WiLAN HTTP transport: frame layer, error mapping, connection state.

The frames asserted here are the ones captured from the device while the
transport was being built: a read of raw holding 40000 (count 2) and its
``SunS`` answer, CRC included. Everything the scanner walks on the real
device goes through the same encode and decode pair, so keeping the
capture as the golden vector pins the whole wire format.
"""

import json
from typing import Any
from unittest.mock import AsyncMock

import aiohttp
import pytest
from modbus_connection import IllegalDataAddressError
from modbus_connection import ModbusConnectionError
from modbus_connection import ModbusExceptionError
from modbus_connection import ModbusTimeoutError

from custom_components.sunspec2.wilan_http import ERRNO_NO_DATA
from custom_components.sunspec2.wilan_http import MODBUS_RW_PATH
from custom_components.sunspec2.wilan_http import WiLanHTTPConnection
from custom_components.sunspec2.wilan_http import _words_from_response
from custom_components.sunspec2.wilan_http import build_rtu_frame
from custom_components.sunspec2.wilan_http import crc16_modbus

# Captured from the device: read slave 1, FC 03, address 40000 (0x9c40),
# count 2. The answer carries 0x5375, 0x6e53, the "SunS" SunSpec magic.
CAPTURED_REQUEST_HEX = "01039c400002eb8f"
CAPTURED_REQUEST_BODY = bytes.fromhex("01039c400002")
CAPTURED_REPLY_HEX = "01030453756e5396f0"
CAPTURED_REPLY_BODY = bytes.fromhex("01030453756e53")

# Captured from the device: read slave 1, FC 03, address 41000, count 17.
# The block reads all zeros and the CRC is what the device sent.
CAPTURED_17_REG_REPLY_HEX = "010322" + "00" * 34 + "b743"


def test_crc_matches_the_captured_frames():
    """The CRC helper agrees with real device bytes, low byte first on the wire.

    The captured frame ``01 03 9c40 0002`` carries the wire bytes
    ``eb 8f``, i.e. crc value 0x8feb; the captured ``SunS`` reply carries
    ``96 f0``, i.e. 0xf096. A CRC that only round-trips through itself
    proves nothing, so the wire answer is the vector.
    """
    assert crc16_modbus(CAPTURED_REQUEST_BODY) == 0x8FEB
    assert crc16_modbus(CAPTURED_REPLY_BODY) == 0xF096


def test_build_rtu_frame_reproduces_the_captured_request():
    """The frame builder writes the same bytes a healthy device has answered before.

    This exact request drove the device read that reported the SunSpec
    magic at 40000 during the build of the transport.
    """
    assert build_rtu_frame(1, 0x03, 40000, 2) == CAPTURED_REQUEST_HEX


def test_response_words_decode_with_crc_verified():
    """A valid reply decodes into its registers."""
    assert _words_from_response(CAPTURED_REPLY_HEX, function=0x03, count=2, slave=1) == [
        0x5375,
        0x6E53,
    ]


def test_response_decodes_a_full_captured_register_block():
    """A whole 17 register block straight off the device decodes zero by zero.

    This is the practical shape a poll sees: one read of a few dozen
    registers, the CRC rechecked over the body before a single value is
    believed.
    """
    assert (
        _words_from_response(CAPTURED_17_REG_REPLY_HEX, function=0x03, count=17, slave=1)
        == [0] * 17
    )


def test_response_rejects_an_altered_crc():
    """A reply whose CRC was flipped must not decode into plausible values."""
    tampered = "01030453756e530096f0"
    with pytest.raises(ModbusConnectionError):
        _words_from_response(tampered, function=0x03, count=2, slave=1)


def test_response_rejects_a_data_count_that_comes_up_short():
    """The frame must carry the bytes its own byte count announced.

    The head announces four data bytes (two registers) and the answer
    stops after one register, no CRC at all: the decoder refuses before
    it gets anywhere near the CRC check.
    """
    frame_hex = "0103045375"
    with pytest.raises(ModbusConnectionError, match="truncated"):
        _words_from_response(frame_hex, function=0x03, count=2, slave=1)


def test_response_rejects_a_borrowed_frame():
    """A reply from another slave is a routing problem, raise instead of attach."""
    wrong = "02030453756e5396f0"
    with pytest.raises(ModbusConnectionError, match="slave 2"):
        _words_from_response(wrong, function=0x03, count=2, slave=1)


def test_response_rejects_a_function_mismatch():
    """A 0x03 answer to a 0x04 request is not the answer for this request."""
    with pytest.raises(ModbusConnectionError, match="function"):
        _words_from_response(CAPTURED_REPLY_HEX, function=0x04, count=2, slave=1)


def test_response_carries_a_modbus_exception_as_such():
    """Exception frames surface as ModbusExceptionError, not garbage words.

    The frame here is ``01 83 02`` (exception code 2) with a CRC the
    helper itself computed, so the raised error names the real Modbus
    exception for the log line.
    """
    frame = bytes.fromhex("018302")
    frame_hex = (frame + crc16_modbus(frame).to_bytes(2, "little")).hex()
    with pytest.raises(ModbusExceptionError, match="exception 2"):
        _words_from_response(frame_hex, function=0x03, count=2, slave=1)


def test_response_rejects_unparsable_hex():
    """A body that is not hex at all is a transport failure, not a value."""
    with pytest.raises(ModbusConnectionError):
        _words_from_response("not hex at all", function=0x03, count=2, slave=1)


async def test_exchange_posts_the_captured_frame_and_decodes_the_reply(hass):
    """One exchange becomes one POST, and the reply decodes to its registers."""
    connection = WiLanHTTPConnection(host="192.0.2.10", timeout=10)
    connection._post_json = AsyncMock(
        return_value={
            "errno": 0,
            "errmsg": "success",
            "data": {"mstype": 3, "result": CAPTURED_REPLY_HEX},
        }
    )

    words = await connection.exchange(1, 0x03, 40000, 2)

    connection._post_json.assert_awaited_once_with({"cmd": CAPTURED_REQUEST_HEX})
    assert words == [0x5375, 0x6E53]


async def test_exchange_maps_datalogger_error_numbers(hass):
    """The datalogger's own errno values carry protocol meaning.

    ``20003`` is the datalogger saying nothing behind it answered that
    address, which the scan treats as an illegal address. ``20002`` is
    the same class of answer on the model-registry path. Anything else
    that is not zero refuses the exchange outright, and an answer with
    a zero errno and no result frame is an error too.
    """
    connection = WiLanHTTPConnection(host="192.0.2.10", timeout=10)

    connection._post_json = AsyncMock(return_value={"errno": ERRNO_NO_DATA, "errmsg": "no data"})
    with pytest.raises(IllegalDataAddressError):
        await connection.exchange(1, 0x03, 40000, 2)

    connection._post_json = AsyncMock(return_value={"errno": 20002, "errmsg": "id not found"})
    with pytest.raises(IllegalDataAddressError):
        await connection.exchange(1, 0x03, 40000, 2)

    connection._post_json = AsyncMock(return_value={"errno": 99, "errmsg": "other"})
    with pytest.raises(ModbusConnectionError):
        await connection.exchange(1, 0x03, 40000, 2)

    connection._post_json = AsyncMock(return_value={"errno": 0})
    with pytest.raises(ModbusConnectionError, match="no result"):
        await connection.exchange(1, 0x03, 40000, 2)


async def test_connect_probes_then_holds_the_flag(hass):
    """Connect runs one real exchange; a healthy answer flips the flag."""
    connection = WiLanHTTPConnection(host="192.0.2.10", timeout=10)
    post = AsyncMock(return_value={"errno": 0, "data": {"result": CAPTURED_REPLY_HEX}})
    connection._post_json = post
    await connection.connect()
    assert connection.connected is True
    # The probe read goes to the SunSpec base the scanner would use.
    assert post.call_args is not None
    assert post.call_args.args[0] == {"cmd": CAPTURED_REQUEST_HEX}

    await connection.disconnect()
    assert connection.connected is False

    connection._post_json = AsyncMock(return_value={"errno": ERRNO_NO_DATA})
    with pytest.raises(IllegalDataAddressError):
        await connection.connect()
    assert connection.connected is False


async def test_close_drops_the_state_and_the_session(hass):
    """Closing tears the HTTP session down and is fine to run twice."""
    connection = WiLanHTTPConnection(host="192.0.2.10", timeout=10)
    connection._connected = True
    await connection.close()
    assert connection.connected is False
    assert connection._session is None
    await connection.close()


async def test_writes_refuse_at_the_unit(hass):
    """Every write refuses until the write path is validated on hardware."""
    connection = WiLanHTTPConnection(host="192.0.2.10", timeout=10)
    unit = connection.for_unit(1)
    with pytest.raises(ModbusConnectionError):
        await unit.write_registers(40000, [1, 2])
    with pytest.raises(ModbusConnectionError):
        await unit.write_register(40000, 1)


def test_for_unit_hands_out_a_stable_identity():
    """Repeated for_unit on one id is one unit; separate ids stay separate."""
    connection = WiLanHTTPConnection(host="192.0.2.10", timeout=10)
    first = connection.for_unit(1)
    assert connection.for_unit(1) is first
    assert connection.for_unit(2) is not first
    assert first.unit_id == 1


def test_scheme_host_adds_http_only_when_needed():
    """Bare hosts get http; anything with a scheme is honored as given."""
    bare = WiLanHTTPConnection(host="192.0.2.10", timeout=10)
    assert bare.scheme_host == "http://192.0.2.10"
    explicit = WiLanHTTPConnection(host="http://192.0.2.10", timeout=10)
    assert explicit.scheme_host == "http://192.0.2.10"


async def test_the_api_client_builds_the_wilan_connection_for_the_wilan_transport(hass):
    """Transport routing: ``wilan`` entries drive the HTTP connection, not Modbus TCP.

    The connection is built lazily on first use and constructing it does
    no I/O, so asserting on its class and host is enough; the end-to-end
    shape is covered by the exchange tests above.
    """
    from custom_components.sunspec2.api import SunSpecApiClient

    api = SunSpecApiClient(
        host="192.0.2.10",
        port=80,
        unit_id=1,
        hass=hass,
        transport="wilan",
    )
    connection = api._get_connection()
    assert isinstance(connection, WiLanHTTPConnection)
    assert connection.scheme_host == "http://192.0.2.10"
    # A second build hands back the same object, like modbus-connection's.
    assert api._get_connection() is connection


async def test_config_flow_wilan_step_creates_an_entry(hass, sunspec_client_mock):
    """Walking the Smart WiLAN setup happy path must create a HTTP entry.

    The ``sunspec_client_mock`` fixture patches ``modbus_connect`` at the
    ``SunSpecApiClient`` level, so the transport never actually posts:
    the probe answer is the standard test inverter and the entry lands
    in CREATE_ENTRY state. What this pins is the config plumbing:
    transport value, the fixed port, and the unit id all stored.
    """
    from homeassistant import config_entries
    from homeassistant.data_entry_flow import FlowResultType

    from custom_components.sunspec2.const import CONF_HOST
    from custom_components.sunspec2.const import CONF_PORT
    from custom_components.sunspec2.const import CONF_TRANSPORT
    from custom_components.sunspec2.const import CONF_UNIT_ID
    from custom_components.sunspec2.const import DOMAIN
    from custom_components.sunspec2.const import TRANSPORT_WILAN
    from custom_components.sunspec2.const import WILAN_HTTP_PORT

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.MENU

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"next_step_id": "wilan"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "wilan"
    schema_keys = {str(marker) for marker in result["data_schema"].schema}
    assert {"host", "unit_id"} <= schema_keys
    # There is deliberately no port field: the datalogger serves HTTP on
    # port 80 only.
    assert "port" not in schema_keys

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={CONF_HOST: "192.0.2.10", CONF_UNIT_ID: 1},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "settings"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "prefix": "",
            "scan_interval": 30,
            "models_enabled": ["103"],
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TRANSPORT] == TRANSPORT_WILAN
    assert result["data"][CONF_HOST] == "192.0.2.10"
    assert result["data"][CONF_PORT] == WILAN_HTTP_PORT
    assert result["data"][CONF_UNIT_ID] == 1


async def test_post_errors_map_onto_the_transport_hierarchy(hass):
    """An HTTP timeout is a ModbusTimeoutError, a refused connect a connection error.

    The datalogger is nothing but an HTTP endpoint here, so the HTTP
    failure modes ARE the Modbus transport failure modes the polling
    pipeline already knows how to classify. A body that will not parse
    as JSON lands in the same boat: it is a transport condition, not a
    value.
    """
    connection = WiLanHTTPConnection(host="192.0.2.10", timeout=10)

    session = _StubSession(_StubResponse(raiser=aiohttp.ServerTimeoutError("over the wait")))
    connection._get_session = AsyncMock(return_value=session)
    with pytest.raises(ModbusTimeoutError):
        await connection._post_json({"cmd": CAPTURED_REQUEST_HEX})

    session = _StubSession(_StubResponse(raiser=aiohttp.ClientConnectionError("refused")))
    connection._get_session = AsyncMock(return_value=session)
    with pytest.raises(ModbusConnectionError):
        await connection._post_json({"cmd": CAPTURED_REQUEST_HEX})

    session = _StubSession(_StubResponse(body="<!DOCTYPE html> not json"))
    connection._get_session = AsyncMock(return_value=session)
    with pytest.raises(ModbusConnectionError, match="non-JSON"):
        await connection._post_json({"cmd": CAPTURED_REQUEST_HEX})

    # A JSON body that parses but is not an object (an array) is the
    # other half: parsed, then rejected by the envelope check.
    session = _StubSession(_StubResponse(body="[1, 2]"))
    connection._get_session = AsyncMock(return_value=session)
    with pytest.raises(ModbusConnectionError):
        await connection._post_json({"cmd": CAPTURED_REQUEST_HEX})


async def test_the_session_hits_the_right_path_and_carries_the_frame(hass):
    """The URL is the captured endpoint on the configured host."""
    connection = WiLanHTTPConnection(host="192.0.2.10", timeout=10)
    session = _StubSession(_StubResponse(body={"errno": 0, "data": {"result": CAPTURED_REPLY_HEX}}))
    connection._get_session = AsyncMock(return_value=session)
    await connection.exchange(1, 0x03, 40000, 2)
    url, kwargs = session.requests[0]
    assert url == f"http://192.0.2.10{MODBUS_RW_PATH}"
    assert kwargs["json"] == {"cmd": CAPTURED_REQUEST_HEX}


class _StubResponse:
    """The two calls _post_json makes on a response object."""

    def __init__(self, raiser=None, body=None) -> None:
        self._raiser = raiser
        self._body = body

    def raise_for_status(self) -> None:
        if self._raiser is not None:
            raise self._raiser

    async def json(self, content_type=None):  # noqa: ARG002 - signature parity
        if isinstance(self._body, Exception):
            raise self._body
        if isinstance(self._body, str):
            # Mirror aiohttp: a body is run through json.loads, and a
            # body that will not parse raises JSONDecodeError (a
            # ValueError) exactly where the real client would.
            return json.loads(self._body)
        return self._body


class _StubPostContext:
    """One ``session.post(...)`` context manager over a canned answer."""

    def __init__(self, response: _StubResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _StubResponse:
        return self._response

    async def __aexit__(self, *args):
        return None


class _StubSession:
    """The one aiohttp call this transport uses, as the tests need it.

    Records every request so the URL and the body can be asserted, and
    hands back the same canned response every time.
    """

    def __init__(self, response: _StubResponse) -> None:
        self._response = response
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        return _StubPostContext(self._response)

    async def close(self):
        return None
