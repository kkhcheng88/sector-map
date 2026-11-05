"""Daily adjusted-close relative momentum; EMA uses adjust=False."""
import numpy as np
import pandas as pd

UNIVERSE = ['XLK', 'XLF', 'XLE', 'XLY', 'XLP', 'XLV', 'XLI', 'XLB', 'XLU', 'XLRE', 'XLC']
TRAIL_LENGTHS = (10, 63, 126, 252, 504)


def empty_map(tickers=()):
    return {'as_of': None, 'benchmark': 'SPY', 'source': 'yfinance adjusted closes',
            'method': {'name': 'lo2cin4', 'h': 21, 's': 5, 'rms_window': 252, 'ema_adjust': False},
            'sessions': [], 'trails': {}, 'requested_tickers': list(tickers), 'excluded': {}}


def calculate(prices: pd.DataFrame, universe=None, trail_length=10) -> dict:
    if trail_length not in TRAIL_LENGTHS:
        raise ValueError('Unsupported trail length')
    universe = list(dict.fromkeys(UNIVERSE if universe is None else universe))
    if not universe:
        return empty_map()
    prices = prices.sort_index().reindex(columns=list(dict.fromkeys(['SPY', *universe])))
    prices = prices.replace([np.inf, -np.inf], np.nan).where(prices > 0)
    benchmark = prices.SPY.dropna()
    if len(benchmark) < 287:
        raise ValueError('At least 287 valid SPY adjusted-close sessions are required')
    sessions = benchmark.index[-trail_length:]

    def scale(series):
        rms = series.pow(2).rolling(252, min_periods=252).mean().pow(.5)
        ratio = series.div(rms.where(rms != 0)).where(rms != 0, 0)
        return 100 + 4 * np.tanh(ratio / 2)

    result = empty_map(universe)
    result.update(as_of=sessions[-1].strftime('%Y-%m-%d'),
                  sessions=[d.strftime('%Y-%m-%d') for d in sessions])
    for ticker in universe:
        # Independent warmup: a new/missing ticker cannot wipe out the entire group.
        pair = prices.loc[:, list(dict.fromkeys(['SPY', ticker]))].dropna()
        if pair.empty:
            result['excluded'][ticker] = 'No adjusted-close data returned by Yahoo Finance'
            continue
        relative = np.log(pair[ticker] / pair.SPY)
        trend = relative.diff(21).ewm(span=5, adjust=False, min_periods=5).mean()
        acceleration = trend.diff(5)
        x, y = scale(trend).reindex(sessions), scale(acceleration).reindex(sessions)
        valid = x.notna() & y.notna()
        if not valid.iloc[-10:].all():
            result['excluded'][ticker] = ('Needs about 291 valid daily sessions and data for '
                                           'all ten latest SPY sessions')
            continue
        # A shorter listing remains usable. Keep the continuous suffix only, so
        # missing sessions never become an invented daily step in a long trail.
        missing = np.flatnonzero(~valid.to_numpy())
        ticker_sessions = sessions[missing[-1] + 1:] if len(missing) else sessions
        result['trails'][ticker] = [
            {'date': d.strftime('%Y-%m-%d'), 'X': round(float(x.loc[d]), 6),
             'Y': round(float(y.loc[d]), 6)} for d in ticker_sessions]
    return result


def select_trails(data, trail_length=10):
    """Slice cached coordinates, without changing indicator warmup or the cache."""
    return {**data, 'trail_length': trail_length,
            'sessions': data['sessions'][-trail_length:],
            'history_available_sessions': {t: len(points) for t, points in data['trails'].items()},
            'trails': {t: points[-trail_length:] for t, points in data['trails'].items()}}
