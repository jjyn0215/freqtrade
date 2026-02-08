"""
Online integration test for Upbit WebSocket candle streams.

These tests make REAL network calls to the Upbit WebSocket API.
They should only be run manually or in CI with ``--longrun`` flag.
"""

import logging
from datetime import timedelta
from time import sleep

import pytest

from freqtrade.enums import CandleType
from freqtrade.exchange.upbit import Upbit
from freqtrade.exchange.upbit_exchange_ws import UpbitExchangeWS
from freqtrade.exchange.upbit_ws import UpbitWSClient


@pytest.mark.longrun
@pytest.mark.timeout(120)
class TestUpbitWSOnline:
    """
    Online connectivity tests for the Upbit WebSocket candle stream.
    These do NOT require API keys (Upbit WS candles are public).
    """

    def test_upbit_ws_client_receives_candles(self):
        """
        Connect to Upbit WS, subscribe to KRW-BTC 1m candles,
        and verify that at least one candle is received within 30 seconds.
        """
        import asyncio

        async def _run():
            client = UpbitWSClient()
            try:
                await client.connect()
                await client.subscribe("BTC/KRW", "1m")

                # Wait up to 30 seconds for at least one candle to appear
                for _ in range(60):
                    candles = client.ohlcvs.get("BTC/KRW", {}).get("1m", [])
                    if candles:
                        break
                    await asyncio.sleep(0.5)

                candles = client.ohlcvs.get("BTC/KRW", {}).get("1m", [])
                assert len(candles) > 0, "No candles received from Upbit WS within 30s"

                row = candles[-1]
                assert len(row) == 6, "OHLCV row should have 6 elements"
                assert row[0] > 0, "Timestamp should be positive"
                assert row[1] > 0, "Open price should be positive"
                assert row[4] > 0, "Close price should be positive"
                assert row[5] >= 0, "Volume should be non-negative"

            finally:
                await client.close()

        asyncio.run(_run())

    def test_upbit_ws_multiple_pairs(self):
        """
        Subscribe to multiple pairs and verify all receive data.
        """
        import asyncio

        async def _run():
            client = UpbitWSClient()
            pairs = ["BTC/KRW", "ETH/KRW"]
            try:
                await client.connect()
                for pair in pairs:
                    await client.subscribe(pair, "1m")

                # Wait up to 30 seconds for candles from both pairs
                for _ in range(60):
                    all_received = all(
                        len(client.ohlcvs.get(p, {}).get("1m", [])) > 0 for p in pairs
                    )
                    if all_received:
                        break
                    await asyncio.sleep(0.5)

                for pair in pairs:
                    candles = client.ohlcvs.get(pair, {}).get("1m", [])
                    assert len(candles) > 0, f"No candles received for {pair}"

            finally:
                await client.close()

        asyncio.run(_run())

    def test_upbit_ws_reconnect_after_close(self):
        """
        Verify the client can reconnect after the connection is closed.
        """
        import asyncio

        async def _run():
            client = UpbitWSClient()
            try:
                await client.connect()
                await client.subscribe("BTC/KRW", "1m")

                # Wait for initial data
                for _ in range(30):
                    if client.ohlcvs.get("BTC/KRW", {}).get("1m", []):
                        break
                    await asyncio.sleep(0.5)

                assert len(client.ohlcvs.get("BTC/KRW", {}).get("1m", [])) > 0

                # Force close and clear cache
                if client._ws and not client._ws.closed:
                    await client._ws.close()
                client.clear_cache()

                # Wait for reconnect and new data
                for _ in range(60):
                    if client.ohlcvs.get("BTC/KRW", {}).get("1m", []):
                        break
                    await asyncio.sleep(0.5)

                candles = client.ohlcvs.get("BTC/KRW", {}).get("1m", [])
                assert len(candles) > 0, "No candles after reconnect"

            finally:
                await client.close()

        asyncio.run(_run())
