# Lines up two suite.py runs seed by seed: which scenarios flag on one build and not the other. Reruns of the seeds
# that differed (suite.py <label>-confirm --seeds ...) are merged in when they exist. A check is "gone" or "new" only
# when the difference holds up (Fisher exact, p < 0.05 two-sided); anything else that differs is "noisy".
#   python -I fuzz/suite_diff.py <stock label> <pr label> [--title "PR #2981 perf: ..."]   writes fuzz/out/suite-diff-<pr label>.md
#   python -I fuzz/suite_diff.py <stock label> <pr label> --differing    prints the seeds that differ, comma-separated
import argparse, json, os, sys
from math import comb
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cases
import fuzz

ap = argparse.ArgumentParser()
ap.add_argument('stock')
ap.add_argument('pr')
ap.add_argument('--title', default='')
ap.add_argument('--differing', action='store_true')
ap.add_argument('--confirm-suffix', default='-confirm', help='reruns are read from suite-<label><suffix>.json')
a = ap.parse_args()


def load(label):
    d = json.load(open(os.path.join(fuzz.OUT, f'suite-{label}.json')))
    conf = os.path.join(fuzz.OUT, f'suite-{label}{a.confirm_suffix}.json')
    d['confirm_runs'] = 0
    if os.path.exists(conf):
        c = json.load(open(conf))
        for seed, runs in c['results'].items():
            d['results'].setdefault(seed, []).extend(runs)
            d['confirm_runs'] += len(runs)
        d['driver_errors'] = {k: d['driver_errors'].get(k, 0) + v for k, v in c['driver_errors'].items()}
    return d


def fisher(a_, b_, c_, d_):
    """two-sided Fisher exact p for [[a, b], [c, d]]"""
    n1, n2, k = a_ + b_, c_ + d_, a_ + c_
    def p(x):
        return comb(n1, x) * comb(n2, k - x) / comb(n1 + n2, k)
    p0 = p(a_)
    return min(1.0, sum(p(x) for x in range(max(0, k - n2), min(k, n1) + 1) if p(x) <= p0 * (1 + 1e-9)))


S, P = load(a.stock), load(a.pr)


def rates(runs):
    """check -> (runs that flagged it, total runs, one offset seen)"""
    out = {}
    for r in runs:
        for c in {c for c, _ in r}:
            off = next(o for cc, o in r if cc == c)
            n, _, o = out.get(c, (0, 0, off))
            out[c] = (n + 1, len(runs), o)
    return out


def order(key):
    """random seeds in number order, then the known-bug cases (case:<name>) by name"""
    return (1, 0, key) if key.startswith('case:') else (0, int(key), '')


gone, new, noisy, same_flag = [], [], [], 0
seeds = sorted(set(S['results']) & set(P['results']), key=order)
for seed in seeds:
    s, p = rates(S['results'][seed]), rates(P['results'][seed])
    ns, np_ = len(S['results'][seed]), len(P['results'][seed])
    for c in sorted(set(s) | set(p)):
        sn, pn = s.get(c, (0, ns, ''))[0], p.get(c, (0, np_, ''))[0]
        pv = fisher(sn, ns - sn, pn, np_ - pn)
        row = (seed, c, f'{sn}/{ns}', f'{pn}/{np_}', f'{pv:.3f}', (s.get(c) or p.get(c))[2])
        if sn / ns == pn / np_:
            same_flag += 1
        elif pv < 0.05:
            (gone if sn / ns > pn / np_ else new).append(row)
        else:
            noisy.append(row)

if a.differing:
    # the confirm job only has 26.3 clients, so old client cases aren't rerun there (the old job runs them 5 times)
    keys = {str(r[0]) for r in gone + new + noisy}
    print(','.join(sorted(k for k in keys if not k.startswith('case:') or cases.version(k[5:]) == '26.3')))
    sys.exit(0)


# the headline counts are the random seeds only: those have to stay at 0, and the cases flag on 2.0 on purpose
def runs_with_flag(d):
    return sum(1 for k, v in d['results'].items() if not k.startswith('case:') for r in v if r)


def total(d):
    return sum(len(v) for k, v in d['results'].items() if not k.startswith('case:'))


def repro(key):
    if key.startswith('case:'):
        return f'python -I fuzz/suite.py repro --seeds {key} --runs 1 --clients 1'
    return f'python -I fuzz/fuzz.py repro --seed {key}'


case_keys = [k for k in seeds if k.startswith('case:')]
n_random = len(seeds) - len(case_keys)
older = sorted({cases.version(k[5:]) for k in case_keys} - {'26.3'})
lines = [f'# Real-client regression net: {a.title or a.pr}', '',
         f'Same {n_random} random scenarios (seeds {S["base"]}-{S["base"] + S["count"] - 1}, fuzz.scenario)'
         + (f' and {len(case_keys)} known-bug cases (fuzz/cases.py)' if case_keys else '')
         + f' on both builds, {S["runs"]} runs each, {len(S["clients"])} vanilla clients, 26.3'
         + (f' plus {", ".join(older)} through ViaBackwards for the old client cases (5 runs each)' if older else '') + '.', '',
         '| | stock | PR |', '|---|---|---|',
         f'| build | {S["grim"]} | {P["grim"]} |',
         f'| runs | {total(S)} | {total(P)} |',
         f'| runs with any flag | {runs_with_flag(S)} | {runs_with_flag(P)} |',
         f'| minutes | {S["minutes"]} | {P["minutes"]} |',
         f'| reruns of differing seeds | {S["confirm_runs"]} | {P["confirm_runs"]} |',
         f'| driver errors / seeds not run | {sum(S["driver_errors"].values())} / {S["not_run"]} | '
         f'{sum(P["driver_errors"].values())} / {P["not_run"]} |', '',
         f'- gone (flags less on the PR, Fisher p < 0.05): **{len(gone)}**',
         f'- new (flags more on the PR, Fisher p < 0.05): **{len(new)}**',
         f'- noisy (differs, not significant): {len(noisy)}',
         f'- same flag rate on both: {same_flag}', '']
if case_keys:
    # every case on both builds, changed or not: this is the part that says which known bugs a build still has
    def flagged(runs):
        hit = [r for r in runs if r]
        checks = sorted({c for r in hit for c, _ in r})
        return f'{len(hit)}/{len(runs)}' + (f' {", ".join(checks)}' if checks else '')

    lines += ['## Known-bug cases', '', '| case | stock | PR | expected |', '|---|---|---|---|']
    for k in case_keys:
        name = k[5:]
        lines.append(f'| {name} | {flagged(S["results"][k])} | {flagged(P["results"][k])} | '
                     f'{cases.CASES[name]["expect"] if name in cases.CASES else "?"} |')
    lines.append('')
for name, rows in (('New on the PR', new), ('Gone on the PR', gone), ('Noisy', noisy)):
    if rows:
        lines += [f'## {name}', '', '| seed | check | stock | PR | p | offset seen | repro |', '|---|---|---|---|---|---|---|']
        lines += [f'| {sd} | {c} | {st} | {pr} | {pv} | {o} | `{repro(sd)}` |' for sd, c, st, pr, pv, o in rows]
        lines.append('')
path = os.path.join(fuzz.OUT, f'suite-diff-{a.pr}.md')
open(path, 'w', encoding='utf-8').write('\n'.join(lines))
print('\n'.join(lines[:16]))
print('report:', path)
