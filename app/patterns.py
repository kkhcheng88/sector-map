"""Descriptive, deterministic geometry of a selected daily rotation trail. No forecast."""
import math
import re
from statistics import median

from app.summary import QUADRANTS, quadrant

CENTER_RADIUS = .1
MIN_TURNS = .125
MIN_COHERENCE = .6


def axis_crosses(points, axis):
    # Touching the axis and returning to the same side is not a cross.
    # Runs on the axis count once only when they emerge on the opposite side.
    signs = [1 if p[axis] > 100 + 1e-6 else -1 if p[axis] < 100 - 1e-6 else 0
             for p in points]
    sides = [sign for sign in signs if sign]
    return sum(a != b for a, b in zip(sides, sides[1:]))


def analyze_trail(points, requested_sessions):
    vectors = [(p['X'] - 100, p['Y'] - 100) for p in points]
    radii = [math.hypot(x, y) for x, y in vectors]
    angles, skipped = [], 0
    for (ax, ay), (bx, by) in zip(vectors, vectors[1:]):
        dx, dy = bx - ax, by - ay
        squared = dx * dx + dy * dy
        t = min(1, max(0, -(ax * dx + ay * dy) / squared)) if squared else 0
        # A segment passing close to the origin has an ambiguous rotation angle.
        if math.hypot(ax + t * dx, ay + t * dy) <= CENTER_RADIUS:
            skipped += 1
            continue
        angles.append(math.atan2(ax * by - ay * bx, ax * bx + ay * by))
    net = sum(angles) / math.tau
    travel = sum(abs(a) for a in angles) / math.tau
    coherence = abs(net) / travel if travel > 1e-12 else 0
    if travel < MIN_TURNS:
        direction = 'no_clear_rotation'
    elif abs(net) >= MIN_TURNS and coherence >= MIN_COHERENCE:
        direction = 'counterclockwise' if net > 0 else 'clockwise'
    else:
        direction = 'mixed'
    counts = {q: 0 for q in QUADRANTS}
    for p in points:
        counts[quadrant(p)] += 1
    third = max(1, len(points) // 3)
    first, last = median(radii[:third]), median(radii[-third:])
    threshold = max(.1, first * .15)
    radial_change = last - first
    shape = ('expanding' if radial_change >= threshold else
             'tightening' if radial_change <= -threshold else 'steady')
    if len(points) < 6:
        shape = 'insufficient_history'
    return {
        'sessions': len(points), 'requested_sessions': requested_sessions,
        'partial': len(points) < requested_sessions,
        'start': points[0]['date'], 'end': points[-1]['date'],
        'current_quadrant': quadrant(points[-1]),
        'direction': direction, 'net_turns': round(net, 4),
        'angular_travel_turns': round(travel, 4), 'coherence': round(coherence, 4),
        'rotation_steps_ignored': skipped,
        'quadrant_sessions': counts,
        'quadrant_percent': {q: round(n / len(points) * 100, 1) for q, n in counts.items()},
        'axis_crosses': {axis: axis_crosses(points, axis) for axis in ('X', 'Y')},
        'path_shape': shape,
        'radius_first_third': round(first, 4), 'radius_last_third': round(last, 4),
    }


def cantonese_note(ticker, row):
    direction = {
        'clockwise': '整段偏向順時針轉', 'counterclockwise': '整段偏向逆時針轉',
        'mixed': '轉向反覆，未有單一方向', 'no_clear_rotation': '未見清晰繞中心旋轉',
    }[row['direction']]
    shape = {
        'expanding': '條線越行越出，代表相對趨勢同加速嘅組合離中性更遠',
        'tightening': '條線收埋向中心，代表相對趨勢同加速嘅組合更接近中性',
        'steady': '條線離中心嘅距離大致相若',
        'insufficient_history': '點數太少，未夠比較路徑收窄定擴大',
    }[row['path_shape']]
    time = '、'.join(f'{name} {row["quadrant_sessions"][q]} 日（{row["quadrant_percent"][q]:g}%）'
                    for q, name in QUADRANTS.items())
    coverage = (f'只得 {row["sessions"]}/{row["requested_sessions"]} 個交易日可用；'
                if row['partial'] else f'睇返呢 {row["sessions"]} 個交易日；')
    ignored = (f'有 {row["rotation_steps_ignored"]} 步太近中心，未計轉向。'
               if row['rotation_steps_ignored'] else '')
    return (f'- **{ticker}**（{row["start"]} 至 {row["end"]}）：{coverage}{direction}。'
            f'收市點分布：{time}。X 穿中線 {row["axis_crosses"]["X"]} 次，'
            f'Y 穿中線 {row["axis_crosses"]["Y"]} 次。{shape}。{ignored}')


def generate_patterns(data):
    requested = data.get('trail_length', 10)
    rows = {ticker: analyze_trail(points, requested)
            for ticker, points in sorted(data['trails'].items()) if points}
    name = re.sub(r'([\\`*_{}\[\]<>#|])', r'\\\1', data['group']['name'])
    lines = [f'**{name}｜輪動軌跡白話筆記（{requested} 個交易日，對比 SPY）**']
    if data.get('stale'):
        lines.append('⚠️ 更新暫時失敗，以下沿用上次成功數據。')
    for ticker, row in rows.items():
        row['text'] = cantonese_note(ticker, row)
        lines.append(row['text'])
    if not rows:
        lines.append('暫時未有可用軌跡；先加代號，或等齊歷史收市價。')
    if data.get('excluded'):
        lines.append('未計入：' + '、'.join(sorted(data['excluded'])) + '（資料不足或未齊，唔代表落後）。')
    lines.append('白話講：呢幅圖係睇相對 SPY 嘅趨勢同加速點樣變；順轉、逆轉同擴大都唔等於之後會升跌。'
                 '象限日數只計收市點；X/Y 同離中心距離都唔係回報百分比。')
    return {**{key: data.get(key) for key in (
                'as_of', 'group', 'benchmark', 'config_revision', 'snapshot_id', 'last_success', 'stale')},
            'trail_length': requested, 'patterns': rows, 'text': '\n'.join(lines), 'format': 'markdown',
            'method': {
                'version': 1, 'rotation_center_radius': CENTER_RADIUS,
                'minimum_net_turns': MIN_TURNS, 'minimum_coherence': MIN_COHERENCE,
                'radial_comparison': 'median radius of last third vs first third; minimum 6 points',
                'radial_threshold': 'max(0.1, 15% of first-third median radius)',
                'axis_rule': 'opposite nonzero sides; touches alone do not count; tolerance 0.000001',
            }}
