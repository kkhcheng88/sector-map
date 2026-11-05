import math
import unittest

from app.patterns import analyze_trail, axis_crosses, generate_patterns


def circle(sign=1, radii=None):
    radii = radii or [2] * 65
    return [{'date': f'session-{i:03}',
             'X': 100 + radius * math.cos(sign * i * math.tau / 64 + .2),
             'Y': 100 + radius * math.sin(sign * i * math.tau / 64 + .2)}
            for i, radius in enumerate(radii)]


class PatternTests(unittest.TestCase):
    def test_known_clockwise_and_counterclockwise_loops(self):
        for sign, expected in [(1, 'counterclockwise'), (-1, 'clockwise')]:
            result = analyze_trail(circle(sign), 126)
            self.assertEqual(result['direction'], expected)
            self.assertEqual(result['net_turns'], sign)
            self.assertEqual(result['coherence'], 1)
            self.assertEqual(result['axis_crosses'], {'X': 2, 'Y': 2})
            self.assertEqual(sum(result['quadrant_sessions'].values()), 65)
            self.assertEqual(result['sessions'], 65)
            self.assertTrue(result['partial'])
            self.assertEqual(result['path_shape'], 'steady')

    def test_outward_inward_spirals_and_small_change(self):
        for radii, expected in [([1 + i / 64 for i in range(65)], 'expanding'),
                                ([2 - i / 64 for i in range(65)], 'tightening'),
                                ([2 + i / 640 for i in range(65)], 'steady')]:
            self.assertEqual(analyze_trail(circle(radii=radii), 63)['path_shape'], expected)

    def test_retraced_loop_is_mixed(self):
        loop = circle()
        result = analyze_trail(loop + list(reversed(loop[:-1])), 252)
        self.assertEqual(result['direction'], 'mixed')
        self.assertAlmostEqual(result['net_turns'], 0)

    def test_axis_touches_plateaus_and_crosses(self):
        for values, expected in [([99,100,100,99], 0), ([99,100,100,101], 1),
                                  ([99,101,99], 2), ([100,100,101], 0),
                                  ([100,100,100], 0), ([99,100.00000001,99], 0)]:
            self.assertEqual(axis_crosses([{'X':x} for x in values], 'X'), expected)

    def test_center_flat_and_ambiguous_cross_origin(self):
        for points in [[{'date':'flat', 'X':100,'Y':100}] * 10,
                       [{'date':'a', 'X':99,'Y':100}, {'date':'b', 'X':101,'Y':100}]]:
            result = analyze_trail(points, 10)
            self.assertEqual(result['direction'], 'no_clear_rotation')
            self.assertGreater(result['rotation_steps_ignored'], 0)
            self.assertEqual(result['net_turns'], 0)
        self.assertEqual(analyze_trail(points, 10)['path_shape'], 'insufficient_history')

    def test_quadrant_boundary_counts_and_single_point(self):
        points = [{'date':'a','X':100,'Y':100}, {'date':'b','X':99,'Y':100},
                  {'date':'c','X':99,'Y':99}, {'date':'d','X':100,'Y':99}]
        result = analyze_trail(points, 10)
        self.assertEqual(list(result['quadrant_sessions'].values()), [1,1,1,1])
        self.assertEqual(list(result['quadrant_percent'].values()), [25,25,25,25])
        self.assertEqual(analyze_trail(points[:1], 10)['direction'], 'no_clear_rotation')

    def test_deterministic_pasteable_notes_stale_partial_and_empty(self):
        data = {'group':{'name':'[Macro]*'}, 'trail_length':126,
                'trails':{'BBB':circle(-1), 'AAA':circle()}, 'excluded':{'NEW':'too short'},
                'as_of':'2026-11-04', 'stale':True, 'snapshot_id':'test-snapshot'}
        result = generate_patterns(data)
        self.assertEqual(result, generate_patterns({**data, 'trails':dict(reversed(list(data['trails'].items())))}))
        self.assertEqual(result['snapshot_id'], data['snapshot_id'])
        for text in [r'\[Macro\]\*', '順時針', '逆時針', '65/126', '上次成功數據', '唔代表落後', '唔係回報百分比']:
            self.assertIn(text, result['text'])
        self.assertIn('未有可用軌跡', generate_patterns({**data, 'trails':{}})['text'])


if __name__ == '__main__':
    unittest.main()
