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
# random seeds only: the known-bug cases (case:<name>, 11-07) are newer than the PC baseline
keys = sorted((k for k in ci['results'] if not k.startswith('case:')), key=int)
for seed in keys:
    c, l = ci['results'][seed], local.get(seed, [])
    cs = {tuple(sorted(f'{a} {b}' for a, b in r)) for r in c}
    ls = {tuple(sorted(f'{a} {b}' for a, b in r)) for r in l}
    ok = cs <= ls if l else None
    same += bool(ok)
    print(f'{seed}: container {summary(c)} | local {summary(l) if l else "not run"} | {"match" if ok else "DIFFERENT" if l else "?"}')
if len(keys) < len(ci['results']):
    print(f'{len(ci["results"]) - len(keys)} known-bug cases not compared, the PC baseline is older than them')
print(f'{same}/{len(keys)} seeds: every container run matches a result the local rig produced (same checks, same offsets)')
print(f'container: {ci["minutes"]} min, driver errors {ci["driver_errors"]}, not run {ci["not_run"]}, grim {ci["grim"]}')
