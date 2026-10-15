"""An HTTP transport for FoxESS Smart WiLAN / WLan-WW WiFi dataloggers.

The Smart WiLAN does not speak Modbus TCP or RTU on any TCP port. What
it serves instead is a small HTTP API on port 80 behind a Vue frontend.
The piece this transport needs is:

    POST /api/v1/sunspec/modbus_rw
    {"cmd": "01039c400002eb8f"}

where ``cmd`` is ONE complete Modbus RTU request frame, hex encoded.
The device relays the frame to the device behind it (the hub itself, or
an inverter) and answers:

    {"errno": 0, "errmsg": "success", "mstype": 3,
     "data": {"result": "01030453756e5396f0"}}

``data.result`` is the matching Modbus RTU response frame, hex encoded,
CRC included.

That is enough to implement the duck-typed ``ModbusConnection`` surface
the polling pipeline expects (the test suite's ``FakeConnection`` in
``tests/fake_unit.py`` documents the exact shape):

* ``for_unit(unit_id)`` hands out a unit that offers
  ``read_holding_registers(address, count)``,
  ``read_input_registers(address, count)``,
  ``write_registers(address, values)`` and
  ``write_register(address, value)``
* ``connect()`` / ``disconnect()`` / ``close()`` and a ``connected``
  property drive the session lifecycle ``api.modbus_connect()`` runs.

Facts established against the hardware this was written on (a Smart
WiLAN at firmware 1.11.005 bridging an AIO-H1 inverter):

* ``GET /api/v1/sunspec/data`` needs no authentication, and the relay
  answer includes no credentials either. The cookie that ``POST
  .../login`` mints for the frontend is not required for ``modbus_rw``
  reads, so the transport does nothing with authentication until a
  reason appears.
* Function 0x03 relays cleanly every time we probed it. Reading raw
  holding address 40000 (count 2) answered ``SunS``, the SunSpec magic
  the image behind the datalogger starts with, and the full SunSpec
  model chain from 40000 to the 0xFFFF end marker walked cleanly.
* Function 0x04 answers on some ranges, but several probed ranges
  returned frames whose byte count did not match the register count
  requested, and raw addresses in the 11000s answer errno 20003 ("no
  data"). Inputs are relayed honestly here: when the frame is
  malformed this raises, so the wrapper above reports a broken device
  rather than plausible garbage.
* Writes were NOT exercised on the device while building this.
  Independent analysis of the device's own frontend bundle (done
  elsewhere, with a hardware verified write round trip) shows the same
  endpoint carrying function 0x10 frames the same way. Write support
  stays out until it is validated on this code path: every write
  raises until then.

Errors map onto the ``modbus_connection`` hierarchy so everything above
``unit_device.translate_error`` keeps working unchanged:

* an answer with ``errno`` 20003 ("no data") is the datalogger saying
  the device did not answer for that address:
  :class:`IllegalDataAddressError`
* a Modbus exception frame (response function bit 0x80 set) becomes a
  :class:`ModbusExceptionError`
* a timeout, a refused HTTP connection or an unparsable answer are
  transport conditions: :class:`ModbusTimeoutError` and
  :class:`ModbusConnectionError`.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
from modbus_connection import IllegalDataAddressError
from modbus_connection import ModbusConnectionError
from modbus_connection import ModbusExceptionError
from modbus_connection import ModbusTimeoutError

_LOGGER = logging.getLogger(__name__)

MODBUS_RW_PATH = "/api/v1/sunspec/modbus_rw"

FUNC_READ_HOLDING = 0x03
FUNC_READ_INPUT = 0x04
FUNC_EXCEPTION_MASK = 0x80

ERRNO_NO_DATA = 20003
ERRNO_ID_NOT_FOUND = 20002


def crc16_modbus(frame: list[int] | bytes) -> int:
    """CRC-16/Modbus (poly 0xA001 reflected, init 0xFFFF), low byte first on the wire.

    Verified against frames captured from the device itself: the frame
    ``01 03 9c40 0002`` carries crc ``eb8f`` on the wire, and the
    captured response ``01 03 04 5375 6e53`` carries ``96f0``.
    """
    crc = 0xFFFF
    for byte in frame:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def build_rtu_frame(slave: int, function: int, address: int, count: int) -> str:
    """Hex encode one Modbus ADU in what the datalogger expects.

    Requests reaching ``modbus_rw`` are ordinary RTU ADUs: slave,
    function, and the function's two-byte payload, plus the CRC in wire
    byte order (low byte first). Captured frames used lowercase hex, so
    the request is emitted lowercase.
    """
    payload = [
        slave & 0xFF,
        function & 0xFF,
        (address >> 8) & 0xFF,
        address & 0xFF,
        (count >> 8) & 0xFF,
        count & 0xFF,
    ]
    crc = crc16_modbus(payload)
    payload.extend([crc & 0xFF, (crc >> 8) & 0xFF])
    return bytes(payload).hex()


def _words_from_response(frame_hex: str, function: int, count: int, slave: int) -> list[int]:
    """Decode a captured RTU reply into plain integers.

    The datalogger relays the device's answer verbatim, CRC included.
    Before anything else the frame must say the slave we asked spoke,
    say a function that matches the request (or carry the exception
    bit), and have a byte count that matches the register count asked
    for. Then the CRC is revalidated over the body. Any deviation
    raises; the transport refuses to invent plausible values out of a
    frame it cannot account for.
    """
    try:
        frame = bytes.fromhex(frame_hex)
    except ValueError as exc:
        raise ModbusConnectionError(f"datalogger returned unparsable hex: {exc}") from exc

    if len(frame) < 5:
        raise ModbusConnectionError(f"datalogger reply too short ({len(frame)} bytes)")

    if frame[0] != (slave & 0xFF):
        raise ModbusConnectionError(
            f"reply came from slave {frame[0]}, not the {slave} that was asked"
        )

    reply_function = frame[1]
    if reply_function & FUNC_EXCEPTION_MASK:
        code = frame[2] if len(frame) > 2 else 0
        # from_code raises the standard subclass the code names
        # (IllegalFunctionError and friends), so callers above can
        # branch with except instead of comparing magic numbers.
        raise ModbusExceptionError.from_code(code, f"Modbus exception {code}")

    if reply_function != function:
        raise ModbusConnectionError(
            f"reply carries function 0x{reply_function:02x}, expected 0x{function:02x}"
        )

    byte_count = frame[2]
    if byte_count != count * 2:
        raise ModbusConnectionError(
            f"reply announces {byte_count} data bytes, expected {count * 2} for {count} registers"
        )

    body = frame[3 : 3 + byte_count]
    if len(body) != byte_count:
        raise ModbusConnectionError(
            f"reply truncated: promises {byte_count} data bytes, carries {len(body)}"
        )

    wire_crc_start = 3 + byte_count
    if len(frame) < wire_crc_start + 2:
        raise ModbusConnectionError("reply carries no CRC")
    expected_crc = crc16_modbus(frame[0:wire_crc_start])
    wire_crc = frame[wire_crc_start : wire_crc_start + 2]
    if wire_crc != bytes([expected_crc & 0xFF, (expected_crc >> 8) & 0xFF]):
        raise ModbusConnectionError("reply CRC does not match its body")

    return list(int.from_bytes(body[i : i + 2], "big") for i in range(0, byte_count, 2))


class _WiLanHTTPUnit:
    """One unit id behind the datalogger, in the FakeUnit shape."""

    def __init__(self, connection: WiLanHTTPConnection, unit_id: int) -> None:
        self.connection = connection
        self.unit_id = unit_id

    async def read_holding_registers(self, address: int, count: int) -> list[int]:
        """FC 0x03 through the datalogger relay."""
        return await self.connection.exchange(self.unit_id, FUNC_READ_HOLDING, address, count)

    async def read_input_registers(self, address: int, count: int) -> list[int]:
        """FC 0x04 through the datalogger relay.

        The datalogger answers this on some address ranges, but several
        ranges probed malformed and the 11000s answer "no data". Still
        relayed honestly: when the frame is malformed this raises, so
        the wrapper above reports a broken device rather than plausible
        garbage. Nothing in this integration's polling uses inputs.
        """
        return await self.connection.exchange(self.unit_id, FUNC_READ_INPUT, address, count)

    async def write_registers(self, address: int, values: list[int]) -> None:
        """Raise unconditionally until write support is validated on hardware.

        The endpoint carries FC 0x10 frames the same way reads carry FC
        0x03. What is not established yet is which value encodings the
        devices behind the hub accept, and a half understood write of
        grid parameters can be expensive. So writes of ANY kind refuse
        here until the write path has been exercised on hardware.
        """
        raise ModbusConnectionError(
            "the Smart WiLAN transport serves reads only at the moment; "
            "register writes are not available"
        )

    async def write_register(self, address: int, value: int) -> None:
        """FC 0x06 equivalent. Refuses like write_registers does."""
        raise ModbusConnectionError(
            "the Smart WiLAN transport serves reads only at the moment; "
            "register writes are not available"
        )


class WiLanHTTPConnection:
    """The datalogger side: state, session, and the exchange itself.

    Lifetime is owned by ``SunSpecApiClient``: one instance per client,
    ``connected`` reflects the last connect, ``close()`` shuts the HTTP
    session and the connection down for good.
    """

    def __init__(self, host: str, timeout: float) -> None:
        self._host = host.rstrip("/")
        self._timeout = timeout
        self._connected = False
        self._session: aiohttp.ClientSession | None = None
        self._units: dict[int, _WiLanHTTPUnit] = {}

    @property
    def scheme_host(self) -> str:
        """The HTTP origin to talk to, derived from the stored host.

        A host given with a scheme is honored as given. Anything
        without one gets plain http. Port 80 is the only thing this
        firmware has ever served, so the port stays implicit.
        """
        if self._host.startswith(("http://", "https://")):
            return self._host
        return f"http://{self._host}"

    @property
    def connected(self) -> bool:
        """Mirror of the connect state, as the FakeConnection exposes it."""
        return self._connected

    def for_unit(self, unit_id: int) -> _WiLanHTTPUnit:
        """The unit wrapper for ``unit_id``, cached so identity stays stable."""
        unit = self._units.get(unit_id)
        if unit is None:
            unit = _WiLanHTTPUnit(self, unit_id)
            self._units[unit_id] = unit
        return unit

    async def connect(self) -> None:
        """Probe the relay with one short read so a dead datalogger fails fast.

        The modbus-connection contract expects connect to attempt
        something real: ``api.modbus_connect`` awaits it eagerly so a
        refused connect comes back as a transport error, not as an
        error inside the first scan. Here, connecting means the device
        answered a single two register read at SunSpec base address
        40000.
        """
        await self.exchange(1, FUNC_READ_HOLDING, 40000, 2)
        self._connected = True

    async def disconnect(self) -> None:
        """Reset the connected flag. Nothing else owns persistent state."""
        self._connected = False

    async def close(self) -> None:
        """Drop the HTTP session and the connected flag for good."""
        self._connected = False
        session, self._session = self._session, None
        if session is not None and not session.closed:
            await session.close()

    async def exchange(self, unit_id: int, function: int, address: int, count: int) -> list[int]:
        """Run one RTU transaction through the datalogger.

        One HTTP request per Modbus ADU, exactly as ``modbus_rw``
        expects. The reply frame is decoded and CRC verified inside
        :func:`_words_from_response`.
        """
        frame_hex = build_rtu_frame(unit_id, function, address, count)
        payload = await self._post_json({"cmd": frame_hex})

        errno = payload.get("errno")
        if errno == ERRNO_NO_DATA:
            # Coded error: carries Modbus exception code 2, so the scan
            # and the diagnostics dump report the real exception class.
            raise IllegalDataAddressError(message="no data from the device behind the datalogger")
        if errno == ERRNO_ID_NOT_FOUND:
            raise IllegalDataAddressError(message="id not found in the datalogger's registry")
        if errno != 0:
            raise ModbusConnectionError(
                f"datalogger refused the exchange (errno {errno}: {payload.get('errmsg', '?')})"
            )

        result = (payload.get("data") or {}).get("result")
        if not isinstance(result, str):
            raise ModbusConnectionError("datalogger reply carries no result frame")
        return _words_from_response(result, function, count, unit_id)

    async def _post_json(self, body: dict[str, Any]) -> dict[str, Any]:
        """The aiohttp leg: post one command envelope, return the decoded envelope.

        The datalogger answers ``Content-Type: text/html`` on this
        endpoint (the JSON body is not declared), so the decoder is
        handed an empty content-type override. Unicode decode failures
        and non-JSON bodies surface as ModbusConnectionError.
        """
        session = await self._get_session()
        url = f"{self.scheme_host}{MODBUS_RW_PATH}"
        try:
            async with session.post(url, json=body) as response:
                response.raise_for_status()
                payload = await response.json(content_type=None)
        except TimeoutError as err:
            raise ModbusTimeoutError(f"datalogger did not answer within {self._timeout}s") from err
        except aiohttp.ClientError as err:
            raise ModbusConnectionError(f"HTTP error talking to the datalogger: {err}") from err
        except ValueError as err:
            # aiohttp surfaces a body that will not parse as
            # json.JSONDecodeError, which subclasses ValueError.
            raise ModbusConnectionError(f"datalogger answered with non-JSON body: {err}") from err
        if not isinstance(payload, dict):
            raise ModbusConnectionError("datalogger answer was not a JSON object")
        return payload

    async def _get_session(self) -> aiohttp.ClientSession:
        """Create the session lazily so it forms on the event loop."""
        session = self._session
        if session is None or session.closed:
            session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._timeout),
                headers={"Content-Type": "application/json"},
            )
            self._session = session
        return session


__all__ = [
    "MODBUS_RW_PATH",
    "WiLanHTTPConnection",
    "build_rtu_frame",
    "crc16_modbus",
]
