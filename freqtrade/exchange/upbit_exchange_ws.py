"""
Upbit-specific ExchangeWS adapter.

Subclasses ``ExchangeWS`` and replaces the ccxt.pro ``watch_ohlcv`` path
with native Upbit WebSocket candle streams via ``UpbitWSClient``.

The local cache (``UpbitWSClient.ohlcvs``) mirrors the structure of
``ccxt_object.ohlcvs`` so that ``ExchangeWS.ohlcvs()`` and
``ExchangeWS.get_ohlcv()`` work transparently.
"""

import asyncio
import logging
from copy import deepcopy

import ccxt

from freqtrade.constants import Config, PairWithTimeframe
from freqtrade.enums.candletype import CandleType
from freqtrade.exceptions import TemporaryError
from freqtrade.exchange.common import retrier
from freqtrade.exchange.exchange_ws import ExchangeWS
from freqtrade.exchange.upbit_ws import UpbitWSClient
from freqtrade.util import dt_ts


logger = logging.getLogger(__name__)


class UpbitExchangeWS(ExchangeWS):
    """
    ExchangeWS variant that uses the native Upbit WebSocket for candle data
    instead of ccxt.pro's ``watch_ohlcv``.
    """

    def __init__(self, config: Config, ccxt_object: ccxt.Exchange) -> None:
        # Do NOT call super().__init__ – we replicate the minimal setup
        # because the parent starts a thread that calls ccxt watch_ohlcv.
        self.config = config
        self._ccxt_object = ccxt_object
        self._background_tasks: set[asyncio.Task] = set()

        self._klines_watching: set[PairWithTimeframe] = set()
        self._klines_scheduled: set[PairWithTimeframe] = set()
        self.klines_last_refresh: dict[PairWithTimeframe, float] = {}
        self.klines_last_request: dict[PairWithTimeframe, float] = {}

        # Our custom Upbit WS client
        self._upbit_ws = UpbitWSClient()

        # Start a dedicated event loop thread (same pattern as parent)
        from threading import Thread

        self._thread = Thread(name="upbit_ws", target=self._start_forever)
        self._thread.start()
        # Match parent cleanup flag name to avoid AttributeError in cleanup/reset
        self._ExchangeWS__cleanup_called = False

    # ------------------------------------------------------------------
    # Overrides
    # ------------------------------------------------------------------

    @retrier(retries=3)
    def ohlcvs(self, pair: str, timeframe: str) -> list[list]:
        """
        Return a copy of cached OHLCV data for *pair* / *timeframe*.
        Reads from the Upbit WS client cache instead of ccxt's cache.
        """
        try:
            return deepcopy(self._upbit_ws.ohlcvs.get(pair, {}).get(timeframe, []))
        except RuntimeError as e:
            raise TemporaryError(f"Error deepcopying: {e}") from e

    async def _cleanup_async(self) -> None:
        """Close the Upbit WS client."""
        try:
            await self._upbit_ws.close()
            self._upbit_ws.clear_cache()
        except Exception:
            logger.exception("Exception in _cleanup_async (Upbit)")
        finally:
            self._ExchangeWS__cleanup_called = True

    def _pop_history(self, paircomb: PairWithTimeframe) -> None:
        """Remove history for a pair/timeframe combination from Upbit cache."""
        pair, timeframe, _ = paircomb
        pair_cache = self._upbit_ws.ohlcvs.get(pair, {})
        pair_cache.pop(timeframe, None)
        self.klines_last_refresh.pop(paircomb, None)

    async def _continuously_async_watch_ohlcv(
        self, pair: str, timeframe: str, candle_type: CandleType
    ) -> None:
        """
        Subscribe to Upbit WS candle stream for *pair* / *timeframe*
        and keep updating ``klines_last_refresh`` while the subscription
        is active.
        """
        try:
            # Ensure the WS client is connected
            if not self._upbit_ws.is_running:
                await self._upbit_ws.connect()

            await self._upbit_ws.subscribe(pair, timeframe)
            logger.info(f"Upbit WS subscribed: {pair}, {timeframe}")

            while (pair, timeframe, candle_type) in self._klines_watching:
                # Poll the cache – candles are updated asynchronously by _recv_loop
                candles = self._upbit_ws.ohlcvs.get(pair, {}).get(timeframe, [])
                if candles:
                    self.klines_last_refresh[(pair, timeframe, candle_type)] = dt_ts()
                await asyncio.sleep(1.0)

        except asyncio.CancelledError:
            logger.debug(f"Upbit WS watch cancelled for {pair}, {timeframe}")
        except Exception:
            logger.exception(f"Exception in Upbit WS watch for {pair}, {timeframe}")
        finally:
            self._klines_watching.discard((pair, timeframe, candle_type))
            try:
                await self._upbit_ws.unsubscribe(pair, timeframe)
            except Exception:
                pass

    async def _unwatch_ohlcv(self, pair: str, timeframe: str, candle_type: CandleType) -> None:
        """Unsubscribe from Upbit WS candle stream."""
        try:
            await self._upbit_ws.unsubscribe(pair, timeframe)
        except Exception:
            logger.exception("Exception in _unwatch_ohlcv (Upbit)")
