# PR safety net (10-27): one fixed list of random scenarios (fuzz.scenario(seed), seeds base .. base+count-1) on
# whatever jar the server is running, every seed `runs` times, spread over the clients. Run it once on stock 2.0 and
# once on a PR build at the same commit, then suite_diff.py lines the two up seed by seed. cobweb/pr-check.ps1 does all of it.
# 11-07: --cases adds the known-bug cases from cases.py, filed under case:<name>. --seeds takes case:<name> too, which
# is how the confirm job reruns a case that differed.
#   python -I fuzz/suite.py <label> [--base 7000000] [--count 120] [--runs 2] [--clients 1,2,3,4] [--cases all|a,b|shard:K/N]
#   saves fuzz/out/suite-<label>.json
import argparse, json, os, queue, re, subprocess, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cases
import fuzz
from drive import Rcon

ap = argparse.ArgumentParser()
ap.add_argument('label')
ap.add_argument('--base', type=int, default=7_000_000)
ap.add_argument('--count', type=int, default=120)
ap.add_argument('--runs', type=int, default=2)
ap.add_argument('--clients', default='1,2,3,4')
ap.add_argument('--seeds', default='', help='comma-separated seeds (or case:<name>) instead of base..base+count-1')
ap.add_argument('--cases', default='', help="known-bug cases from cases.py: all, old, a,b, or shard:K/N")
ap.add_argument('--client-versions', default='', help='mixed versions, e.g. 1=1.14.4,2=1.21.8 (a client left out is 26.3); '
                                                       'a client only takes the seeds and cases for its version')
a = ap.parse_args()
clients = [int(c) for c in a.clients.split(',')]
versions = dict(x.split('=') for x in a.client_versions.split(',') if x)

tokens = [x for x in a.seeds.split(',') if x]
seed_list = [int(x) for x in tokens if not x.startswith('case:')]
case_list = list(dict.fromkeys([x[5:] for x in tokens if x.startswith('case:')] + cases.pick(a.cases)))
cases.pick(','.join(case_list))                     # fails here on a name cases.py doesn't have
if not tokens and not a.cases:
    seed_list = [a.base + i for i in range(a.count)]
queues = {}                                         # one per client version: random seeds and new cases are 26.3
for k in range(a.runs):
    for key in seed_list + [f'case:{c}' for c in case_list]:
        v = cases.version(key[5:]) if isinstance(key, str) else '26.3'
        queues.setdefault(v, queue.Queue()).put((key, k))
tail = fuzz.Tail()
tail.start()
results, lock = {}, threading.Lock()
errors = {c: 0 for c in clients}
GIVE_UP = 15                          # driver errors before a client stops taking work (its seeds go to the others)


def run(client):
    rc = Rcon()
    work = queues.setdefault(versions.get(str(client), '26.3'), queue.Queue())
    fuzz.play(rc, tail, fuzz.scenario(client * 1_000_000 - 1, ticks=20), client)   # warm-up: first trip isn't scored
    failing = 0
    while errors[client] <= GIVE_UP:
        try:
            seed, k = work.get_nowait()
        except queue.Empty:
            return
        if isinstance(seed, str):        # a known-bug case: its own setup and cleanup around the scenario
            sc, before, after = cases.build(seed[5:], client)
            for cmd in before:
                rc(cmd)
            try:
                flags = fuzz.play(rc, tail, fuzz.fresh(sc), client)
            finally:
                for cmd in after:
                    rc(cmd)
        else:
            flags = fuzz.play(rc, tail, fuzz.fresh(fuzz.scenario(seed)), client)
        if any(f[0] in ('DriverError', 'DriverTimeout', 'Unpaced') for f in flags):
            errors[client] += 1
            failing += 1
            work.put((seed, k))          # someone runs it again later
            if failing >= 3:             # crashed or disconnected client: restart just this one
                try:
                    subprocess.run(['pwsh', '-NoProfile', '-File', os.path.join(fuzz.HERE, 'launch.ps1'), '-Only', str(client)],
                                   capture_output=True, text=True, timeout=400)
                except FileNotFoundError:   # the CI image has no pwsh: this client stops taking work, its seeds go to the other
                    return
                failing = 0
            continue
        failing = 0
        with lock:
            results.setdefault(str(seed), []).append([list(f) for f in fuzz.real(flags)])


t0 = time.time()
threads = [threading.Thread(target=run, args=(c,)) for c in clients]
for t in threads:
    t.start()
for t in threads:
    t.join()
grim = ''
try:
    m = re.search(r'GrimAC v(\S+)', open(fuzz.SERVER_LOG, encoding='utf-8', errors='replace').read())
    grim = m.group(1) if m else ''
except OSError:
    pass
left = sum(q.qsize() for q in queues.values())     # includes cases for a version no client here runs
out = {'label': a.label, 'grim': grim, 'base': a.base, 'count': len(seed_list), 'seeds': a.seeds, 'cases': case_list,
       'runs': a.runs, 'clients': clients, 'minutes': round((time.time() - t0) / 60, 1), 'driver_errors': errors,
       'not_run': left, 'results': results}
json.dump(out, open(os.path.join(fuzz.OUT, f'suite-{a.label}.json'), 'w'), indent=1)
done = sum(len(v) for v in results.values())
flagged = sum(1 for v in results.values() for r in v if r)
print(f'{a.label} ({grim}): {done} runs of {len(seed_list)} seeds and {len(case_list)} cases in {out["minutes"]} min, '
      f'{flagged} with a flag, driver errors {sum(errors.values())}, not run {left}')
