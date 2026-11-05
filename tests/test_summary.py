import unittest

from app.summary import generate_summary, quadrant


def point(x, y, date='2026-11-04'):
    return {'date':date, 'X':x, 'Y':y}


def snapshot(trails=None):
    return {'group':{'id':'macro', 'name':'Macro', 'tickers':list(trails or {})},
            'as_of':'2026-11-04', 'trails':trails or {}, 'excluded':{},
            'config_revision':1, 'stale':False, 'last_success':'2026-11-05T00:00:00Z'}


class SummaryTests(unittest.TestCase):
    def test_boundary_rules(self):
        for x, y, expected in [(100,100,'Leading'), (99,100,'Improving'),
                                (99,99,'Lagging'), (100,99,'Weakening')]:
            self.assertEqual(quadrant(point(x,y)), expected)

    def test_all_quadrants_and_crossing_vs_previous_point(self):
        data = snapshot({'MAGS':[point(99,101), point(101,102)],
                         'AIPO':[point(98,100), point(99,101)],
                         'DRAM':[point(98,98), point(97,97)],
                         'XLK':[point(102,99), point(101,98)]})
        result = generate_summary(data)
        self.assertEqual(result['quadrants'], {'Leading':['MAGS'], 'Improving':['AIPO'],
                                             'Lagging':['DRAM'], 'Weakening':['XLK']})
        for text in ['領先', '改善', '落後', '轉弱', '改善 → 領先', '強弱 X +2.00', '加速 Y +1.00']:
            self.assertIn(text, result['text'])
        self.assertIn('👉 先留意 MAGS', result['text'])
        self.assertEqual(result['text'].count('👉'), 1)

    def test_deterministic_regardless_of_input_order(self):
        data = snapshot({'BBB':[point(99,99),point(100,101)],
                         'AAA':[point(99,99),point(100,101)]})
        other = {**data, 'trails':dict(reversed(list(data['trails'].items())))}
        self.assertEqual(generate_summary(data), generate_summary(other))
        self.assertIn('👉 先留意 AAA', generate_summary(data)['text'])

    def test_empty_missing_and_stale_are_not_mislabeled_as_laggards(self):
        data = snapshot()
        data.update(as_of=None, excluded={'DRAM':'Insufficient history'}, stale=True)
        text = generate_summary(data)['text']
        self.assertIn('上次成功數據', text)
        self.assertIn('未有可用', text)
        self.assertIn('唔代表落後', text)
        self.assertNotIn('落後：DRAM', text)
        self.assertEqual(text.count('👉'), 1)

    def test_previous_point_means_penultimate_not_start_of_trail(self):
        data = snapshot({'MAGS':[point(97,97), point(101,101), point(102,102)]})
        text = generate_summary(data)['text']
        self.assertIn('強弱 X +1.00', text)
        self.assertIn('仍屬領先', text)
        self.assertNotIn('落後 → 領先', text)

    def test_weakening_group_has_observe_call(self):
        text = generate_summary(snapshot({'MAGS':[point(102,99),point(101,98)]}))['text']
        self.assertIn('👉 暫時先觀察', text)

    def test_single_point_and_markdown_name(self):
        data = snapshot({'MAGS':[point(102,103)]})
        data['group']['name'] = '[Macro]*'
        text = generate_summary(data)['text']
        self.assertIn(r'\[Macro\]\*', text)
        self.assertNotIn('較軌跡上一點', text)
        self.assertEqual(text.count('👉'), 1)
