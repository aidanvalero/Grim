# Turns a night of finds into distinct bugs. Every confirmed find was minimized by the fuzzer; finds whose minimal
# scenarios need the same movement blocks (water, slime, ice, pits...) and effects for the same check are one family.
# Writes fuzz/out/groups.json (families, representative = smallest minimal scenario) and fuzz/out/report.md, which
# also shows any fuzz/out/verify-<label>.json reruns (stock, fix jars).
#   python -I fuzz/group.py [--since YYYY-MM-DDTHH:MM] [--results fuzz/out/results-*.jsonl]
import argparse, glob, json, os, re, statistics, time

HERE = os.path.dirname(os.path.abspath(__file__))
FINDS, OUT = os.path.join(HERE, 'finds'), os.path.join(HERE, 'out')
# Blocks that change how a player moves (friction, fluids, climbing, sticking, bouncing). Everything else is shape
# only (slabs, stairs, fences...) and just tells the family apart when nothing in here is left
ACTIVE = {'water', 'lava', 'slime_block', 'honey_block', 'soul_sand', 'ice', 'packed_ice', 'blue_ice', 'cobweb',
          'powder_snow', 'scaffolding', 'ladder', 'mud', 'bubble_column', 'pit'}


def block_name(kind, block):
    name = re.sub(r'\[.*', '', block).replace('minecraft:', '')
    return 'pit' if name == 'air' else name


def signature(find):
    m = find.get('minimal') or find['scenario']
    blocks = sorted({block_name(b[0], b[2]) for b in m['build']})
    effects = sorted({e[0] for e in m['effects']})
    keys = sorted({e[2] for e in m['events'] if e[1] in ('down', 'up')})
    turns = any(e[1] in ('look', 'flick') for e in m['events'])
    active = [b for b in blocks if b in ACTIVE]
    where = ','.join(active) if active else 'shape:' + ','.join(blocks) if blocks else 'flat floor'
    family = ' / '.join([','.join(find['checks']), where, ','.join(effects) or '-'])   # no "|": it goes in a md table
    size = len(m['build']) + len(m['effects']) + len(keys) + (1 if turns else 0)
    return family, {'blocks': blocks, 'effects': effects, 'keys': keys + (['turns'] if turns else []),
                    'ticks': m['ticks'], 'yaw': m['yaw'], 'size': size, 'active': active}


def related(g, groups):
    """The smallest other family with the same check whose movement blocks and effects this one also has: often the same
    bug with an extra block the minimizer couldn't drop (a flaky try keeps an element rather than lose one)."""
    rep = g['rep']
    for other in sorted(groups, key=lambda o: len(o['rep']['active']) + len(o['rep']['effects'])):
        o = other['rep']
        if (other is not g and other['family'].split(' / ')[0] == g['family'].split(' / ')[0] and o['active']
                and set(o['active']) <= set(rep['active']) and set(o['effects']) <= set(rep['effects'])
                and (o['active'], o['effects']) != (rep['active'], rep['effects'])):
            return other['family']
    return ''


def decay(offsets):
    """Median ratio between consecutive flagged offsets: about .91 airborne, .8 in water, .546 walking on a normal block."""
    vals = [float(o) for _, o in offsets if re.fullmatch(r'\d*\.\d+(E-?\d+)?', o or '')]
    ratios = [b / a for a, b in zip(vals, vals[1:]) if a > 0]
    return round(statistics.median(ratios), 3) if ratios else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', help='only finds written after this local time, e.g. 2026-10-23T22:00')
    ap.add_argument('--results', nargs='*', default=[], help='results jsonl files of the run, for the scenario count')
    a = ap.parse_args()
    since = time.mktime(time.strptime(a.since, '%Y-%m-%dT%H:%M')) if a.since else 0
    families = {}
    for path in sorted(glob.glob(os.path.join(FINDS, 's*.json'))):
        if path.endswith('-min.json') or os.path.getmtime(path) < since:
            continue
        find = json.load(open(path))
        if 'minimal' not in find:
            continue
        family, sig = signature(find)
        fam = families.setdefault(family, {'family': family, 'finds': []})
        fam['finds'].append({'seed': find['seed'], 'client': find['client'], 'repeat_rate': find.get('repeat_rate', '?'),
                             'first': find['first'][:4], 'decay': decay(find['first']), **sig,
                             'min_path': os.path.relpath(path[:-5] + '-min.json', os.path.dirname(HERE))})
    for fam in families.values():
        fam['finds'].sort(key=lambda f: (f['size'], f['ticks']))
        fam['rep'] = fam['finds'][0]
    groups = sorted(families.values(), key=lambda f: -len(f['finds']))
    json.dump(groups, open(os.path.join(OUT, 'groups.json'), 'w'), indent=1)

    verify = {}   # stock first, then the fix jars
    for path in sorted(glob.glob(os.path.join(OUT, 'verify-*.json')), key=lambda p: (not p.endswith('verify-stock.json'), p)):
        verify[os.path.basename(path)[7:-5]] = json.load(open(path))
    scenarios = sum(sum(1 for _ in open(p)) for p in a.results)
    finds = sum(len(g['finds']) for g in groups)
    lines = [f'# Fuzz report {time.strftime("%Y-%m-%d %H:%M")}', '',
             f'{finds} confirmed finds' + (f' in {scenarios} scenarios' if scenarios else '') + f' -> {len(groups)} families', '']
    head = '| family | finds | representative | needs | first offsets | decay | may be the same as |' + ''.join(f' {k} |' for k in verify)
    lines += [head, '|' + '---|' * (7 + len(verify))]
    for g in groups:
        r = g['rep']
        needs = '; '.join(x for x in (','.join(r['blocks']) or 'flat floor', ','.join(r['effects']), ','.join(r['keys']) or 'no keys') if x)
        row = (f'| {g["family"]} | {len(g["finds"])} | s{r["seed"]} ({r["ticks"]} ticks, yaw {r["yaw"]:g}) | {needs} | '
               f'{" ".join(o for _, o in r["first"][:3])} | {r["decay"]} | {related(g, groups)} |')
        for label, res in verify.items():
            v = res.get(g['family'])
            row += f' {v["flagged"]}/{v["runs"]} |' if v else ' - |'
        lines.append(row)
    fixes = [k for k in verify if k != 'stock']
    if 'stock' in verify and fixes:
        lines += ['', 'Fixed by: a family that repeats on stock (2+ of 3) and never with the fix jar.', '']
        for g in groups:
            s = verify['stock'].get(g['family'])
            if not s or s['flagged'] < 2:
                continue
            by = [k for k in fixes if verify[k].get(g['family'], {}).get('flagged', -1) == 0]
            lines.append(f'- {g["family"]}: ' + (', '.join(by) if by else 'nothing yet, new bug'))
    lines += ['', 'Repro for a representative: `python -I fuzz/flaky.py <min_path> <client> 3`; Grim\'s per-tick view:',
              '`python -I fuzz/explain.py <min_path> <client> 3`', '']
    for g in groups:
        lines.append(f'- {g["family"]}: {g["rep"]["min_path"]}  (all: ' + ', '.join(f's{f["seed"]}' for f in g['finds']) + ')')
    open(os.path.join(OUT, 'report.md'), 'w').write('\n'.join(lines) + '\n')
    print('\n'.join(lines[:8 + len(groups)]))


if __name__ == '__main__':
    main()
