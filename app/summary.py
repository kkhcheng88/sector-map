"""Deterministic Cantonese Traditional Chinese briefing from the displayed snapshot."""
import re

QUADRANTS = {'Leading': '領先', 'Improving': '改善', 'Lagging': '落後', 'Weakening': '轉弱'}


def quadrant(point):
    if point['X'] >= 100:
        return 'Leading' if point['Y'] >= 100 else 'Weakening'
    return 'Improving' if point['Y'] >= 100 else 'Lagging'


def generate_summary(data):
    safe_name = re.sub(r'([\\`*_{}\[\]<>#|])', r'\\\1', data['group']['name'])
    lines = [f"**{safe_name}｜板塊輪動小結（{data['as_of'] or '未有收市數據'}，對比 SPY）**"]
    if data.get('stale'):
        lines.append('⚠️ 更新暫時失敗，以下沿用上次成功數據。')
    buckets = {q: [] for q in QUADRANTS}
    changes = []
    for ticker, trail in sorted(data['trails'].items()):
        point = trail[-1]
        q = quadrant(point)
        buckets[q].append((ticker, point))
        if len(trail) >= 2:
            prior = trail[-2]
            dx, dy = point['X'] - prior['X'], point['Y'] - prior['Y']
            changes.append((ticker, q, quadrant(prior), dx, dy))
    explanations = {
        'Leading': '跑得較強，仲有加速', 'Improving': '仍然較弱，但開始加速',
        'Lagging': '較弱，亦未有加速', 'Weakening': '仍然較強，但已減速',
    }
    for q, title in QUADRANTS.items():
        ranked = sorted(buckets[q], key=lambda row: (
            -(row[1]['X'] + row[1]['Y']) if q in ('Leading', 'Improving')
            else row[1]['X'] + row[1]['Y'], row[0]))
        names = '、'.join(t for t, _ in ranked[:3]) or '暫時冇'
        if len(ranked) > 3:
            names += f' 等 {len(ranked)} 隻'
        lines.append(f'- {title}：{names}（{explanations[q]}）。')
    if changes:
        movers = sorted(changes, key=lambda c: (c[1] == c[2], -(abs(c[3]) + abs(c[4])), c[0]))[:3]
        descriptions = []
        for ticker, q, prior_q, dx, dy in movers:
            direction = f'{QUADRANTS[prior_q]} → {QUADRANTS[q]}' if q != prior_q else f'仍屬{QUADRANTS[q]}'
            descriptions.append(f'{ticker} {direction}，強弱 X {dx:+.2f}、加速 Y {dy:+.2f}')
        lines.append('較軌跡上一點：' + '；'.join(descriptions) + '。')
    if not data['trails']:
        lines.append('👉 呢組暫時未有可用輪動數據；先加入代號，或等齊歷史收市價再睇。')
    else:
        rising = sorted((c for c in changes if c[1] in ('Leading', 'Improving') and c[4] > 0),
                        key=lambda c: (c[1] != 'Leading', -c[4], c[0]))
        if rising:
            lines.append(f'👉 先留意 {rising[0][0]} 嘅加速可唔可以延續到下一個收市點，再判斷輪動有冇持續。')
        else:
            lines.append('👉 暫時先觀察，等見到加速回升或由落後轉改善，再判斷輪動有冇持續。')
    if data.get('excluded'):
        lines.append('未計入：' + '、'.join(sorted(data['excluded'])) + '（資料不足或未齊，唔代表落後）。')
    lines.append('X/Y 係相對趨勢同加速指標，唔係升跌百分比。')
    return {'text': '\n'.join(lines), 'format': 'markdown', 'as_of': data['as_of'],
            'group': data['group'], 'benchmark': 'SPY', 'stale': data.get('stale', False),
            'config_revision': data['config_revision'],
            'snapshot_id': data.get('snapshot_id'),
            'last_success': data.get('last_success'),
            'quadrants': {q: [t for t, _ in sorted(rows)] for q, rows in buckets.items()}}
