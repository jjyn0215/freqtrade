"""
Tests for Upbit WebSocket candle client and UpbitExchangeWS adapter.
"""

import asyncio
import json
import threading
from time import sleep
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from freqtrade.enums import CandleType
from freqtrade.exchange.upbit import Upbit
from freqtrade.exchange.upbit_ws import UpbitWSClient


# ---------------------------------------------------------------------------
# Upbit exchange subclass helpers
# ---------------------------------------------------------------------------


class TestUpbitHelpers:
    def test_pair_to_upbit_code(self):
        assert Upbit.pair_to_upbit_code("BTC/KRW") == "KRW-BTC"
        assert Upbit.pair_to_upbit_code("ETH/KRW") == "KRW-ETH"
        assert Upbit.pair_to_upbit_code("XRP/BTC") == "BTC-XRP"

    def test_upbit_code_to_pair(self):
        assert Upbit.upbit_code_to_pair("KRW-BTC") == "BTC/KRW"
        assert Upbit.upbit_code_to_pair("KRW-ETH") == "ETH/KRW"
        assert Upbit.upbit_code_to_pair("BTC-XRP") == "XRP/BTC"

    def test_ws_timeframe_mapping(self):
        assert Upbit.ws_timeframe("1m") == "candle.1m"
        assert Upbit.ws_timeframe("5m") == "candle.5m"
        assert Upbit.ws_timeframe("1h") == "candle.60m"
        assert Upbit.ws_timeframe("4h") == "candle.240m"
        assert Upbit.ws_timeframe("1d") is None  # Unsupported
        assert Upbit.ws_timeframe("1w") is None  # Unsupported

    def test_ws_timeframe_roundtrip(self):
        """Ensure the mapping in Upbit and UpbitWSClient are consistent."""
        for ft_tf, upbit_type in Upbit._ws_timeframe_map.items():
            result = UpbitWSClient._msg_type_to_timeframe(upbit_type)
            assert result == ft_tf, f"Roundtrip failed for {ft_tf} -> {upbit_type} -> {result}"


# ---------------------------------------------------------------------------
# UpbitWSClient unit tests
# ---------------------------------------------------------------------------


class TestUpbitWSClient:
    def test_handle_message_valid_candle(self):
        """Verify a valid candle message is parsed and stored."""
        client = UpbitWSClient()
        msg = json.dumps(
            {
                "type": "candle.1m",
                "code": "KRW-BTC",
                "candle_date_time_utc": "2025-01-15T10:30:00",
                "opening_price": 50000000.0,
                "high_price": 50100000.0,
                "low_price": 49900000.0,
                "trade_price": 50050000.0,
                "candle_acc_trade_volume": 12.345,
                "timestamp": 1736935800000,
                "stream_type": "REALTIME",
            }
        )

        client._handle_message(msg)

        assert "BTC/KRW" in client.ohlcvs
        assert "1m" in client.ohlcvs["BTC/KRW"]
        candles = client.ohlcvs["BTC/KRW"]["1m"]
        assert len(candles) == 1
        row = candles[0]
        # Verify OHLCV fields
        assert row[1] == 50000000.0  # open
        assert row[2] == 50100000.0  # high
        assert row[3] == 49900000.0  # low
        assert row[4] == 50050000.0  # close
        assert row[5] == 12.345  # volume

    def test_handle_message_updates_existing_candle(self):
        """When a candle with the same timestamp arrives, it should be updated."""
        client = UpbitWSClient()
        base = {
            "type": "candle.1m",
            "code": "KRW-BTC",
            "candle_date_time_utc": "2025-01-15T10:30:00",
            "opening_price": 50000000.0,
            "high_price": 50100000.0,
            "low_price": 49900000.0,
            "trade_price": 50050000.0,
            "candle_acc_trade_volume": 10.0,
            "timestamp": 1736935800000,
            "stream_type": "REALTIME",
        }
        client._handle_message(json.dumps(base))

        # Same timestamp, updated volume
        base["candle_acc_trade_volume"] = 15.0
        base["trade_price"] = 50060000.0
        client._handle_message(json.dumps(base))

        candles = client.ohlcvs["BTC/KRW"]["1m"]
        assert len(candles) == 1
        assert candles[0][4] == 50060000.0
        assert candles[0][5] == 15.0

    def test_handle_message_multiple_candles_sorted(self):
        """Multiple candles should be stored in chronological order."""
        client = UpbitWSClient()
        for minute in [32, 30, 31]:
            msg = json.dumps(
                {
                    "type": "candle.1m",
                    "code": "KRW-BTC",
                    "candle_date_time_utc": f"2025-01-15T10:{minute:02d}:00",
                    "opening_price": 50000000.0,
                    "high_price": 50100000.0,
                    "low_price": 49900000.0,
                    "trade_price": 50050000.0,
                    "candle_acc_trade_volume": 1.0,
                    "timestamp": 1736935800000,
                    "stream_type": "REALTIME",
                }
            )
            client._handle_message(msg)

        candles = client.ohlcvs["BTC/KRW"]["1m"]
        assert len(candles) == 3
        # Check sorted order
        assert candles[0][0] < candles[1][0] < candles[2][0]

    def test_handle_message_60m_maps_to_1h(self):
        """candle.60m should be stored under timeframe '1h'."""
        client = UpbitWSClient()
        msg = json.dumps(
            {
                "type": "candle.60m",
                "code": "KRW-ETH",
                "candle_date_time_utc": "2025-01-15T10:00:00",
                "opening_price": 4000000.0,
                "high_price": 4010000.0,
                "low_price": 3990000.0,
                "trade_price": 4005000.0,
                "candle_acc_trade_volume": 100.0,
                "timestamp": 1736935800000,
                "stream_type": "REALTIME",
            }
        )
        client._handle_message(msg)

        assert "1h" in client.ohlcvs["ETH/KRW"]
        assert len(client.ohlcvs["ETH/KRW"]["1h"]) == 1

    def test_handle_message_ignores_non_candle(self):
        """Non-candle messages should be silently ignored."""
        client = UpbitWSClient()
        msg = json.dumps({"type": "ticker", "code": "KRW-BTC", "trade_price": 50000000})
        client._handle_message(msg)
        assert len(client.ohlcvs) == 0

    def test_handle_message_ignores_invalid_json(self):
        """Invalid JSON should not raise."""
        client = UpbitWSClient()
        client._handle_message("not json at all {{")
        assert len(client.ohlcvs) == 0

    def test_handle_message_ignores_missing_fields(self):
        """Candle message missing required fields should be ignored."""
        client = UpbitWSClient()
        msg = json.dumps({"type": "candle.1m", "code": "KRW-BTC"})  # no candle_date_time_utc
        client._handle_message(msg)
        assert len(client.ohlcvs) == 0

    def test_clear_cache(self):
        """clear_cache() should remove all stored data."""
        client = UpbitWSClient()
        msg = json.dumps(
            {
                "type": "candle.1m",
                "code": "KRW-BTC",
                "candle_date_time_utc": "2025-01-15T10:30:00",
                "opening_price": 50000000.0,
                "high_price": 50100000.0,
                "low_price": 49900000.0,
                "trade_price": 50050000.0,
                "candle_acc_trade_volume": 1.0,
                "timestamp": 1736935800000,
                "stream_type": "REALTIME",
            }
        )
        client._handle_message(msg)
        assert len(client.ohlcvs) > 0
        client.clear_cache()
        assert len(client.ohlcvs) == 0

    def test_upsert_candle_cache_limit(self):
        """Cache should not exceed MAX_CANDLE_CACHE entries."""
        from freqtrade.exchange.upbit_ws import MAX_CANDLE_CACHE

        client = UpbitWSClient()
        for i in range(MAX_CANDLE_CACHE + 50):
            msg = json.dumps(
                {
                    "type": "candle.1m",
                    "code": "KRW-BTC",
                    "candle_date_time_utc": f"2025-01-15T{(i // 60) % 24:02d}:{i % 60:02d}:00",
                    "opening_price": 50000000.0,
                    "high_price": 50100000.0,
                    "low_price": 49900000.0,
                    "trade_price": 50050000.0,
                    "candle_acc_trade_volume": 1.0,
                    "timestamp": 1736935800000 + i * 60000,
                    "stream_type": "REALTIME",
                }
            )
            client._handle_message(msg)

        candles = client.ohlcvs["BTC/KRW"]["1m"]
        assert len(candles) <= MAX_CANDLE_CACHE

    def test_msg_type_to_timeframe(self):
        assert UpbitWSClient._msg_type_to_timeframe("candle.1m") == "1m"
        assert UpbitWSClient._msg_type_to_timeframe("candle.60m") == "1h"
        assert UpbitWSClient._msg_type_to_timeframe("candle.240m") == "4h"
        assert UpbitWSClient._msg_type_to_timeframe("candle.999m") is None
        assert UpbitWSClient._msg_type_to_timeframe("ticker") is None


# ---------------------------------------------------------------------------
# UpbitExchangeWS adapter tests
# ---------------------------------------------------------------------------


def _patch_eventloop_threading(exchange_ws):
    """Utility to create a working event loop in a background thread."""
    init_event = threading.Event()

    def thread_func():
        exchange_ws._loop = asyncio.new_event_loop()
        init_event.set()
        exchange_ws._loop.run_forever()

    t = threading.Thread(target=thread_func, daemon=True)
    t.start()
    if not init_event.wait(timeout=5.0):
        raise RuntimeError("Failed to initialize event loop thread")


class TestUpbitExchangeWS:
    def test_init(self, mocker):
        from freqtrade.exchange.upbit_exchange_ws import UpbitExchangeWS

        config = MagicMock()
        ccxt_object = MagicMock()
        mocker.patch(
            "freqtrade.exchange.upbit_exchange_ws.UpbitExchangeWS._start_forever", MagicMock()
        )

        ws = UpbitExchangeWS(config, ccxt_object)
        sleep(0.1)

        assert ws.config == config
        assert ws._ccxt_object == ccxt_object
        assert ws._thread.name == "upbit_ws"
        assert hasattr(ws, "_upbit_ws")
        assert isinstance(ws._upbit_ws, UpbitWSClient)

        ws.cleanup()

    def test_ohlcvs_reads_from_upbit_cache(self, mocker):
        from freqtrade.exchange.upbit_exchange_ws import UpbitExchangeWS

        config = MagicMock()
        ccxt_object = MagicMock()
        mocker.patch(
            "freqtrade.exchange.upbit_exchange_ws.UpbitExchangeWS._start_forever", MagicMock()
        )

        ws = UpbitExchangeWS(config, ccxt_object)
        sleep(0.1)

        # Populate the Upbit cache directly
        test_candle = [1736935800000, 50000000, 50100000, 49900000, 50050000, 10.0]
        ws._upbit_ws.ohlcvs["BTC/KRW"]["1m"] = [test_candle]

        result = ws.ohlcvs("BTC/KRW", "1m")
        assert len(result) == 1
        assert result[0] == test_candle
        # Should be a copy, not the same list
        assert result is not ws._upbit_ws.ohlcvs["BTC/KRW"]["1m"]

        ws.cleanup()

    def test_schedule_ohlcv(self, mocker):
        from freqtrade.exchange.upbit_exchange_ws import UpbitExchangeWS

        config = MagicMock()
        ccxt_object = MagicMock()
        mocker.patch(
            "freqtrade.exchange.upbit_exchange_ws.UpbitExchangeWS._start_forever", MagicMock()
        )

        ws = UpbitExchangeWS(config, ccxt_object)
        _patch_eventloop_threading(ws)

        try:
            ws.schedule_ohlcv("BTC/KRW", "1m", CandleType.SPOT)
            sleep(0.2)

            assert ("BTC/KRW", "1m", CandleType.SPOT) in ws._klines_watching
            assert ("BTC/KRW", "1m", CandleType.SPOT) in ws.klines_last_request
        finally:
            ws.cleanup()

    def test_pop_history(self, mocker):
        from freqtrade.exchange.upbit_exchange_ws import UpbitExchangeWS

        config = MagicMock()
        ccxt_object = MagicMock()
        mocker.patch(
            "freqtrade.exchange.upbit_exchange_ws.UpbitExchangeWS._start_forever", MagicMock()
        )

        ws = UpbitExchangeWS(config, ccxt_object)
        sleep(0.1)

        # Set up cache data
        ws._upbit_ws.ohlcvs["BTC/KRW"]["1m"] = [[1, 2, 3, 4, 5, 6]]
        ws.klines_last_refresh[("BTC/KRW", "1m", CandleType.SPOT)] = 12345

        ws._pop_history(("BTC/KRW", "1m", CandleType.SPOT))

        assert "1m" not in ws._upbit_ws.ohlcvs.get("BTC/KRW", {})
        assert ("BTC/KRW", "1m", CandleType.SPOT) not in ws.klines_last_refresh

        ws.cleanup()


# ---------------------------------------------------------------------------
# subscribe / unsubscribe tests (w/ mocked aiohttp)
# ---------------------------------------------------------------------------


class TestUpbitWSClientSubscribe:
    @pytest.mark.asyncio
    async def test_subscribe_sends_message(self):
        client = UpbitWSClient()
        mock_ws = AsyncMock()
        mock_ws.closed = False
        client._ws = mock_ws
        client._running = True

        await client.subscribe("BTC/KRW", "1m")
        await asyncio.sleep(0.3)

        assert "candle.1m" in client._subscriptions
        assert "KRW-BTC" in client._subscriptions["candle.1m"]
        mock_ws.send_str.assert_called_once()

        # Verify the sent payload structure
        sent = json.loads(mock_ws.send_str.call_args[0][0])
        assert len(sent) == 3
        assert "ticket" in sent[0]
        assert sent[1]["type"] == "candle.1m"
        assert "KRW-BTC" in sent[1]["codes"]
        assert sent[2] == {"format": "DEFAULT"}

    @pytest.mark.asyncio
    async def test_subscribe_unsupported_timeframe(self):
        client = UpbitWSClient()
        mock_ws = AsyncMock()
        mock_ws.closed = False
        client._ws = mock_ws
        client._running = True

        await client.subscribe("BTC/KRW", "1d")  # 1d not supported

        assert len(client._subscriptions) == 0
        mock_ws.send_str.assert_not_called()

    @pytest.mark.asyncio
    async def test_unsubscribe_removes_code(self):
        client = UpbitWSClient()
        mock_ws = AsyncMock()
        mock_ws.closed = False
        client._ws = mock_ws
        client._running = True

        await client.subscribe("BTC/KRW", "1m")
        await client.subscribe("ETH/KRW", "1m")
        assert len(client._subscriptions["candle.1m"]) == 2

        await client.unsubscribe("BTC/KRW", "1m")
        assert "KRW-BTC" not in client._subscriptions.get("candle.1m", set())
        assert "KRW-ETH" in client._subscriptions["candle.1m"]
