"""Compare an API snapshot JSON with the supplied reference; dates must match."""
import json
import sys
from pathlib import Path

actual = json.loads(Path(sys.argv[1]).read_text())
reference = json.loads(Path('rotation-trails-latest.json').read_text())
if actual['as_of'][:10] != reference['as_of'][:10]:
    sys.exit('Cannot compare different as-of dates')
errors = []
for ticker, points in reference['trails'].items():
    by_date = {p['date']: p for p in actual['trails'][ticker]}
    for point in points:
        for axis in ['X', 'Y']:
            errors.append(abs(by_date[point['date']][axis] - point[axis]))
maximum = max(errors)
print(f'Maximum coordinate difference: {maximum:.6f}')
sys.exit(0 if maximum <= .05 else 1)
