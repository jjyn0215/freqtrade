"""Upbit exchange subclass"""

import logging

from freqtrade.enums import MarginMode, TradingMode
from freqtrade.exchange.exchange import Exchange
from freqtrade.exchange.exchange_types import FtHas


logger = logging.getLogger(__name__)


class Upbit(Exchange):
    """Upbit exchange class.
    Contains adjustments needed for Freqtrade to work with this exchange.

    Upbit does not support watchOHLCV in ccxt/ccxt.pro, so we override
    exchange_has_overrides to enable our custom websocket candle integration.
    """

    _ft_has: FtHas = {
        "ohlcv_candle_limit": 200,
        "ohlcv_has_history": True,
        "trades_has_history": False,
        "order_time_in_force": ["GTC", "IOC"],
        "exchange_has_overrides": {
            "watchOHLCV": True,  # We implement this via custom Upbit WS
        },
        "ws_enabled": True,
    }

    _supported_trading_mode_margin_pairs: list[tuple[TradingMode, MarginMode]] = [
        (TradingMode.SPOT, MarginMode.NONE),
    ]

    # Mapping from freqtrade timeframe strings to Upbit WS candle types
    _ws_timeframe_map: dict[str, str] = {
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

    @classmethod
    def ws_timeframe(cls, timeframe: str) -> str | None:
        """
        Convert a freqtrade timeframe string to an Upbit WS candle type string.
        Returns None if the timeframe is not supported via Upbit WS.
        """
        return cls._ws_timeframe_map.get(timeframe)

    @classmethod
    def pair_to_upbit_code(cls, pair: str) -> str:
        """
        Convert a ccxt-style pair (e.g. 'BTC/KRW') to Upbit market code (e.g. 'KRW-BTC').
        """
        parts = pair.split("/")
        if len(parts) == 2:
            return f"{parts[1]}-{parts[0]}"
        return pair

    @classmethod
    def upbit_code_to_pair(cls, code: str) -> str:
        """
        Convert an Upbit market code (e.g. 'KRW-BTC') to a ccxt-style pair (e.g. 'BTC/KRW').
        """
        parts = code.split("-")
        if len(parts) == 2:
            return f"{parts[1]}/{parts[0]}"
        return code
