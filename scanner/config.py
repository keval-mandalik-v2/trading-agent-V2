"""
Every tunable number in one place, so that changing the strategy is a visible diff.

Times are IST candle START labels, matching what nse_chart_data.py returns and what
the NSE charting Table view displays.
"""

from __future__ import annotations

from datetime import timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
CANDLE_MINUTES = 15

# --- the pattern (the strategy as taught; see CONTEXT.md for the vocabulary) -----
CANDLE_1 = "09:15"
COIL_TIMES = ("09:30", "09:45", "10:00", "10:15")
BREAKOUT_WINDOW = (
    "10:30", "10:45", "11:00", "11:15", "11:30",
    "11:45", "12:00", "12:15", "12:30", "12:45", "13:00",
)
SESSION_CANDLES = BREAKOUT_WINDOW + (
    "13:15", "13:30", "13:45", "14:00", "14:15",
    "14:30", "14:45", "15:00", "15:15",
)
# MIS positions are force-closed around 15:20; the 15:15 candle OPEN is the closest
# price in 15m data to that moment.
SQUARE_OFF_CANDLE = "15:15"

TARGET_PCT = 1.0

# --- universe (ADR-0001) --------------------------------------------------------
UNIVERSE_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv"
UNIVERSE_NAME = "NIFTY 50"

# --- money ----------------------------------------------------------------------
EQUITY = 20000.0
MAX_BUYING_POWER = 100000.0     # 5x MIS on EQUITY
DEFAULT_RISK_PCT = 1.0          # ADR-0001 amendment: risk-based sizing is the default

# --- the lab --------------------------------------------------------------------
BASE_RATE_MIN_N = 20            # below this a Base Rate reads "insufficient data" (ADR-0002)
DECIDE_BY_TRADES = 100          # ADR-0006 first checkpoint
EXTEND_TO_TRADES = 250          # ADR-0006 second and final checkpoint

# --- paths ----------------------------------------------------------------------
DATA_DIR = "data"
CANDLE_DIR = "data/candles"
SIGNALS_CSV = "data/signals.csv"
PICKS_CSV = "data/picks.csv"
STATE_JSON = "data/state.json"
UNIVERSE_CSV = "data/universe.csv"
RESEARCH_LOG = "docs/research-log.md"
