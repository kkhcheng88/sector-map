import math
import unittest
import numpy as np
import pandas as pd
from app.rotation import calculate, select_trails, TRAIL_LENGTHS, UNIVERSE


class RotationFixture(unittest.TestCase):
    def test_independent_scalar_fixture(self):
        n = 500
        dates = pd.bdate_range('2023-01-02', periods=n)
        r = [.04 * math.sin(i / 31) + .0001 * i for i in range(n)]
        prices = pd.DataFrame({'SPY': np.full(n, 100.),
                               **{t: [100 * math.exp(v) for v in r] for t in UNIVERSE}}, index=dates)
        result = calculate(prices)
        # Scalar recurrence and RMS independently reproduce the fixture's last point.
        trend = [float('nan')] * n
        for i in range(21, n):
            m = r[i] - r[i-21]
            trend[i] = m if i == 21 else trend[i-1] * (2/3) + m / 3
        accel = [trend[i] - trend[i-5] if i >= 30 else float('nan') for i in range(n)]
        def expected(values):
            rms = math.sqrt(sum(v*v for v in values[-252:]) / 252)
            return 100 + 4 * math.tanh(values[-1] / (2*rms))
        point = result['trails']['XLK'][-1]
        self.assertAlmostEqual(point['X'], expected(trend), places=5)
        self.assertAlmostEqual(point['Y'], expected(accel), places=5)
        self.assertEqual(len(result['sessions']), 10)
        self.assertEqual(set(result['trails']), set(UNIVERSE))

    def test_flat_relative_prices_are_centered(self):
        prices = pd.DataFrame(100., index=pd.bdate_range('2020-01-01', periods=400), columns=['SPY', *UNIVERSE])
        self.assertEqual(calculate(prices)['trails']['XLF'][-1]['X'], 100)
        self.assertEqual(calculate(prices)['trails']['XLF'][-1]['Y'], 100)
        with self.assertRaises(ValueError):
            calculate(prices.iloc[:100])

    def test_custom_universe_uses_only_requested_tickers(self):
        prices = pd.DataFrame(100., index=pd.bdate_range('2020-01-01', periods=400),
                              columns=['SPY', 'IYW', 'IYF', 'XLK'])
        # An unselected ticker with no prices cannot affect the active group.
        prices['XLK'] = np.nan
        result = calculate(prices, ['IYF', 'IYW'])
        self.assertEqual(list(result['trails']), ['IYF', 'IYW'])
        self.assertEqual(result['benchmark'], 'SPY')
        self.assertEqual(len(result['sessions']), 10)
        for trail in result['trails'].values():
            self.assertEqual(len(trail), 10)
            self.assertTrue(all(point['X'] == 100 and point['Y'] == 100 for point in trail))

    def test_benchmark_may_also_be_a_group_ticker(self):
        prices = pd.DataFrame(100., index=pd.bdate_range('2020-01-01', periods=400),
                              columns=['SPY', 'IYW'])
        result = calculate(prices, ['SPY', 'IYW'])
        self.assertEqual(result['trails']['SPY'][-1]['X'], 100)
        self.assertEqual(result['trails']['SPY'][-1]['Y'], 100)

    def test_new_missing_or_stale_symbol_does_not_block_others(self):
        prices = pd.DataFrame(100., index=pd.bdate_range('2020-01-01', periods=400),
                              columns=['SPY', 'MAGS', 'AIPO', 'DRAM', 'OLD'])
        prices.loc[prices.index[:300], 'AIPO'] = np.nan
        prices['DRAM'] = np.nan
        prices.loc[prices.index[-1], 'OLD'] = np.nan
        result = calculate(prices, ['MAGS', 'AIPO', 'DRAM', 'OLD'])
        self.assertEqual(list(result['trails']), ['MAGS'])
        self.assertEqual(set(result['excluded']), {'AIPO', 'DRAM', 'OLD'})
        self.assertEqual(result['trails']['MAGS'][-1]['date'], result['as_of'])
        self.assertEqual(len(result['trails']['MAGS']), 10)

    def test_empty_universe_needs_no_prices(self):
        result = calculate(pd.DataFrame(), [])
        self.assertEqual(result['trails'], {})
        self.assertIsNone(result['as_of'])

    def test_long_windows_share_the_same_warmup_and_endpoint(self):
        dates = pd.bdate_range('2020-01-01', periods=1100)
        steps = np.arange(len(dates))
        prices = pd.DataFrame({'SPY':100., 'MAGS':100 * np.exp(.08 * np.sin(steps / 37))}, index=dates)
        cached = calculate(prices, ['MAGS'], trail_length=504)
        self.assertEqual(len(cached['trails']['MAGS']), 504)
        for length in TRAIL_LENGTHS:
            selected = select_trails(cached, length)
            independent = calculate(prices, ['MAGS'], trail_length=length)
            self.assertEqual(selected['trails'], independent['trails'])
            self.assertEqual(selected['sessions'], independent['sessions'])
            self.assertEqual(selected['trails']['MAGS'][-1], cached['trails']['MAGS'][-1])
        self.assertEqual(len(cached['trails']['MAGS']), 504)  # Selection cannot mutate cache.

    def test_short_listing_and_gap_keep_only_real_contiguous_history(self):
        dates = pd.bdate_range('2020-01-01', periods=900)
        prices = pd.DataFrame(100., index=dates, columns=['SPY','NEW','GAP'])
        prices.loc[dates[:500], 'NEW'] = np.nan
        prices.loc[dates[-30], 'GAP'] = np.nan
        result = calculate(prices, ['NEW','GAP'], trail_length=504)
        self.assertEqual(len(result['trails']['NEW']), 119)
        self.assertEqual(len(result['trails']['GAP']), 29)
        selected = select_trails(result, 504)
        self.assertEqual(selected['history_available_sessions'], {'NEW':119,'GAP':29})
        self.assertEqual(result['trails']['NEW'][-1]['date'], result['as_of'])


if __name__ == '__main__':
    unittest.main()
