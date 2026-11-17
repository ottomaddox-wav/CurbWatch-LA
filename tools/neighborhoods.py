"""Static pages for search: neighborhoods, venues, topics, plus sitemap.xml.

Reads the finished curb-data.js (run build.py first) and writes static HTML pages, so search
engines and AI tools get real text instead of a canvas. Every number comes from the same data
the map draws; the only hand-written text is the short per-page notes in HOOD_NOTES and VENUES.

    python3 tools/neighborhoods.py

Writes:
  /<neighborhood>/          one per HOODS entry (LA blocks sorted by tools/neighborhoods.geojson)
  /west-hollywood/          its own city and its own data (permit blocks with posted hours, sweeping)
  /parking/<venue>/         streets, permit blocks, lots and tickets within walking distance of a venue
  /street-sweeping/         sweeping across the whole map, by neighborhood
  /speed-cameras/           LADOT speed-camera corridors inside the map
  sitemap.xml

Neighborhood outlines: tools/neighborhoods.geojson, the LA Times "Mapping L.A."
boundaries (informal, not official city limits). A block belongs to the neighborhood that
contains its first point.
"""
import collections, datetime, html, json, math, pathlib, re, sys, urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = 'https://curbwatch.la'
O = (-118.40, 34.04)
DOW = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

# slug -> display name, in the order they link to each other (roughly east to west)
HOODS = {
    'echo-park': 'Echo Park', 'silver-lake': 'Silver Lake', 'los-feliz': 'Los Feliz', 'chinatown': 'Chinatown',
    'downtown': 'Downtown LA', 'westlake': 'Westlake', 'koreatown': 'Koreatown', 'east-hollywood': 'East Hollywood',
    'hollywood': 'Hollywood', 'mid-wilshire': 'Mid-Wilshire', 'hancock-park': 'Hancock Park', 'fairfax': 'Fairfax',
    'beverly-grove': 'Beverly Grove',
}
WEHO = ('west-hollywood', 'West Hollywood')

# hand-written notes, each one a fact from CLAUDE.md's data decisions
HOOD_NOTES = {
    'downtown': [
        'The map stops around 7th–8th Street, so the Fashion District, South Park and most of the Arts District aren’t covered.',
        'Downtown is also swept overnight, 1–4 AM on weekdays, on routes the city doesn’t class as posted no-parking routes, '
        'so they aren’t listed here. If your block has a sign, the sign applies.',
    ],
}

# slug, display name, name in the map's place list, walking radius in meters, note
VENUES = [
    ('the-echo', 'The Echo', 'The Echo', 400, ''),
    ('echo-park-lake', 'Echo Park Lake', 'Echo Park', 400, ''),
    ('dodger-stadium', 'Dodger Stadium', 'Dodger Stadium', 900,
     'The stadium sells its own parking. On game days the streets around it change too: see the game-day zone below.'),
    ('hollywood-bowl', 'Hollywood Bowl', 'Hollywood Bowl', 600,
     'On show nights the Bowl runs its own stacked parking and shuttles; check its site for those. Street rules below still apply.'),
    ('hollywood-palladium', 'Hollywood Palladium', 'Hollywood Palladium', 400, ''),
    ('hollywood-forever', 'Hollywood Forever', 'Hollywood Forever Cemetery and Funeral Home', 400,
     'Screenings and concerts often park inside the grounds; street parking around it is what’s listed here.'),
    ('the-wiltern', 'The Wiltern', 'The Wiltern', 400, ''),
    ('lacma', 'LACMA', 'Los Angeles County Museum of Art', 400, ''),
    ('the-grove', 'The Grove & Farmers Market', 'The Grove', 400, ''),
    ('cedars-sinai', 'Cedars-Sinai', 'Cedars-Sinai Medical Center', 400,
     'Its west side borders West Hollywood, which has its own rules and its own page.'),
    ('walt-disney-concert-hall', 'Walt Disney Concert Hall', 'Walt Disney Concert Hall', 400,
     ''),
]

esc = lambda s: html.escape(str(s), quote=True)

s = (ROOT / 'curb-data.js').read_text()
C = json.loads(s[s.index('window.CURB=') + 12:].rstrip().rstrip(';'))
GEO = json.loads((ROOT / 'tools' / 'neighborhoods.geojson').read_text())

built = C['meta']['built'][:10]
cite_from, cite_to = C['citeRange']


def date_txt(iso):
    y, m, d = map(int, iso[:10].split('-'))
    return f'{MON[m - 1]} {d}, {y}'


def title(t):
    t = t.lower()
    t = re.sub(r'\b([a-z])', lambda m: m.group(1).upper(), t)
    t = re.sub(r'\b(\d+)(St|Nd|Rd|Th)\b', lambda m: m.group(1) + m.group(2).lower(), t)
    t = re.sub(r'\bWy\b', 'Way', t)
    t = re.sub(r'\bMc([a-z])', lambda m: 'Mc' + m.group(1).upper(), t)
    return t


def fmt_min(m):
    m = ((m % 1440) + 1440) % 1440
    h, mm = divmod(m, 60)
    if m == 0: return 'midnight'
    if m == 720: return 'noon'
    return f'{h % 12 or 12}' + (f':{mm:02d}' if mm else '') + (' AM' if h < 12 else ' PM')


def ordn(n):
    return f'{n}' + ('th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th'))


def pip(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][:2]
        xj, yj = ring[j][:2]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def dec(a, start=0):
    x = y = 0
    out = []
    for i in range(start, len(a) - 1, 2):
        x += a[i]; y += a[i + 1]
        out.append((O[0] + x / 1e5, O[1] + y / 1e5))
    return out


MLON = 111320 * math.cos(math.radians(34.07))
MLAT = 110540


def dist_m(a, b):
    return math.hypot((a[0] - b[0]) * MLON, (a[1] - b[1]) * MLAT)


def dist_path(p, path):
    """Meters from point p to the nearest point on a polyline."""
    px, py = p[0] * MLON, p[1] * MLAT
    best = float('inf')
    pts = [(x * MLON, y * MLAT) for x, y in path]
    if len(pts) == 1: return math.hypot(px - pts[0][0], py - pts[0][1])
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        dx, dy = bx - ax, by - ay
        L = dx * dx + dy * dy
        t = 0 if not L else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L))
        best = min(best, math.hypot(px - ax - t * dx, py - ay - t * dy))
    return best


polys = {}
names = {}
for f in GEO['features']:
    g = f['geometry']
    polys[f['properties']['slug']] = [p[0] for p in ([g['coordinates']] if g['type'] == 'Polygon' else g['coordinates'])]
    names[f['properties']['slug']] = f['properties']['name']


def hood_of(x, y):
    for slug, rings in polys.items():
        if any(pip(x, y, r) for r in rings): return slug
    return None


# the map's real extent, for "how much of this neighborhood is covered"
BX = [O[0] + b[9][0] / 1e5 for b in C['blocks']]
BY = [O[1] + b[9][1] / 1e5 for b in C['blocks']]
EXTENT = (min(BX), min(BY), max(BX), max(BY))


def covered_share(rings):
    xs = [c[0] for r in rings for c in r]; ys = [c[1] for r in rings for c in r]
    tot = inn = 0
    N = 50
    for i in range(N):
        for j in range(N):
            x = min(xs) + (max(xs) - min(xs)) * (i + .5) / N
            y = min(ys) + (max(ys) - min(ys)) * (j + .5) / N
            if any(pip(x, y, r) for r in rings):
                tot += 1
                inn += EXTENT[0] <= x <= EXTENT[2] and EXTENT[1] <= y <= EXTENT[3]
    return inn / tot if tot else 1


# ---------- blocks: (row, first point, full path)
BLOCKS = []
for b in C['blocks']:
    path = dec(b[9])
    BLOCKS.append((b, path[0], path))
HB = collections.defaultdict(list)
for t in BLOCKS:
    h = hood_of(*t[1])
    if h: HB[h].append(t)
METERS = [((O[0] + m[0] / 1e5, O[1] + m[1] / 1e5), m) for m in C['meters']]
HMET = collections.defaultdict(list)
for p, m in METERS:
    h = hood_of(*p)
    if h: HMET[h].append(m)

# ---------- venues: find each in the map's place list (notable entries first)
PLACES = sorted(C['places'], key=lambda p: -p[6])
VEN = []
for slug, name, pname, radius, note in VENUES:
    p = next((p for p in PLACES if p[0] == pname), None)
    if not p: sys.exit(f'venue {pname!r} not in the place list; check curb-data.js')
    pt = (O[0] + p[1] / 1e5, O[1] + p[2] / 1e5)
    VEN.append(dict(slug=slug, name=name, pname=pname, addr=p[4], pt=pt, r=radius, note=note, hood=hood_of(*pt)))


# ticket types as people say them; the city's codes repeat themselves
VIOL = {'NO PARK/STREET CLEAN': 'Street cleaning', '8069B NO PARK ST CLN': 'Street cleaning', 'PREFERENTIAL PARKING': 'Permit parking',
        'RED ZONE': 'Red zone', 'METER EXP.': 'Expired meter', 'DISPLAY OF PLATES': 'Plates not displayed', 'NO PARKING': 'No parking'}
vname = lambda v: VIOL.get(v) or title(v)
SWEEP_CODES = ('NO PARK/STREET CLEAN', '8069B NO PARK ST CLN')


def block_range(lo, hi):
    if not lo: return 'whole street'
    a, b = lo // 100 * 100, hi // 100 * 100 + 99
    return f'{a}–{b}' if b != a + 99 else f'{a} block'


def nice_limit(l):
    return {'15MIN': '15 min', '30MIN': '30 min', '1HR': '1 hour', '2HR': '2 hours', '4HR': '4 hours', '6HR': '6 hours',
            '10HR': '10 hours', '2HR-30MIN': '2 hours / 30 min'}.get(l, l)


def sweep_groups(b):
    """A block's sweeping as [(t0, t1, wk, days-bitmask)], one entry per distinct schedule."""
    g = collections.OrderedDict()
    for ri in b[4]:
        _, mask, t0, t1, wk, _ = C['routes'][ri]
        k = (t0, t1, wk)
        g[k] = g.get(k, 0) | mask
    return sorted((k[0], k[1], k[2], v) for k, v in g.items())


def days_txt(mask):
    d = [DOW[i] for i in range(7) if mask & (1 << i)]
    if len(d) == 7: return 'Every day'
    if d == DOW[1:6]: return 'Monday–Friday'
    if d == DOW[1:7]: return 'Monday–Saturday'
    if d == [DOW[0], DOW[6]]: return 'Weekends'
    if len(d) == 1: return d[0]
    return ' & '.join(x[:3] for x in d) if len(d) <= 2 else ', '.join(x[:3] for x in d)


def weeks_txt(wk):
    w = [n for n in (1, 2, 3, 4) if wk & (1 << (n - 1))]
    return 'Every week' if not w else ' & '.join(ordn(n) for n in w) + ' of the month'


def street_key(name):
    return re.sub(r'^(N|S|E|W) ', '', name)


def tix(b): return sum(b[7][1::2])
def ruled(b): return bool(b[4] or b[6] or b[11] or b[10][0])


# ---------- shared sections, each taking a list of (row, first point, path)

def sweep_rows(blocks):
    streets = collections.defaultdict(lambda: collections.defaultdict(list))
    for t, _, _ in blocks:
        if t[4]: streets[t[0]][tuple(sweep_groups(t))].append((t[1], t[2]))
    rows = []
    for st in sorted(streets, key=lambda s: (street_key(s), s)):
        for sig, rng in sorted(streets[st].items(), key=lambda kv: min(r[0] for r in kv[1])):
            lo, hi = min(r[0] for r in rng), max(r[1] for r in rng)
            sched = ' · '.join(f'{days_txt(g[3])}, {fmt_min(g[0])}–{fmt_min(g[1])} ({weeks_txt(g[2])})' for g in sig)
            rows.append((title(st), block_range(lo, hi), sched))
    return rows, len(streets)


def sweep_days(blocks):
    swept = [t for t in blocks if t[0][4]]
    by_day = collections.Counter()
    for t in swept:
        seen = {i for g in sweep_groups(t[0]) for i in range(7) if g[3] & (1 << i)}
        for i in seen: by_day[i] += 1
    return ', '.join(f'{DOW[i]} {round(100 * c / len(swept))}%' for i, c in by_day.most_common() if swept and c / len(swept) >= .05)


def permit_rows(blocks):
    ps = collections.defaultdict(lambda: [10 ** 9, 0, 0])
    for t, _, _ in blocks:
        if t[11] and not t[10][0]:
            p = ps[t[0]]
            p[0] = min(p[0], t[1]); p[1] = max(p[1], t[2]); p[2] += t[11][0] + t[11][1]
    return sorted(((title(k), block_range(v[0], v[1]), v[2]) for k, v in ps.items()), key=lambda r: (street_key(r[0].upper()), r[0]))


def zone_rows(blocks):
    zs = collections.defaultdict(lambda: [10 ** 9, 0, 0])
    for t, _, _ in blocks:
        if t[10][0]:
            z = zs[t[0]]
            z[0] = min(z[0], t[1]); z[1] = max(z[1], t[2]); z[2] = max(z[2], t[10][0])
    return sorted(((title(k), block_range(v[0], v[1]), 'inferred from ticket patterns' if v[2] == 2 else 'on our street list') for k, v in zs.items()), key=lambda r: r[0])


def table(head, rows, numeric=()):
    out = ['<div class="wrap"><table><thead><tr>' + ''.join(f'<th>{esc(h)}</th>' for h in head) + '</tr></thead><tbody>']
    for r in rows:
        out.append('<tr>' + ''.join(f'<td class="n">{c}</td>' if i in numeric else f'<td>{c}</td>' for i, c in enumerate(r)) + '</tr>')
    out.append('</tbody></table></div>')
    return ''.join(out)


def sweep_section(P, blocks, where, empty_ok=True):
    rows, nstreets = sweep_rows(blocks)
    if rows:
        P(f'<h2>Street sweeping {where}, by street</h2>')
        P('<p>Posted no-parking hours for street cleaning, from the City of Los Angeles’ sweeping routes. '
          'Where two days are listed, the city usually sweeps one side of the street each day; the data doesn’t say which side. '
          '“1st &amp; 3rd” means the first and third week of the month. City holidays suspend sweeping. '
          'See <a href="/street-sweeping/">how LA street sweeping works</a>.</p>')
        P(table(['Street', 'Block', 'Day, hours'], [(esc(a), esc(b), esc(c)) for a, b, c in rows], numeric=(1,)))
    elif empty_ok:
        P(f'<h2>Street sweeping</h2><p>No posted sweeping routes are mapped {where}.</p>')
    return nstreets


def permit_section(P, rows):
    if not rows: return
    P('<h2>Permit parking streets</h2>')
    P('<p>Los Angeles hasn’t published a permit-district map since 2015, and that map no longer matches the street. '
      'These streets are <strong>inferred from ticket history</strong>: permit-parking tickets written here routinely, '
      'on many days over many months. A permit street nobody has ticketed yet won’t appear, so this list is a floor, not a full list.</p>')
    P(table(['Street', 'Block', 'Permit tickets'], [(esc(a), esc(b), c) for a, b, c in rows], numeric=(1, 2)))


def zone_section(P, rows):
    if not rows: return
    P('<h2>Dodger Stadium game-day zone</h2>')
    P('<p>No parking from four hours before and during events at Dodger Stadium, except with District D permits. '
      'This zone is in no city dataset; it is built from a hand-kept street list plus blocks where almost all no-parking tickets land on game days. '
      'Concerts and other stadium events count too.</p>')
    P(table(['Street', 'Block', 'Source'], [(esc(a), esc(b), esc(c)) for a, b, c in rows], numeric=(1,)))


def meter_section(P, meters):
    if not meters: return
    rates = collections.Counter(C['rates'][m[2]] for m in meters)
    limits = collections.Counter(C['limits'][m[3]] for m in meters)
    prices = [float(x) for r in rates for x in re.findall(r'\$(\d+(?:\.\d+)?)', r or '')]
    lim = [k for k, _ in limits.most_common(4) if k]
    P('<h2>Meters</h2>')
    bits = [f'{len(meters):,} metered spaces']
    if prices: bits.append(f'hourly rates run from ${min(prices):.2f} to ${max(prices):.2f}')
    if lim: bits.append('time limits ' + ', '.join(esc(nice_limit(k)) for k in lim))
    P('<p>' + '; '.join(bits) + '. Meters run Monday–Saturday, 8 AM–8 PM on almost every block (going by when meter tickets are written); outside those hours parking at a meter is free unless a sign says otherwise. Rates vary by time of day. Live space-by-space availability is on the map.</p>')


def ticket_section(P, blocks, top=8):
    viol, vfine = collections.Counter(), {}
    for t, _, _ in blocks:
        for v, c in t[8]:
            viol[vname(v)] += c
            if v in C['fines']: vfine.setdefault(vname(v), C['fines'][v])
    top_viol = viol.most_common(6)
    top_blocks = [t for t in sorted(blocks, key=lambda t: -tix(t[0]))[:top] if tix(t[0])]
    P('<h2>Where tickets get written</h2>')
    if not top_blocks:
        P('<p>No parking tickets on record here in the six months to ' + date_txt(cite_to) + '.</p>')
        return
    if top_viol:
        P('<p>Most common tickets here: ' + ', '.join(f'{esc(v)} ({c:,}' + (f', typically ${vfine[v]}' if v in vfine else '') + ')' for v, c in top_viol) + '.</p>')
    rows = [(esc(title(t[0])) + (f' · {esc(block_range(t[1], t[2]))}' if t[1] else ''), tix(t), esc(vname(t[8][0][0])) if t[8] else '') for t, _, _ in top_blocks]
    P(table(['Block', 'Tickets, 6 months', 'Most common'], rows, numeric=(1,)))


def limits_section(P, blocks, extra=''):
    n = len(blocks)
    tix_only = sum(1 for t, _, _ in blocks if not ruled(t) and t[7])
    none = n - sum(1 for t, _, _ in blocks if ruled(t)) - tix_only
    P('<h2>What this page can’t tell you</h2>')
    P(f'<p>{extra}Of {n:,} blocks, {tix_only:,} have tickets but no rule we can point to (a sign probably exists that the city’s map doesn’t carry), '
      f'and {none:,} have no records at all. <strong>No record is not the same as free parking.</strong> '
      'No dataset covers red curbs, hydrants, driveways, loading zones, or temporary no-parking signs. '
      '<a href="/terms.html">Terms &amp; data notes</a> explain what is inferred and what is official.</p>')


def nav(here):
    hoods = [(f'/{k}/', v) for k, v in HOODS.items()] + [(f'/{WEHO[0]}/', WEHO[1])]
    hoods.sort(key=lambda kv: kv[1])
    ven = [(f'/parking/{v["slug"]}/', v['name']) for v in VEN]
    topics = [('/street-sweeping/', 'Street sweeping schedules'), ('/speed-cameras/', 'Speed cameras')]
    li = lambda xs: ''.join(f'<li><a href="{u}">{esc(n)}</a></li>' for u, n in xs if u != here)
    return (f'<h2>Neighborhoods</h2><ul class="near">{li(hoods)}</ul>'
            f'<h2>Parking near</h2><ul class="near">{li(ven)}</ul>'
            f'<h2>Guides</h2><ul class="near">{li(topics)}</ul>')


CALLOUT = ('<div class="callout"><p><strong>The posted sign is the authority. This page is not.</strong> '
           'It is built from public records and can be out of date or wrong. Read the curb before you leave your car.</p></div>')


def sub_line():
    return f'<p class="sub">Updated {date_txt(built)} · ticket records through {date_txt(cite_to)}</p>'


CSS = """:root{--ground:#EEEAE1;--panel:#FBFAF6;--ink:#2A2926;--ink-2:#6B675F;--ink-3:#9A958B;--line:#E2DDD1;--moss:#4E7A52;--red:#C8433B}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--ground:#1C1B18;--panel:#26241F;--ink:#EDE9DF;--ink-2:#A9A497;--ink-3:#7D786C;--line:#3B3831;--moss:#7FB08A;--red:#E0625A}}
:root[data-theme="dark"]{--ground:#1C1B18;--panel:#26241F;--ink:#EDE9DF;--ink-2:#A9A497;--ink-3:#7D786C;--line:#3B3831;--moss:#7FB08A;--red:#E0625A}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);font:16px/1.6 "Helvetica Neue",Helvetica,Arial,sans-serif;-webkit-font-smoothing:antialiased}
main{max-width:46rem;margin:0 auto;padding:56px 24px 96px}
a{color:var(--moss)}
.top{margin:0 0 32px;font-size:14px}
.top a{text-decoration:none;font-weight:600}
.top a:hover{text-decoration:underline}
h1{font:600 38px/1.1 "Helvetica Neue",Helvetica,Arial,sans-serif;letter-spacing:-.02em;margin:0 0 6px}
.sub{color:var(--ink-2);margin:0 0 28px}
.lede{color:var(--ink);font-size:18px;line-height:1.5}
h2{font:600 21px/1.25 "Helvetica Neue",Helvetica,Arial,sans-serif;letter-spacing:-.02em;margin:38px 0 10px}
p,li{color:var(--ink-2)}
p{margin:0 0 14px}
ul{margin:0 0 14px;padding-left:20px}
li{margin-bottom:6px}
strong{color:var(--ink);font-weight:600}
.callout{background:var(--panel);border-left:4px solid var(--red);border-radius:12px;padding:16px 18px;margin:0 0 14px}
.callout p:last-child{margin:0}
.btn{display:inline-block;background:var(--moss);color:var(--panel);font-weight:600;text-decoration:none;border-radius:10px;padding:10px 16px;margin:4px 0 8px}
.btn:hover{opacity:.9}
.wrap{overflow-x:auto;margin:0 0 14px}
table{border-collapse:collapse;width:100%;font-size:15px}
th,td{text-align:left;padding:7px 12px 7px 0;border-bottom:1px solid var(--line);vertical-align:top;color:var(--ink-2)}
th{color:var(--ink);font-weight:600;font-size:13px;letter-spacing:.02em;text-transform:uppercase}
td:first-child{color:var(--ink);font-weight:500}
.n{font-variant-numeric:tabular-nums;white-space:nowrap}
.near{display:flex;flex-wrap:wrap;gap:6px 14px;list-style:none;padding:0}
.near li{margin:0}
footer{border-top:1px solid var(--line);margin-top:48px;padding-top:18px;font-size:13.5px;color:var(--ink-3)}
footer p{color:var(--ink-3)}"""

FOOT = ('Sources: City of Los Angeles sweeping routes, meters and parking citations; City of West Hollywood GIS; LA County; '
        'OpenStreetMap contributors (ODbL). Neighborhood outlines: LA Times Mapping L.A., an informal set, not official city limits. '
        'Not affiliated with the City of Los Angeles, the City of West Hollywood or LADOT.')


def page(path, ttl, desc, body, crumbs, about=None):
    """path like '/echo-park/'; crumbs = [(name, path), ...] after the home crumb."""
    here = SITE + path
    items = [('CurbWatch LA', '/')] + crumbs
    ld = {
        '@context': 'https://schema.org', '@type': 'WebPage', 'name': ttl, 'url': here, 'description': desc,
        'dateModified': built, 'inLanguage': 'en',
        'isPartOf': {'@type': 'WebSite', 'name': 'CurbWatch LA', 'url': SITE + '/'},
        'breadcrumb': {'@type': 'BreadcrumbList', 'itemListElement': [
            {'@type': 'ListItem', 'position': i + 1, 'name': n, 'item': SITE + p} for i, (n, p) in enumerate(items)]},
    }
    if about: ld['about'] = about
    et = esc(ttl)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{et}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{here}">
<meta name="robots" content="index, follow, max-image-preview:large">
<link rel="icon" href="/FAVICON2.png" type="image/png">
<link rel="apple-touch-icon" href="/FAVICON2.png">
<meta name="theme-color" content="#EEEAE1" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#1C1B18" media="(prefers-color-scheme: dark)">
<meta name="color-scheme" content="light dark">
<meta property="og:type" content="website">
<meta property="og:site_name" content="CurbWatch LA">
<meta property="og:title" content="{et}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{here}">
<meta property="og:image" content="{SITE}/og-image.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{et}">
<meta name="twitter:description" content="{esc(desc)}">
<meta name="twitter:image" content="{SITE}/og-image.png">
<script type="application/ld+json">
{json.dumps(ld, indent=1, ensure_ascii=False)}
</script>
<script>try{{const t=localStorage.getItem('curbwatch-theme');if(t==='light'||t==='dark')document.documentElement.dataset.theme=t}}catch(e){{}}</script>
<style>
{CSS}
</style>
</head>
<body>
<!-- Built by tools/neighborhoods.py; don't hand-edit. -->
<main>
  <p class="top"><a href="/">← CurbWatch LA map</a></p>
{chr(10).join('  ' + x for x in body)}
  <footer>
    <p>{FOOT}</p>
  </footer>
</main>
</body>
</html>
"""


def centroid(pts):
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


# ---------- neighborhood pages

def render_hood(slug, name):
    blocks = HB[slug]
    n = len(blocks)
    meters = HMET[slug]
    cx, cy = centroid([t[1] for t in blocks])
    path = f'/{slug}/'
    nruled = sum(1 for t, _, _ in blocks if ruled(t))
    prow, zrow = permit_rows(blocks), zone_rows(blocks)
    _, nstreets = sweep_rows(blocks)
    day_line = sweep_days(blocks)
    share = covered_share(polys[slug])
    notes = list(HOOD_NOTES.get(slug, []))
    if share < .9 and not notes:
        notes.insert(0, f'About {round(100 * share)}% of {name}, by the LA Times outline, falls inside the map’s coverage; streets outside it aren’t listed.')
    near_v = [v for v in VEN if v['hood'] == slug]

    h = []
    P = h.append
    P(f'<h1>Parking in {esc(name)}</h1>')
    P(sub_line())
    P(f'<p class="lede">Street parking in {esc(name)}, block by block: when each street is swept, which streets are permit-only, '
      'where the meters are, and which blocks draw the most tickets.</p>')
    P(CALLOUT)
    P(f'<p><a class="btn" href="/?at={cx:.4f},{cy:.4f},14.5">See {esc(name)} on the map</a></p>')
    for note in notes: P(f'<p>{esc(note)}</p>')

    P('<h2>At a glance</h2><ul>')
    P(f'<li><strong>{n:,}</strong> street blocks mapped in {esc(name)}; <strong>{nruled:,}</strong> ({round(100 * nruled / n)}%) have a mapped rule: sweeping, meters, permit enforcement or a game-day zone.</li>')
    if nstreets: P(f'<li>Street sweeping is posted on <strong>{nstreets}</strong> streets.' + (f' By block, the days are {esc(day_line)}.' if day_line else '') + '</li>')
    if meters: P(f'<li><strong>{len(meters):,}</strong> parking meters.</li>')
    if prow: P(f'<li><strong>{len(prow)}</strong> streets where permit-parking tickets are written routinely.</li>')
    if zrow: P(f'<li><strong>{len(zrow)}</strong> streets in the Dodger Stadium game-day zone.</li>')
    P(f'<li><strong>{sum(tix(t) for t, _, _ in blocks):,}</strong> parking tickets written here in the six months to {date_txt(cite_to)}.</li></ul>')
    if near_v:
        P('<p>Going somewhere specific? ' + ', '.join(f'<a href="/parking/{v["slug"]}/">Parking near {esc(v["name"])}</a>' for v in near_v) + '.</p>')

    sweep_section(P, blocks, f'in {esc(name)}')
    permit_section(P, prow)
    zone_section(P, zrow)
    meter_section(P, meters)
    ticket_section(P, blocks)
    limits_section(P, blocks)
    P(nav(path))

    desc = (f'Parking in {name}, street by street: street-sweeping days and hours, permit-parking streets, meters '
            f'and where tickets get written. From LA open data, updated {date_txt(built)}.')
    ttl = f'Parking in {name}: Street Sweeping, Permit Streets & Meters · CurbWatch LA'
    return page(path, ttl, desc, h, [(name, path)], about={'@type': 'Place', 'name': f'{name}, Los Angeles'})


# ---------- venue pages

def lot_line(l):
    if l.get('src') == 'LADOT':
        return ' · '.join(x for x in [l.get('r') if l.get('r') and l['r'] != 'See Special Features' else '', l['d'] + ' max' if l.get('d') else '', l.get('h')] if x)
    if l.get('src') == 'WeHo':
        return ' · '.join(x for x in [l['s'] + ' spaces' if l.get('s') else '', 'City of West Hollywood'] if x)
    return ' · '.join(x for x in ['Garage' if l['k'] == 'garage' else 'Lot', {'customers': 'customers only', 'yes': 'public'}.get(l.get('acc'), ''),
                                  {'yes': 'paid', 'no': 'free'}.get(l.get('fee'), ''), l['s'] + ' spaces' if l.get('s') else '', l.get('h', '')] if x)


def walk(m):
    return f'{max(1, round(m / 80))} min walk'


def render_venue(v):
    pt, R, name = v['pt'], v['r'], v['name']
    near = [(t, dist_path(pt, t[2])) for t in BLOCKS]
    blocks = [t for t, d in near if d <= R]
    if not blocks: sys.exit(f'no blocks near {name}')
    meters = [m for p, m in METERS if dist_m(pt, p) <= R]
    prow, zrow = permit_rows(blocks), zone_rows(blocks)
    _, nstreets = sweep_rows(blocks)
    path = f'/parking/{v["slug"]}/'
    hood = v['hood']
    hood_name = HOODS.get(hood) or names.get(hood)
    q = v['pname'] + (', ' + v['addr'] if v['addr'] else '')
    map_url = '/?q=' + urllib.parse.quote(q)
    lots = sorted(((l, dist_m(pt, (O[0] + l['p'][0] / 1e5, O[1] + l['p'][1] / 1e5))) for l in C['lots'] if l.get('n')), key=lambda x: x[1])
    lots = [x for x in lots if x[1] <= max(R * 1.5, 650)][:8]
    # how far each permit street is, for the event-night reader
    pdist = {}
    for t, d in near:
        b = t[0]
        if d <= R and b[11] and not b[10][0]:
            k = title(b[0]); pdist[k] = min(pdist.get(k, 1e9), d)

    h = []
    P = h.append
    P(f'<h1>Parking near {esc(name)}</h1>')
    P(sub_line())
    where = (esc(v['addr']) + ', ' if v['addr'] else '') + (f'<a href="/{hood}/">{esc(hood_name)}</a>' if hood in HOODS else esc(hood_name or 'Los Angeles'))
    P(f'<p class="lede">Street parking within about {R:,} m ({walk(R)}) of {esc(name)}, {where}: '
      'which streets are permit-only, when they’re swept, where the meters and public lots are, and where tickets get written.</p>')
    P(CALLOUT)
    P(f'<p><a class="btn" href="{map_url}">See {esc(name)} on the map</a></p>')
    if v['note']: P(f'<p>{esc(v["note"])}</p>')

    P('<h2>At a glance</h2><ul>')
    P(f'<li><strong>{len(blocks):,}</strong> street blocks within {R:,} m.</li>')
    if prow: P(f'<li><strong>{len(prow)}</strong> nearby street{"s have" if len(prow) != 1 else " has"} routine permit-parking enforcement. '
               'Read the posted permit hours before parking there.</li>')
    if nstreets: P(f'<li>Street sweeping is posted on <strong>{nstreets}</strong> nearby streets.</li>')
    if meters: P(f'<li><strong>{len(meters):,}</strong> metered spaces.</li>')
    if lots: P(f'<li><strong>{len(lots)}</strong> named garages or lots close by.</li>')
    P(f'<li><strong>{sum(tix(t) for t, _, _ in blocks):,}</strong> parking tickets written within {R:,} m in the six months to {date_txt(cite_to)}.</li></ul>')

    if lots:
        P('<h2>Garages and lots</h2>')
        P('<p>From LADOT, the City of West Hollywood and OpenStreetMap. Rates and hours change, and event pricing usually applies on show nights.</p>')
        P(table(['Lot', 'Distance', 'Details'], [(esc(l['n']) + (f'<br><small>{esc(l["a"])}</small>' if l.get('a') else ''), f'{round(d / 10) * 10:,} m', esc(lot_line(l))) for l, d in lots], numeric=(1,)))

    if prow:
        P(f'<h2>Permit parking near {esc(name)}</h2>')
        P('<p>Permit-parking tickets are written routinely on these streets. Los Angeles hasn’t published a current permit-district map, '
          'so these are <strong>inferred from ticket history</strong>; read the posted hours on the sign. A permit street nobody has ticketed yet won’t appear.</p>')
        P(table(['Street', 'Block', 'Distance', 'Permit tickets'], [(esc(a), esc(b), f'{round(pdist.get(a, 0) / 10) * 10:,} m', c) for a, b, c in prow], numeric=(1, 2, 3)))

    sweep_section(P, blocks, f'near {esc(name)}', empty_ok=False)
    zone_section(P, zrow)
    meter_section(P, meters)
    ticket_section(P, blocks, top=6)
    limits_section(P, blocks)
    P(nav(path))

    desc = (f'Where to park near {name}: permit streets, street-sweeping hours, meters, public lots and ticket hot spots within a '
            f'{walk(R)}. From LA open data, updated {date_txt(built)}.')
    ttl = f'Parking near {name}: Street Parking, Lots & Permit Streets · CurbWatch LA'
    crumbs = ([(hood_name, f'/{hood}/')] if hood in HOODS else []) + [(f'Parking near {name}', path)]
    about = {'@type': 'Place', 'name': name, 'geo': {'@type': 'GeoCoordinates', 'latitude': round(pt[1], 5), 'longitude': round(pt[0], 5)}}
    if v['addr']: about['address'] = {'@type': 'PostalAddress', 'streetAddress': v['addr'], 'addressLocality': 'Los Angeles', 'addressRegion': 'CA'}
    return page(path, ttl, desc, h, crumbs, about=about)


# ---------- West Hollywood

def weho_rules(rules, raw):
    if not rules: return raw or 'Hours not listed'
    parts = []
    for typ, mask, t0, t1 in rules:
        when = days_txt(mask) + ', ' + ('24 hours' if (t0, t1) == (0, 1440) else f'{fmt_min(t0)}–{fmt_min(t1)}')
        parts.append(('Permit only: ' if typ == 'permit' else '2-hour limit without a permit: ') + when)
    return '; '.join(parts)


def render_weho():
    slug, name = WEHO
    path = f'/{slug}/'
    seen = set()
    rows = []
    for nm, dist, hrs, rules, _ in C['wehoPermit']:
        key = (nm, dist, hrs)
        if key in seen: continue
        seen.add(key)
        m = re.match(r'(\d+)\s*-\s*(\d+)\s+(.*)', nm.strip())
        st, rng = (title(m.group(3)), f'{m.group(1)}–{m.group(2)}') if m else (title(nm), '')
        rows.append((st, rng, dist, weho_rules(rules, hrs)))
    rows.sort(key=lambda r: (r[0], int(re.match(r'\d*', r[1]).group() or 0)))
    dists = sorted({r[2] for r in rows if r[2]}, key=lambda d: [int(x) for x in re.findall(r'\d+', d)])
    allday = sum(1 for nm, d, hrs, rules, _ in C['wehoPermit'] if any(r[0] == 'permit' and (r[2], r[3]) == (0, 1440) for r in rules))

    sched = collections.Counter()
    for zone, mask, t0, t1, day, _ in C['wehoSweep']:
        sched[(mask, t0, t1, zone.strip())] += 1
    srows = sorted(sched.items(), key=lambda kv: (min(i for i in range(7) if kv[0][0] & (1 << i)) if kv[0][0] != 127 else -1, kv[0][1]))
    lots = [l for l in C['lots'] if l.get('src') == 'WeHo']
    cx, cy = centroid(dec(C['weho'][0]))

    h = []
    P = h.append
    P(f'<h1>Parking in {name}</h1>')
    P(sub_line().replace(f' · ticket records through {date_txt(cite_to)}', ''))
    P('<p class="lede">West Hollywood is its own city, with its own permit districts and its own sweeping schedule. '
      'This page uses the City of West Hollywood’s published data, which, unlike Los Angeles, lists the posted permit hours block by block.</p>')
    P(CALLOUT)
    P(f'<p><a class="btn" href="/?at={cx:.4f},{cy:.4f},14.6">See West Hollywood on the map</a></p>')

    P('<h2>At a glance</h2><ul>')
    P(f'<li><strong>{len(rows):,}</strong> permit-parking blocks across <strong>{len(dists)}</strong> districts ({", ".join(esc(d) for d in dists)}).</li>')
    if allday: P(f'<li>Some districts are permit-only around the clock; on most blocks, non-residents get a 2-hour limit by day and permit-only parking at night.</li>')
    P(f'<li>Street sweeping is posted on <strong>{sum(sched.values()):,}</strong> street segments, on {len(srows)} schedules.</li>')
    if lots: P(f'<li><strong>{len(lots)}</strong> city-run parking structures and lots.</li>')
    P('<li>West Hollywood’s parking tickets aren’t in Los Angeles’ ticket data, so this page has no ticket history.</li></ul>')

    P('<h2>Permit parking by block</h2>')
    P('<p>From the City of West Hollywood’s permit-parking map. A block’s 2-hour limit applies to cars without that district’s permit; '
      'permit-only hours mean no parking at all without one. The posted sign wins where they differ.</p>')
    P(table(['Street', 'Block', 'District', 'Hours'], [(esc(a), esc(b), esc(c), esc(d)) for a, b, c, d in rows], numeric=(1,)))

    P('<h2>Street sweeping</h2>')
    P('<p>West Hollywood’s sweeping data doesn’t carry street names, so this lists the schedules. Click a street on the map to see which one applies to it. '
      'Lettered zones are the early-morning commercial routes.</p>')
    P(table(['Day', 'Hours', 'Zone', 'Street segments'],
            [(esc(days_txt(k[0])), esc(f'{fmt_min(k[1])}–{fmt_min(k[2])}'), esc(k[3] or '—'), c) for k, c in srows], numeric=(1, 3)))

    if lots:
        P('<h2>City parking structures and lots</h2>')
        P(table(['Name', 'Address', 'Spaces'], [(esc(l['n']), esc(l.get('a', '')), esc(l.get('s', ''))) for l in sorted(lots, key=lambda l: l['n'])], numeric=(2,)))

    P('<h2>What this page can’t tell you</h2>')
    P('<p>No dataset covers red curbs, hydrants, driveways, loading zones, valet zones or temporary no-parking signs. '
      'Meters on West Hollywood’s commercial streets aren’t in the data either. '
      '<a href="/terms.html">Terms &amp; data notes</a> explain where everything comes from.</p>')
    P(nav(path))

    desc = (f'Parking in West Hollywood, block by block: permit districts with posted hours, the street-sweeping schedule and city parking '
            f'structures, from the City of West Hollywood’s data. Updated {date_txt(built)}.')
    ttl = 'Parking in West Hollywood: Permit Districts, Hours & Street Sweeping · CurbWatch LA'
    return page(path, ttl, desc, h, [(name, path)], about={'@type': 'City', 'name': 'West Hollywood, California'})


# ---------- topic: street sweeping

def render_sweeping():
    path = '/street-sweeping/'
    swept = [t for t in BLOCKS if t[0][4]]
    by_day = collections.Counter()
    windows = collections.Counter()
    weeks = collections.Counter()
    hours = collections.Counter()
    for t in swept:
        gs = sweep_groups(t[0])
        for i in {i for g in gs for i in range(7) if g[3] & (1 << i)}: by_day[i] += 1
        for g in gs:
            windows[(g[0], g[1])] += 1
            weeks['weekly' if g[2] in (0, 15) else 'twice'] += 1
            hours[(g[1] - g[0]) % 1440] += 1
    twice = weeks['twice'] / max(1, sum(weeks.values()))
    hr_txt = ' or '.join(f'{m / 60:g}' for m, _ in sorted(hours.most_common(2))) + ' hours'
    sweep_tix = sum(c for t in BLOCKS for v, c in t[0][8] if v in SWEEP_CODES)
    fine = C['fines'].get('NO PARK/STREET CLEAN')
    rows = []
    for slug, name in sorted(HOODS.items(), key=lambda kv: kv[1]):
        bl = HB[slug]
        _, ns = sweep_rows(bl)
        st = sum(c for t, _, _ in bl for v, c in t[8] if v in SWEEP_CODES)
        rows.append((f'<a href="/{slug}/">{esc(name)}</a>', ns, esc(sweep_days(bl) or '—'), f'{st:,}'))
    win_txt = ', '.join(f'{fmt_min(a)}–{fmt_min(b)}' for (a, b), _ in windows.most_common(4))

    h = []
    P = h.append
    P('<h1>Street sweeping in Los Angeles</h1>')
    P(sub_line())
    P('<p class="lede">Los Angeles posts a street-sweeping window on most residential streets: one day, a few hours, no parking. '
      'Miss it and the ticket is ' + (f'${fine}' if fine else 'a fine') + '. Here’s how the schedule works from Echo Park to West Hollywood, '
      'with every street’s day and hours on its neighborhood page.</p>')
    P(CALLOUT)
    P('<p><a class="btn" href="/">Check your block on the map</a></p>')

    P('<h2>How LA street sweeping works</h2><ul>')
    P('<li><strong>The sign is the schedule.</strong> Posted sweeping signs give a day and a time window. The city’s route data is what this site uses, and it matches the signs on most blocks, but not all.</li>')
    P('<li><strong>One side per day.</strong> Many streets are swept on two days, one side each day. The city’s data doesn’t say which side, so read the sign on your side.</li>')
    P(f'<li><strong>Twice a month, not every week.</strong> {"Nearly all" if twice >= .99 else f"{round(100 * twice)}% of"} swept blocks are on a 1st &amp; 3rd week '
      'or 2nd &amp; 4th week schedule, so check which weeks your sign says, not just the day.</li>'
      if twice >= .5 else
      f'<li><strong>Most streets are swept every week</strong>; {round(100 * twice)}% are on a 1st &amp; 3rd or 2nd &amp; 4th week schedule.</li>')
    P(f'<li><strong>Windows run {esc(hr_txt)}.</strong> The most common are {esc(win_txt)}.</li>')
    P('<li><strong>City holidays suspend sweeping.</strong> Other days, the posted window applies whether or not you see the sweeper.</li>')
    P('<li><strong>West Hollywood is a separate city</strong> with its own schedule: see <a href="/west-hollywood/">parking in West Hollywood</a>.</li></ul>')

    P('<h2>Which days</h2>')
    P(f'<p>Across the map, <strong>{len(swept):,}</strong> Los Angeles blocks have posted sweeping. By block, the days are '
      + esc(', '.join(f'{DOW[i]} {round(100 * c / len(swept))}%' for i, c in by_day.most_common())) + '.</p>')
    P(f'<p>{sweep_tix:,} street-cleaning tickets were written on these blocks between {date_txt(cite_from)} and {date_txt(cite_to)}.</p>')

    P('<h2>By neighborhood</h2>')
    P('<p>Each neighborhood page lists every swept street with its day and hours.</p>')
    P(table(['Neighborhood', 'Streets swept', 'Days, by block', 'Street-cleaning tickets'], rows, numeric=(1, 3)))
    P('<p>Downtown is also swept overnight on weekdays on routes the city doesn’t post as no-parking, so those aren’t listed.</p>')
    P(nav(path))

    desc = ('How Los Angeles street sweeping works, and the posted day and hours for every street from Echo Park to West Hollywood. '
            f'From StreetsLA route data, updated {date_txt(built)}.')
    ttl = 'LA Street Sweeping Schedule: Days & Hours by Street · CurbWatch LA'
    return page(path, ttl, desc, h, [('Street sweeping', path)])


# ---------- topic: speed cameras

def render_speed():
    path = '/speed-cameras/'
    rows = []
    for st, fr, to, on, paths in C['speed']:
        pts = dec(paths[0])
        mx, my = pts[len(pts) // 2]
        d0 = datetime.date.fromisoformat(on)
        fines = d0 + datetime.timedelta(days=60)
        rows.append((street_key(st), f'<a href="/?at={mx:.4f},{my:.4f},16">{esc(st)}</a>', esc(fr), esc(to), date_txt(on), date_txt(fines.isoformat())))
    rows = [r[1:] for r in sorted(rows)]

    h = []
    P = h.append
    P('<h1>Speed cameras in Los Angeles</h1>')
    P(sub_line().replace(f' · ticket records through {date_txt(cite_to)}', ''))
    P(f'<p class="lede">LADOT’s speed-camera program switched on its first corridors in late 2026. '
      f'{len(rows)} of them run through CurbWatch’s map area. Here’s where they are and when fines start.</p>')
    P('<p><a class="btn" href="/">See them on the map</a></p>')

    P('<h2>How the tickets work</h2><ul>')
    P('<li>Cameras ticket drivers going <strong>11 mph or more over the limit</strong>, by mail to the registered owner.</li>')
    P('<li>Fines: <strong>$50</strong> up to 15 mph over, <strong>$100</strong> up to 25 over, <strong>$200</strong> above that, and <strong>$500</strong> at 100 mph or more.</li>')
    P('<li>Each corridor starts with <strong>60 days of warning notices</strong> before fines begin.</li></ul>')

    P('<h2>Corridors on the map</h2>')
    P('<p>Each corridor covers the street between the two cross streets listed. Click a street to open it on the map.</p>')
    P(table(['Street', 'From', 'To', 'Switched on', 'Fines from'], rows))
    P('<p>LADOT publishes the corridors per council district, not as a data feed; this list is kept by hand and traced on OpenStreetMap streets. '
      'Corridors outside the map’s area aren’t listed.</p>')
    P(nav(path))

    desc = (f'Where LA’s new speed cameras are: {len(rows)} LADOT corridors from Koreatown to West Hollywood, with the cross streets, '
            'start dates, when fines begin and how much they are.')
    ttl = 'LA Speed Camera Locations & Fines · CurbWatch LA'
    return page(path, ttl, desc, h, [('Speed cameras', path)])


def write(rel, text):
    f = ROOT / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    if not f.exists() or f.read_text() != text:
        f.write_text(text)
        print('wrote', rel)


def lastmod(rel):
    """When this page last changed: today if it differs from the last commit (or is new), else the
    date of the last commit that touched it. Using the build date for every page told Google nothing
    about which pages actually changed."""
    import subprocess
    git = lambda *a: subprocess.run(['git', '-C', str(ROOT), *a], capture_output=True, text=True).stdout.strip()
    if git('status', '--porcelain', '--', rel) or not git('ls-files', '--', rel):
        return datetime.date.today().isoformat()
    return git('log', '-1', '--format=%cs', '--', rel) or built


def main():
    missing = [s for s in HOODS if s not in HB]
    if missing: sys.exit(f'no blocks for {missing}; check tools/neighborhoods.geojson')
    pages = [('/', 'index.html'), ('/terms.html', 'terms.html')]
    for slug, name in HOODS.items():
        write(f'{slug}/index.html', render_hood(slug, name)); pages.append((f'/{slug}/', f'{slug}/index.html'))
    write(f'{WEHO[0]}/index.html', render_weho()); pages.append((f'/{WEHO[0]}/', f'{WEHO[0]}/index.html'))
    for v in VEN:
        rel = f'parking/{v["slug"]}/index.html'
        write(rel, render_venue(v)); pages.append((f'/parking/{v["slug"]}/', rel))
    write('street-sweeping/index.html', render_sweeping()); pages.append(('/street-sweeping/', 'street-sweeping/index.html'))
    write('speed-cameras/index.html', render_speed()); pages.append(('/speed-cameras/', 'speed-cameras/index.html'))
    sm = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + ''.join(
        f'  <url>\n    <loc>{SITE}{u}</loc>\n    <lastmod>{lastmod(rel)}</lastmod>\n  </url>\n' for u, rel in pages) + '</urlset>\n'
    write('sitemap.xml', sm)
    print('sitemap', len(pages), 'urls')

main()
