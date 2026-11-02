# Fuzzer v0: random movement scenarios on N real vanilla clients against Grim. A vanilla client can't cheat, so any
# Grim flag here is a false flag. Every scenario is rebuilt from its seed (terrain, effects, yaw, keys), so a find
# reruns exactly; a candidate only counts once a rerun flags the same check again.
#
#   pwsh -File fuzz\launch.ps1 -Jar grim-jars\<jar> -N 4      server + N hidden clients
#   python -I fuzz/fuzz.py setup 4                            plots, gamerules
#   python -I fuzz/fuzz.py run --clients 4 --minutes 30       fuzz; results in fuzz/out/, finds in fuzz/finds/
#   python -I fuzz/fuzz.py repro --seed S [--client 1] [--ticks K]
import argparse, collections, itertools, json, os, random, re, subprocess, sys, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'cobweb'))
sys.path.insert(0, HERE)   # python -I leaves the script's own folder off the path
from drive import Rcon, JAVA, HERE as COBWEB, SERVER_LOG, ANSI   # noqa: E402
from minimize import minimize   # noqa: E402

OUT = os.path.join(HERE, 'out')
FINDS = os.path.join(HERE, 'finds')
# superflat grass top; players stand at y=-60. FUZZ_FLOOR_Y=99 builds the plots in the air instead, for clients that only
# get y 0-255 through ViaBackwards (1.12.2 for #2858, 10-31)
FLOOR_Y = int(os.environ.get('FUZZ_FLOOR_Y', '-61'))
FLOOR = ['ice', 'packed_ice', 'blue_ice', 'soul_sand', 'honey_block', 'slime_block', 'mud', 'stone']
OBSTACLES = ['stone_slab[type=bottom]', 'stone_slab[type=top]', 'oak_stairs[facing=north]', 'oak_stairs[facing=east]',
             'oak_trapdoor[half=bottom,open=false]', 'oak_trapdoor[half=top,open=false]', 'oak_fence', 'cobblestone_wall',
             'iron_bars', 'glass_pane', 'white_carpet', 'snow[layers=2]', 'snow[layers=5]', 'scaffolding', 'cobweb',
             'powder_snow', 'water', 'soul_sand', 'honey_block', 'slime_block', 'stone', 'ladder', 'oak_fence_gate[open=true]',
             'lantern', 'chain', 'composter', 'cauldron', 'hopper', 'end_rod', 'bubble_column']
EFFECTS = [('speed', 2), ('slowness', 1), ('jump_boost', 2), ('slow_falling', 0)]
SPECIAL_YAWS = [-376.30127, 736.9, -123.4, 0.0, 45.0, 90.0, -90.0, 180.0]
FLAG = re.compile(r'Grim \S+ AidanFuzz(\d+) failed (\w+) \(x(\d+)\) ?(\S*)')
MARK = re.compile(r'\[Rcon\] FUZZ c(\d+) s(\d+) (start|end)')
TIMER = {'TimerLimit', 'Timer', 'NegativeTimer'}


def origin(client):
    return 1000 + 48 * (client - 1), 1000


def scenario(seed, ticks=None):
    r = random.Random(seed)
    build = []                                             # (kind, coords relative to the plot origin, block)
    w, d = r.randint(1, 7), r.randint(1, 7)
    x0, z0 = r.randint(-5, 2), r.randint(-5, 2)
    build.append(('fill', (x0, 0, z0, x0 + w - 1, 0, z0 + d - 1), r.choice(FLOOR)))
    if r.random() < 0.3:                                    # a pit: crouch edges and drops
        px, pz = r.randint(-5, 3), r.randint(-5, 3)
        build.append(('fill', (px, -1, pz, px + r.randint(0, 3), 0, pz + r.randint(0, 3)), 'air'))
    for _ in range(r.randint(0, 7)):
        x, z, block = r.randint(-5, 5), r.randint(-5, 5), r.choice(OBSTACLES)
        if (x, z) == (0, 0):
            continue
        if block == 'ladder':
            build += [('set', (x, 1, z + 1), 'stone'), ('set', (x, 2, z + 1), 'stone'),
                      ('set', (x, 1, z), 'ladder[facing=north]'), ('set', (x, 2, z), 'ladder[facing=north]')]
        elif block == 'bubble_column':
            build += [('set', (x, 0, z), 'soul_sand'), ('set', (x, 1, z), 'water'), ('set', (x, 2, z), 'water')]
        else:
            build.append(('set', (x, 1, z), block))
    if r.random() < 0.25:                                   # a ceiling: head hitters
        cy = r.choice([3, 4])
        cx, cz = r.randint(-4, 1), r.randint(-4, 1)
        build.append(('fill', (cx, cy, cz, cx + r.randint(1, 4), cy, cz + r.randint(1, 4)), r.choice(['stone', 'stone_slab[type=bottom]'])))
    effects = [(name, r.randint(0, amp)) for name, amp in EFFECTS if r.random() < 0.25]
    yaw = r.choice(SPECIAL_YAWS) if r.random() < 0.3 else round(r.uniform(-1080, 1080), 5)

    full = r.randint(100, 220)                              # always drawn, so a shrunk rerun keeps the same random stream
    total = full if ticks is None else min(ticks, full)
    events, held, t, cur = [], set(), 0, yaw
    while t < full:
        seg = r.randint(4, 30)
        want = set()
        if r.random() < 0.75:
            want.add('forward')
        elif r.random() < 0.3:
            want.add('back')
        if r.random() < 0.3:
            want.add(r.choice(['left', 'right']))
        if 'forward' in want and r.random() < 0.45:
            want.add('sprint')
        if r.random() < 0.3:
            want.add('sneak')
        j = r.random()
        if j < 0.2:
            want.add('jump')
        for k in sorted(held - want):
            events.append((t, 'up', k))
        for k in sorted(want - held):
            events.append((t, 'down', k))
        held = want
        if 0.2 <= j < 0.5 and 'jump' not in want:
            tt = t + r.randint(0, 3)
            while tt < t + seg - 4:
                events.append((tt, 'down', 'jump')); events.append((tt + r.randint(1, 3), 'up', 'jump'))
                tt += r.randint(5, 12)
        if r.random() < 0.2:
            cur = round(cur + r.uniform(-120, 120), 5)
            events.append((t, 'look', f'{cur} 0'))
        elif r.random() < 0.08:                             # a one-tick flick, like a PvP player snapping onto a target
            cur = round(cur + r.choice([-1, 1]) * r.uniform(60, 150), 5)
            events.append((t, 'flick', f'{cur} 0'))
        t += seg
    events = sorted((e for e in events if e[0] < total), key=lambda e: e[0])
    down = set()                                            # whatever is still held at the cut gets released there
    for _, action, arg in events:
        if action == 'down':
            down.add(arg)
        elif action == 'up':
            down.discard(arg)
    for k in sorted(down):
        events.append((total, 'up', k))
    return {'seed': seed, 'build': build, 'effects': effects, 'yaw': yaw, 'ticks': total, 'events': events}


def build_world(rc, sc, client):
    ox, oz = origin(client)
    p = f'AidanFuzz{client}'
    rc(f'fill {ox - 10} {FLOOR_Y + 1} {oz - 10} {ox + 10} {FLOOR_Y + 7} {oz + 10} minecraft:air')
    rc(f'fill {ox - 10} {FLOOR_Y - 2} {oz - 10} {ox + 10} {FLOOR_Y - 1} {oz + 10} minecraft:dirt')
    rc(f'fill {ox - 10} {FLOOR_Y} {oz - 10} {ox + 10} {FLOOR_Y} {oz + 10} minecraft:grass_block')
    for kind, c, block in sc['build']:
        if kind == 'fill':
            rc(f'fill {ox + c[0]} {FLOOR_Y + c[1]} {oz + c[2]} {ox + c[3]} {FLOOR_Y + c[4]} {oz + c[5]} minecraft:{block}')
        else:
            rc(f'setblock {ox + c[0]} {FLOOR_Y + c[1]} {oz + c[2]} minecraft:{block}')
    rc(f'effect clear {p}')
    for name, amp in sc['effects']:
        rc(f'effect give {p} minecraft:{name} 120 {amp} true')


MAX_TURN = 30.0                    # degrees per tick: a fast spin, never a one-tick snap (that trips AimModulo360)


def turn(lines, t, cur, target):
    """Rotate from cur to target at most MAX_TURN per tick starting at tick t; returns (next free tick, yaw)."""
    while abs(target - cur) > 1e-9:
        cur = target if abs(target - cur) <= MAX_TURN else round(cur + (MAX_TURN if target > cur else -MAX_TURN), 5)
        lines.append((t, f'{t} look {cur} 0'))
        t += 1
    return t, cur


def schedule(sc, client):
    ox, oz = origin(client)
    p = f'AidanFuzz{client}'
    lines = [(0, f'0 rcon tp {p} {ox + 0.5} {FLOOR_Y + 1} {oz + 0.5} 0 0'), (1, f'1 sync {ox + 0.5} {FLOOR_Y + 1}.0 {oz + 0.5}')]
    t0, cur = turn(lines, 2, 0.0, sc['yaw'])
    t0 += 2
    lines.append((t0 - 1, f'{t0 - 1} rcon say FUZZ c{client} s{sc["seed"]} start'))
    for t, action, arg in sc['events']:
        if action == 'look':                                # mid-run turns are spread over ticks too
            _, cur = turn(lines, t + t0, cur, float(arg.split()[0]))
        elif action == 'flick':                             # except flicks, which land in one tick on purpose
            cur = float(arg.split()[0])
            lines.append((t + t0, f'{t + t0} look {arg}'))
        else:
            lines.append((t + t0, f'{t + t0} {action} {arg}'))
    end = sc['ticks'] + t0 + 20
    lines += [(end, f'{end} rcon say FUZZ c{client} s{sc["seed"]} end'), (end + 6, f'{end + 6} end')]
    return '\n'.join(line for _, line in sorted(lines, key=lambda x: x[0])) + '\n'


class Tail(threading.Thread):
    """Reads the server log once for all clients and files each flag under the scenario that was open for its player."""
    def __init__(self):
        super().__init__(daemon=True)
        self.f = open(SERVER_LOG, encoding='utf-8', errors='replace')
        self.f.seek(0, 2)
        self.open, self.flags, self.closed = {}, collections.defaultdict(list), set()
        self.lock = threading.Lock()

    def run(self):
        while True:
            line = self.f.readline()
            if not line:
                time.sleep(0.2)
                continue
            line = ANSI.sub('', line)
            with self.lock:
                if m := MARK.search(line):
                    c, s, what = int(m.group(1)), int(m.group(2)), m.group(3)
                    if what == 'start':
                        self.open[c] = s
                    else:
                        self.open.pop(c, None)
                        self.closed.add((c, s))
                elif m := FLAG.search(line):
                    c = int(m.group(1))
                    self.flags[(c, self.open.get(c))].append((m.group(2), m.group(4)))

    def result(self, client, seed, timeout=8):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                if (client, seed) in self.closed:
                    return list(self.flags.pop((client, seed), []))
            time.sleep(0.2)
        with self.lock:
            return list(self.flags.pop((client, seed), [])) + [('NoEndMarker', '')]


RETRY_TIMER = int(os.environ.get('FUZZ_RETRY_TIMER', '0'))


def play(rc, tail, sc, client):
    """One scenario on one client. With FUZZ_RETRY_TIMER=N a run that trips a Timer check is void: the client stalled
    and then sent its ticks in a burst, and on the PC every scored run like that in October's suites flagged falsely
    (all 6 in pr2312-final, client 3, one session, 0/48 on rerun). It reruns under a fresh marker up to N times and
    comes back as Unpaced if every try stalled. Voided runs go to out/voided.jsonl."""
    cur = sc
    for attempt in range(RETRY_TIMER + 1):
        if attempt:
            cur = fresh(sc)
        flags = play_once(rc, tail, cur, client)
        if not RETRY_TIMER or not any(f[0] in TIMER for f in flags):
            return flags
        with open(os.path.join(OUT, 'voided.jsonl'), 'a') as f:
            f.write(json.dumps({'client': client, 'marker': cur['seed'], 'flags': flags}) + '\n')
    return [('Unpaced', '')]


def play_once(rc, tail, sc, client):
    build_world(rc, sc, client)
    path = os.path.join(OUT, f'sched-c{client}.txt')
    with open(path, 'w') as f:
        f.write(schedule(sc, client))
    env = dict(os.environ, JDWP_PORT=str(5005 + client))
    try:
        names = [os.path.join(COBWEB, os.environ['NAMES'])] if os.environ.get('NAMES') else []   # pre-26.1 clients
        p = subprocess.run([JAVA, '-cp', COBWEB, 'KeyDriver', path] + names, env=env, capture_output=True, text=True,
                           timeout=sc['ticks'] / 20 + 90)
        driver = p.stdout.strip().splitlines()[-1:] if p.stdout else []
        if p.returncode:
            return [('DriverError', (p.stdout + p.stderr).strip()[-160:])]
    except subprocess.TimeoutExpired:
        return [('DriverTimeout', '')]
    return tail.result(client, sc['seed'])


def real(flags):
    return [f for f in flags if f[0] not in TIMER and f[0] not in ('NoEndMarker', 'DriverError', 'DriverTimeout', 'Unpaced')]


_marks = itertools.count(1)


def fresh(sc):
    """The same scenario under a marker no run has used yet. A rerun under its old marker isn't waited for: the tail
    already saw that marker's end line, so flags from the rerun's last ticks could be missed or land on the next run."""
    return dict(sc, seed=900_000_000 + next(_marks))


def shrink(rc, tail, seed, client, checks, ticks):
    """Cut the key timeline down while the same check still fires; returns the shortest tick count that kept it."""
    best, lo, hi = ticks, 10, ticks
    for _ in range(5):
        if hi - lo < 8:
            break
        mid = (lo + hi) // 2
        got = {c for c, _ in real(play(rc, tail, fresh(scenario(seed, ticks=mid)), client))}
        if got & checks:
            best, hi = mid, mid
        else:
            lo = mid
    return best


def worker(client, base, until, tail, log, stats):
    rc = Rcon()
    n, failing = 0, 0
    play(rc, tail, scenario(client * 1_000_000 - 1, ticks=20), client)   # warm-up: first trip to the plot isn't scored
    while time.time() < until:
        seed = base + client * 1_000_000 + n
        n += 1
        sc = scenario(seed)
        flags = play(rc, tail, sc, client)
        rec = {'t': time.strftime('%H:%M:%S'), 'client': client, 'seed': seed, 'ticks': sc['ticks'], 'flags': flags}
        stats[client]['scenarios'] += 1
        if any(f[0] in ('DriverError', 'DriverTimeout', 'Unpaced') for f in flags):
            stats[client]['errors'] += 1
            failing += 1
            if failing >= 3:   # crashed or disconnected client: restart just this one, the others keep going
                stats[client]['relaunches'] += 1
                try:
                    r = subprocess.run(['pwsh', '-NoProfile', '-File', os.path.join(HERE, 'launch.ps1'), '-Only', str(client)],
                                       capture_output=True, text=True, timeout=400)
                    rec['relaunch'] = (r.stdout or r.stderr).strip()[-120:]
                except FileNotFoundError:   # the CI image has no pwsh to relaunch with: this client stops, the other goes on
                    rec['relaunch'], rec['gave_up'] = 'no pwsh to relaunch with', True
                    until = 0
                failing = 0
                if stats[client]['relaunches'] > 5:
                    rec['gave_up'] = True
                    until = 0
        else:
            failing = 0
        hits = real(flags)
        if hits:
            stats[client]['candidates'] += 1
            reruns = [real(play(rc, tail, fresh(scenario(seed)), client)) for _ in range(3)]
            first = {c for c, _ in hits}
            repeats = sum(1 for again in reruns if first & {c for c, _ in again})
            again = next((a for a in reruns if first & {c for c, _ in a}), [])
            checks = first & {c for c, _ in again}
            rec['rerun'], rec['repeat_rate'] = again, f'{repeats}/3'
            if repeats >= 2:
                stats[client]['confirmed'] += 1
                rec['shrunk_ticks'] = shrink(rc, tail, seed, client, checks, sc['ticks'])
                shrunk = scenario(seed, ticks=rec['shrunk_ticks'])
                used = [0]

                def still(s):
                    used[0] += 1
                    return bool({c for c, _ in real(play(rc, tail, fresh(s), client))} & checks)

                minimal = minimize(shrunk, still)
                rec['minimize_runs'] = used[0]
                find = {'seed': seed, 'client': client, 'checks': sorted(checks), 'repeat_rate': rec['repeat_rate'],
                        'first': hits, 'rerun': again, 'shrunk_ticks': rec['shrunk_ticks'], 'scenario': shrunk,
                        'minimal': minimal,
                        'repro': f'python -I fuzz/flaky.py fuzz/finds/s{seed}-min.json {client} 3'}
                os.makedirs(FINDS, exist_ok=True)
                with open(os.path.join(FINDS, f's{seed}.json'), 'w') as f:
                    json.dump(find, f, indent=1)
                with open(os.path.join(FINDS, f's{seed}-min.json'), 'w') as f:
                    json.dump(dict(find, scenario=minimal, minimized=True), f, indent=1)
        with log['lock']:
            log['f'].write(json.dumps(rec) + '\n')
            log['f'].flush()


def setup(n):
    rc = Rcon()
    for c in range(1, n + 1):
        ox, oz = origin(c)
        print(rc(f'forceload add {ox - 16} {oz - 16} {ox + 16} {oz + 16}').strip())
        print(rc(f'gamemode survival AidanFuzz{c}').strip())
    for rule in ('fallDamage', 'fall_damage', 'drowningDamage', 'drowning_damage', 'freezeDamage', 'freeze_damage',
                 'fireDamage', 'fire_damage'):
        print(rule, rc(f'gamerule {rule} false').strip()[:60])
    for rule in ('doImmediateRespawn', 'immediate_respawn', 'doDaylightCycle', 'advance_time'):
        print(rule, rc(f'gamerule {rule} {"true" if "spawn" in rule.lower() else "false"}').strip()[:60])
    print(rc('difficulty peaceful').strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['setup', 'run', 'repro'])
    ap.add_argument('n', nargs='?', type=int, default=4)
    ap.add_argument('--clients', type=int, default=4)
    ap.add_argument('--minutes', type=float, default=30)
    ap.add_argument('--seed', type=int, default=int(time.time()) % 100000)
    ap.add_argument('--client', type=int, default=1)
    ap.add_argument('--ticks', type=int)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    if a.cmd == 'setup':
        return setup(a.n)
    tail = Tail()
    tail.start()
    if a.cmd == 'repro':
        flags = play(Rcon(), tail, scenario(a.seed, ticks=a.ticks), a.client)
        print(json.dumps(flags))
        return
    until = time.time() + a.minutes * 60
    stats = {c: collections.Counter() for c in range(1, a.clients + 1)}
    log = {'f': open(os.path.join(OUT, f'results-{time.strftime("%m%d-%H%M")}.jsonl'), 'w'), 'lock': threading.Lock()}
    threads = [threading.Thread(target=worker, args=(c, a.seed, until, tail, log, stats), daemon=True) for c in stats]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    hours = (time.time() - t0) / 3600
    total = sum(s['scenarios'] for s in stats.values())
    print(f'{total} scenarios in {hours * 60:.1f} min = {total / hours:.0f}/hour ({total / hours / a.clients:.0f}/hour/client); '
          f'candidates {sum(s["candidates"] for s in stats.values())}, confirmed {sum(s["confirmed"] for s in stats.values())}, '
          f'driver errors {sum(s["errors"] for s in stats.values())}')
    for c, s in stats.items():
        print(f'  client {c}: {dict(s)}')


if __name__ == '__main__':
    main()
