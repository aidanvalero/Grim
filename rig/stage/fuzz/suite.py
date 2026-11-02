# PR safety net (10-27): one fixed list of random scenarios (fuzz.scenario(seed), seeds base .. base+count-1) on
# whatever jar the server is running, every seed `runs` times, spread over the clients. Run it once on stock 2.0 and
# once on a PR build at the same commit, then suite_diff.py lines the two up seed by seed. cobweb/pr-check.ps1 does all of it.
#   python -I fuzz/suite.py <label> [--base 7000000] [--count 120] [--runs 2] [--clients 1,2,3,4]
#   saves fuzz/out/suite-<label>.json
import argparse, json, os, queue, re, subprocess, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fuzz
from drive import Rcon

ap = argparse.ArgumentParser()
ap.add_argument('label')
ap.add_argument('--base', type=int, default=7_000_000)
ap.add_argument('--count', type=int, default=120)
ap.add_argument('--runs', type=int, default=2)
ap.add_argument('--clients', default='1,2,3,4')
ap.add_argument('--seeds', default='', help='comma-separated seeds instead of base..base+count-1')
a = ap.parse_args()
clients = [int(c) for c in a.clients.split(',')]

work = queue.Queue()
seed_list = [int(x) for x in a.seeds.split(',') if x] or [a.base + i for i in range(a.count)]
for k in range(a.runs):
    for sd in seed_list:
        work.put((sd, k))
tail = fuzz.Tail()
tail.start()
results, lock = {}, threading.Lock()
errors = {c: 0 for c in clients}
GIVE_UP = 15                          # driver errors before a client stops taking work (its seeds go to the others)


def run(client):
    rc = Rcon()
    fuzz.play(rc, tail, fuzz.scenario(client * 1_000_000 - 1, ticks=20), client)   # warm-up: first trip isn't scored
    failing = 0
    while errors[client] <= GIVE_UP:
        try:
            seed, k = work.get_nowait()
        except queue.Empty:
            return
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
left = work.qsize()
out = {'label': a.label, 'grim': grim, 'base': a.base, 'count': len(seed_list), 'seeds': a.seeds, 'runs': a.runs, 'clients': clients,
       'minutes': round((time.time() - t0) / 60, 1), 'driver_errors': errors, 'not_run': left, 'results': results}
json.dump(out, open(os.path.join(fuzz.OUT, f'suite-{a.label}.json'), 'w'), indent=1)
done = sum(len(v) for v in results.values())
flagged = sum(1 for v in results.values() for r in v if r)
print(f'{a.label} ({grim}): {done} runs of {a.count} seeds in {out["minutes"]} min, {flagged} with a flag, '
      f'driver errors {sum(errors.values())}, not run {left}')
