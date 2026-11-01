# Joins the per-runner suite-<build>.json files into one suite-<build>.json per build, in the shape fuzz/suite_diff.py reads
#   python3 merge_shards.py <downloaded artifacts dir> <fuzz/out dir> <build> [<build> ...]
import glob, json, os, sys

src, dst, builds = sys.argv[1], sys.argv[2], sys.argv[3:]
os.makedirs(dst, exist_ok=True)
for b in builds:
    parts = sorted(glob.glob(os.path.join(src, f'suite-{b}-*', f'suite-{b}.json')))
    if not parts:
        sys.exit(f'no shards for {b}')
    out = {'label': b, 'grim': '', 'base': 7000000, 'count': 0, 'seeds': '', 'runs': 0, 'clients': [], 'minutes': 0.0,
           'driver_errors': {}, 'not_run': 0, 'results': {}, 'shards': len(parts)}
    for k, p in enumerate(parts):
        d = json.load(open(p))
        out['grim'] = out['grim'] or d['grim']
        out['runs'] = d['runs']
        out['count'] += d['count']
        out['minutes'] = max(out['minutes'], d['minutes'])          # wall clock: the shards run side by side
        out['clients'] += [f's{k}c{c}' for c in d['clients']]
        out['driver_errors'].update({f's{k}c{c}': n for c, n in d['driver_errors'].items()})
        out['not_run'] += d['not_run']
        out['results'].update(d['results'])
    json.dump(out, open(os.path.join(dst, f'suite-{b}.json'), 'w'), indent=1)
    print(f'{b}: {len(parts)} shards, {len(out["results"])} seeds, {sum(len(v) for v in out["results"].values())} runs, '
          f'{out["not_run"]} not run, longest shard {out["minutes"]} min')
