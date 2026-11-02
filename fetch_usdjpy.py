import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = timezone.utc
JST = ZoneInfo('Asia/Tokyo')
NY = ZoneInfo('America/New_York')
NOW = datetime.now(UTC)
DATA = Path('data')
HISTORY = DATA / 'history_1min'
DATA.mkdir(exist_ok=True)
HISTORY.mkdir(exist_ok=True)
RETENTION_DAYS = 56

def is_closed(timestamp_utc):
    ny = timestamp_utc.astimezone(NY)
    return (ny.weekday() == 5 or
            (ny.weekday() == 4 and ny.hour >= 17) or
            (ny.weekday() == 6 and ny.hour < 17))

def parse_jst(s):
    return datetime.strptime(s, '%Y-%m-%d %H:%M:%S').replace(tzinfo=JST)

def existing(path):
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))

def saved_bars(path):
    return existing(path).get('data', {}).get('values', [])

def normalize(bars, interval):
    found = {}
    for bar in bars:
        try:
            o, h, l, c = (float(bar[k]) for k in ('open', 'high', 'low', 'close'))
            if not (0 < l <= o <= h and l <= c <= h):
                continue
            if interval != '1day':
                start = parse_jst(bar['datetime'])
                if is_closed(start.astimezone(UTC)):
                    continue
                minutes = 1 if interval == '1min' else 60
                if start + timedelta(minutes=minutes) > NOW.astimezone(JST):
                    continue
            found[bar['datetime']] = {k:bar[k] for k in ('datetime','open','high','low','close')}
        except (KeyError, ValueError, TypeError):
            continue
    return [found[k] for k in sorted(found, reverse=True)]

def payload(interval, bars, **extra):
    return {
        'source':'Twelve Data', 'symbol':'USD/JPY', 'interval':interval,
        'timezone':'Exchange timezone' if interval == '1day' else 'Asia/Tokyo',
        'fetched_at':NOW.isoformat(), **extra,
        'data':{'status':'ok','values':bars}
    }

def store(path, obj):
    content = json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
    path.write_text(content, encoding='utf-8')

def fetch(interval, size):
    params = urllib.parse.urlencode({
        'symbol':'USD/JPY','interval':interval,'outputsize':size,
        'timezone':'Asia/Tokyo','apikey':os.environ['TWELVE_API_KEY']
    })
    req = urllib.request.Request('https://api.twelvedata.com/time_series?' + params,
                                 headers={'User-Agent':'USDJPY-Data-Collector'})
    with urllib.request.urlopen(req, timeout=40) as res:
        answer = json.load(res)
    if answer.get('status') != 'ok' or not answer.get('values'):
        raise RuntimeError(f'{interval}: {answer.get("message", "Missing OHLC")}')
    bars = normalize(answer['values'], interval)
    if not bars:
        raise RuntimeError(f'{interval}: no confirmed market candles')
    return bars

def is_due(interval, hours):
    old = existing(DATA / f'usdjpy_{interval}.json')
    try:
        timestamp = datetime.fromisoformat(old['fetched_at'].replace('Z','+00:00'))
        return NOW - timestamp >= timedelta(hours=hours)
    except (KeyError, TypeError, ValueError):
        return True

if is_closed(NOW):
    print('FX weekend: API calls skipped')
    raise SystemExit(0)

# One API call per 15-minute run; 180 candles for overlap, 1500 after long gaps.
recent_path = DATA / 'usdjpy_1min.json'
previous = normalize(saved_bars(recent_path), '1min')
if previous:
    gap = (NOW.astimezone(JST) - parse_jst(previous[0]['datetime'])).total_seconds()/60
    outputsize = 1500 if gap > 150 else 180
else:
    outputsize = 1500
new = fetch('1min', outputsize)
combined = normalize(previous + new, '1min')
if not combined or (NOW.astimezone(JST) - parse_jst(combined[0]['datetime'])) > timedelta(minutes=35):
    raise RuntimeError('Minute feed too stale; archive not changed')

# Preserve completed minutes in daily archives; do not rewrite unchanged days.
by_day = {}
for bar in combined:
    by_day.setdefault(bar['datetime'][:10], []).append(bar)
for day, bars in by_day.items():
    path = HISTORY / f'{day}.json'
    old = normalize(saved_bars(path), '1min')
    merged = normalize(old + bars, '1min')
    if merged != old:
        store(path, payload('1min', merged, session_date_jst=day))

# Recent feed is compact; detailed historical days are loaded on demand.
store(recent_path, payload('1min', combined[:1500]))

cutoff = NOW.astimezone(JST).date() - timedelta(days=RETENTION_DAYS)
for path in HISTORY.glob('????-??-??.json'):
    if date.fromisoformat(path.stem) < cutoff:
        path.unlink()

index = []
for path in sorted(HISTORY.glob('????-??-??.json'), reverse=True):
    bars = saved_bars(path)
    if bars:
        index.append({'date_jst':path.stem, 'path':str(path), 'count':len(bars),
                      'first':bars[-1]['datetime'], 'last':bars[0]['datetime']})
store(DATA / 'history_1min_index.json', {'updated_at':NOW.isoformat(),
                                       'retention_calendar_days':RETENTION_DAYS,
                                       'sessions':index})
print(f'1min: API fetched {len(new)}, latest {len(combined[:1500])}, archived {len(index)} days')

# Context timeframes need fewer requests than 1min.
for interval, hours in [('1h', 1), ('1day', 6)]:
    if is_due(interval, hours):
        fetched = fetch(interval, 200)
        store(DATA / f'usdjpy_{interval}.json', payload(interval, fetched))
        print(f'{interval}: refreshed {len(fetched)}')

# Prevent obsolete 5min/15min/4h files from being mistaken for fresh data.
for interval in ('5min','15min','4h'):
    (DATA / f'usdjpy_{interval}.json').unlink(missing_ok=True)
print('USDJPY update completed')
