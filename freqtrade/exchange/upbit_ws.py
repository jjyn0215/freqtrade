"""
Upbit native WebSocket candle client.

Connects to Upbit's WebSocket API (wss://api.upbit.com/websocket/v1)
and subscribes to candle streams for requested pairs/timeframes.
Normalises incoming candle data into ccxt-compatible OHLCV format
and stores them in a dict compatible with ccxt's ``ohlcvs`` cache.

Reference: https://docs.upbit.com/reference/websocket-candle
"""

import asyncio
import json
import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime

import aiohttp


logger = logging.getLogger(__name__)

UPBIT_WS_URL = "wss://api.upbit.com/websocket/v1"

# Maximum number of candles to keep per pair/timeframe in the local cache.
MAX_CANDLE_CACHE = 500

# Mapping from freqtrade timeframe strings to Upbit WS candle types
WS_TIMEFRAME_MAP: dict[str, str] = {
    "1s": "candle.1s",
    "1m": "candle.1m",
    "3m": "candle.3m",
    "5m": "candle.5m",
    "10m": "candle.10m",
    "15m": "candle.15m",
    "30m": "candle.30m",
    "1h": "candle.60m",
    "4h": "candle.240m",
}


class UpbitWSClient:
    """
    Low-level Upbit WebSocket candle client.

    Responsibilities:
      * Maintain a persistent WS connection with automatic reconnect.
      * Subscribe / unsubscribe pairs + timeframes.
      * Parse incoming JSON candle messages and upsert them into an
        ``ohlcvs`` dict that mirrors the ccxt cache layout:
            ``ohlcvs[pair][timeframe]`` → ``list[list]``  (OHLCV rows)
        Each OHLCV row: ``[timestamp_ms, open, high, low, close, volume]``
    """

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._subscriptions: dict[str, set[str]] = defaultdict(set)
        # pair -> timeframe -> [[ts, o, h, l, c, v], ...]
        self.ohlcvs: dict[str, dict[str, list[list]]] = defaultdict(lambda: defaultdict(list))
        self._running = False
        self._recv_task: asyncio.Task | None = None
        self._reconnect_delay = 1.0  # seconds, doubles on consecutive failures
        self._connected = asyncio.Event()
        self._subscribe_task: asyncio.Task | None = None
        self._subscribe_delay = 0.2

    @property
    def is_running(self) -> bool:
        """Whether the WS client receive loop is active."""
        return self._running

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Establish the WS connection and start the receiver loop."""
        if self._running:
            return
        self._running = True
        self._connected.clear()
        self._session = aiohttp.ClientSession()
        await self._open_ws()
        self._recv_task = asyncio.create_task(self._recv_loop())

    async def close(self) -> None:
        """Gracefully shut down the WS connection."""
        self._running = False
        self._connected.clear()
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()
        self._ws = None
        self._session = None

    async def subscribe(self, pair: str, timeframe: str) -> None:
        """
        Subscribe to candle updates for *pair* / *timeframe*.

        :param pair: ccxt-style pair, e.g. ``'BTC/KRW'``
        :param timeframe: freqtrade timeframe, e.g. ``'1m'``
        """
        upbit_type = self._ws_timeframe(timeframe)
        if upbit_type is None:
            logger.warning(f"Timeframe {timeframe} not supported by Upbit WS – skipping.")
            return

        code = self._pair_to_upbit_code(pair)
        key = upbit_type  # e.g. "candle.1m"

        if code in self._subscriptions.get(key, set()):
            return  # already subscribed

        self._subscriptions[key].add(code)
        self._schedule_subscribe_send()

    async def unsubscribe(self, pair: str, timeframe: str) -> None:
        """Remove a subscription (best effort – Upbit WS doesn't support
        selective unsubscribe, so we re-send the full subscription set)."""
        upbit_type = self._ws_timeframe(timeframe)
        if upbit_type is None:
            return
        code = self._pair_to_upbit_code(pair)
        codes = self._subscriptions.get(upbit_type, set())
        codes.discard(code)
        if not codes:
            self._subscriptions.pop(upbit_type, None)
        # Re-send full subscription (or close & re-open if nothing left)
        if self._subscriptions:
            self._schedule_subscribe_send()

    def clear_cache(self) -> None:
        """Clear all cached OHLCV data."""
        self.ohlcvs.clear()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _open_ws(self) -> None:
        """Open (or re-open) the WebSocket connection."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        try:
            self._ws = await self._session.ws_connect(
                UPBIT_WS_URL,
                heartbeat=30,
                timeout=aiohttp.ClientWSTimeout(ws_close=10),
            )
            logger.info("Upbit WS connected.")
            self._reconnect_delay = 1.0
            self._connected.set()
            # Re-send active subscriptions after (re-)connect
            if self._subscriptions:
                await self._send_subscribe()
        except Exception:
            logger.exception("Failed to connect to Upbit WS")
            raise

    async def _send_subscribe(self) -> None:
        """
        Send the full subscription message to Upbit WS.

        Upbit expects a JSON array:
        [
          {"ticket": "<uuid>"},
          {"type": "candle.1m", "codes": ["KRW-BTC", ...]},
          ...
          {"format": "DEFAULT"}
        ]
        """
        if not self._ws or self._ws.closed:
            return

        payload: list[dict] = [{"ticket": str(uuid.uuid4())}]
        for candle_type, codes in self._subscriptions.items():
            if codes:
                payload.append({
                    "type": candle_type,
                    "codes": sorted(codes),
                    "is_only_realtime": False,  # get snapshot first, then realtime
                })
        payload.append({"format": "DEFAULT"})

        msg = json.dumps(payload)
        logger.debug(f"Upbit WS subscribe: {msg}")
        await self._ws.send_str(msg)

    async def _recv_loop(self) -> None:
        """
        Main receive loop – reads messages, handles reconnect on failure.
        """
        while self._running:
            try:
                if self._ws is None or self._ws.closed:
                    await self._reconnect()
                    continue

                msg = await self._ws.receive(timeout=60)

                if msg.type == aiohttp.WSMsgType.TEXT:
                    self._handle_message(msg.data)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    # Upbit may send binary (gzip) – decode as utf-8 text
                    self._handle_message(msg.data.decode("utf-8", errors="replace"))
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.ERROR,
                ):
                    logger.warning(f"Upbit WS connection issue: {msg.type}")
                    await self._reconnect()

            except asyncio.TimeoutError:
                # No message received within timeout – send ping or reconnect
                logger.debug("Upbit WS recv timeout – will ping/reconnect")
                if self._ws and not self._ws.closed:
                    try:
                        await self._ws.ping()
                    except Exception:
                        await self._reconnect()
                else:
                    await self._reconnect()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Upbit WS recv_loop error")
                await self._reconnect()

    async def _reconnect(self) -> None:
        """Close current connection and attempt to re-open after a delay."""
        if not self._running:
            return
        logger.info(f"Upbit WS reconnecting in {self._reconnect_delay:.1f}s ...")
        self._connected.clear()
        try:
            if self._ws and not self._ws.closed:
                await self._ws.close()
        except Exception:
            pass
        await asyncio.sleep(self._reconnect_delay)
        self._reconnect_delay = min(self._reconnect_delay * 2, 60.0)
        try:
            await self._open_ws()
        except Exception:
            logger.exception("Upbit WS reconnect failed")

    def _schedule_subscribe_send(self) -> None:
        """Schedule a debounced subscription update to avoid spamming the server."""
        if self._subscribe_task and not self._subscribe_task.done():
            return
        if not self._running:
            return
        self._subscribe_task = asyncio.create_task(self._delayed_send_subscribe())

    async def _delayed_send_subscribe(self) -> None:
        try:
            await asyncio.sleep(self._subscribe_delay)
            if self._subscriptions and await self._wait_until_connected():
                await self._send_subscribe()
        except asyncio.CancelledError:
            pass
        finally:
            self._subscribe_task = None

    async def _wait_until_connected(self) -> bool:
        """Wait for a usable connection before sending subscriptions."""
        if self._ws and not self._ws.closed:
            return True
        if not self._running:
            return False
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=10.0)
            return True
        except TimeoutError:
            logger.warning("Upbit WS connect timeout - subscription skipped")
            return False

    # ------------------------------------------------------------------
    # Message parsing
    # ------------------------------------------------------------------

    def _handle_message(self, raw: str) -> None:
        """Parse a single Upbit WS candle message and upsert into cache."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug(f"Upbit WS non-JSON message: {raw[:200]}")
            return

        if not isinstance(data, dict):
            return

        msg_type = data.get("type", "")
        if not msg_type.startswith("candle."):
            return

        code = data.get("code")  # e.g. "KRW-BTC"
        if not code:
            return

        # Parse candle_date_time_utc → ms timestamp
        candle_dt_str = data.get("candle_date_time_utc")
        if not candle_dt_str:
            return

        try:
            candle_dt = datetime.strptime(candle_dt_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
            candle_ts = int(candle_dt.timestamp() * 1000)
        except (ValueError, OSError):
            logger.debug(f"Failed to parse candle_date_time_utc: {candle_dt_str}")
            return

        open_price = float(data.get("opening_price", 0))
        high_price = float(data.get("high_price", 0))
        low_price = float(data.get("low_price", 0))
        close_price = float(data.get("trade_price", 0))
        volume = float(data.get("candle_acc_trade_volume", 0))

        ohlcv_row = [candle_ts, open_price, high_price, low_price, close_price, volume]

        # Convert Upbit code to ccxt pair
        pair = self._upbit_code_to_pair(code)

        # Derive timeframe string from msg_type (e.g. "candle.1m" → "1m", "candle.60m" → "1h")
        timeframe = self._msg_type_to_timeframe(msg_type)
        if not timeframe:
            return

        # Upsert into cache
        candles = self.ohlcvs[pair][timeframe]
        self._upsert_candle(candles, ohlcv_row)

    @staticmethod
    def _msg_type_to_timeframe(msg_type: str) -> str | None:
        """
        Convert an Upbit WS message type to a freqtrade timeframe string.
        e.g. ``'candle.1m'`` → ``'1m'``, ``'candle.60m'`` → ``'1h'``.
        """
        mapping = {
            "candle.1s": "1s",
            "candle.1m": "1m",
            "candle.3m": "3m",
            "candle.5m": "5m",
            "candle.10m": "10m",
            "candle.15m": "15m",
            "candle.30m": "30m",
            "candle.60m": "1h",
            "candle.240m": "4h",
        }
        return mapping.get(msg_type)

    @staticmethod
    def _ws_timeframe(timeframe: str) -> str | None:
        """Convert freqtrade timeframe to Upbit WS candle type string."""
        return WS_TIMEFRAME_MAP.get(timeframe)

    @staticmethod
    def _pair_to_upbit_code(pair: str) -> str:
        """Convert a ccxt-style pair (e.g. 'BTC/KRW') to Upbit market code (e.g. 'KRW-BTC')."""
        parts = pair.split("/")
        if len(parts) == 2:
            return f"{parts[1]}-{parts[0]}"
        return pair

    @staticmethod
    def _upbit_code_to_pair(code: str) -> str:
        """Convert an Upbit market code (e.g. 'KRW-BTC') to a ccxt-style pair (e.g. 'BTC/KRW')."""
        parts = code.split("-")
        if len(parts) == 2:
            return f"{parts[1]}/{parts[0]}"
        return code

    @staticmethod
    def _upsert_candle(candles: list[list], new_row: list) -> None:
        """
        Insert or update a candle row in the sorted candle list.
        If a candle with the same timestamp exists, replace it (latest data wins).
        Otherwise insert in sorted position.  Trim to MAX_CANDLE_CACHE.
        """
        ts = new_row[0]
        # Search from the end (most likely to match recent candles)
        for i in range(len(candles) - 1, -1, -1):
            if candles[i][0] == ts:
                candles[i] = new_row
                return
            if candles[i][0] < ts:
                candles.insert(i + 1, new_row)
                # Trim
                if len(candles) > MAX_CANDLE_CACHE:
                    del candles[: len(candles) - MAX_CANDLE_CACHE]
                return
        # Oldest candle – insert at position 0
        candles.insert(0, new_row)
        if len(candles) > MAX_CANDLE_CACHE:
            del candles[: len(candles) - MAX_CANDLE_CACHE]
