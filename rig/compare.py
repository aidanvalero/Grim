# Container (CI-shaped) suite results vs the rig's cached local results, seed by seed: which checks flagged and the exact offsets
#   python -I compare.py <container suite json> <local suite json> [<local confirm json> ...]
import json, sys
from collections import Counter

ci = json.load(open(sys.argv[1]))
local = {}
for path in sys.argv[2:]:
    for seed, runs in json.load(open(path))['results'].items():
        local.setdefault(seed, []).extend(runs)


def summary(runs):
    sig = Counter(tuple(sorted(f'{c} {o}' for c, o in r)) for r in runs)
    return ', '.join(f'{n}/{len(runs)} {" + ".join(k) if k else "no flag"}' for k, n in sig.most_common())


same = 0
for seed in sorted(ci['results'], key=int):
    c, l = ci['results'][seed], local.get(seed, [])
    cs = {tuple(sorted(f'{a} {b}' for a, b in r)) for r in c}
    ls = {tuple(sorted(f'{a} {b}' for a, b in r)) for r in l}
    ok = cs <= ls if l else None
    same += bool(ok)
    print(f'{seed}: container {summary(c)} | local {summary(l) if l else "not run"} | {"match" if ok else "DIFFERENT" if l else "?"}')
print(f'{same}/{len(ci["results"])} seeds: every container run matches a result the local rig produced (same checks, same offsets)')
print(f'container: {ci["minutes"]} min, driver errors {ci["driver_errors"]}, not run {ci["not_run"]}, grim {ci["grim"]}')
