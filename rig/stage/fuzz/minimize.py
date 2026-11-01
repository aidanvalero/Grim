# Cuts a find down to what it needs: drops one block, effect, key or the camera turns at a time and keeps each drop
# that still flags the same check. The fuzzer calls minimize() on every confirmed find; run alone it writes
# <find>-min.json next to the find.
#   python -I fuzz/minimize.py fuzz/finds/s123.json [client] [runs per try]
import copy, json, os, sys


def drop(sc, kind, i):
    s = copy.deepcopy(sc)
    if kind == 'build':
        s['build'].pop(i)
    elif kind == 'effect':
        s['effects'].pop(i)
    elif kind == 'key':
        s['events'] = [e for e in s['events'] if e[2] != i or e[1] not in ('down', 'up')]
    elif kind == 'turns':
        s['events'] = [e for e in s['events'] if e[1] not in ('look', 'flick')]
    return s


def minimize(sc, still_flags, say=lambda line: None):
    """The smallest scenario still_flags() accepts. The caller owns running it (no import of fuzz here: the fuzzer runs
    as __main__, and a second copy of it would hand out run markers that collide with its own)."""
    for kind, items in (('build', lambda s: range(len(s['build']) - 1, -1, -1)),
                        ('effect', lambda s: range(len(s['effects']) - 1, -1, -1)),
                        ('turns', lambda s: [0] if any(e[1] in ('look', 'flick') for e in s['events']) else []),
                        ('key', lambda s: sorted({e[2] for e in s['events'] if e[1] in ('down', 'up')}))):
        for i in list(items(sc)):
            what = sc['build'][i] if kind == 'build' else sc['effects'][i] if kind == 'effect' else i
            trial = drop(sc, kind, i)
            keep = still_flags(trial)
            say(f'drop {kind} {what}: {"still flags, dropped" if keep else "needed"}')
            if keep:
                sc = trial
    return sc


def load(path, key='scenario'):
    find = json.load(open(path))
    sc = find[key]
    sc['events'] = [tuple(e) for e in sc['events']]
    sc['build'] = [(b[0], tuple(b[1]), b[2]) for b in sc['build']]
    return find, sc


if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import fuzz
    from drive import Rcon
    path, client = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 4
    runs = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    find, sc = load(path)
    checks = set(find['checks'])
    tail, rc = fuzz.Tail(), Rcon()
    tail.start()
    used = [0]

    def once(s):
        used[0] += 1
        return bool({c for c, _ in fuzz.real(fuzz.play(rc, tail, fuzz.fresh(s), client))} & checks)

    if sum(once(sc) for _ in range(3)) < 3:
        sys.exit('the find does not flag 3/3 as stored, nothing to minimize')
    # A try only counts as still flagging if all its runs do, so a flaky find keeps an element it doesn't need
    # rather than losing one it does
    sc = minimize(sc, lambda s: all(once(s) for _ in range(runs)), say=lambda line: print(line, flush=True))
    ok = sum(once(sc) for _ in range(3))
    out = path[:-5] + '-min.json'
    json.dump(dict(find, scenario=sc, minimized=True, min_confirms=f'{ok}/3'), open(out, 'w'), indent=1)
    print(f'minimal: build={sc["build"]} effects={sc["effects"]} yaw={sc["yaw"]} ticks={sc["ticks"]}')
    print(f'keys: {[e for e in sc["events"] if e[1] in ("down", "up")]}')
    print(f'flags {ok}/3 at the end; {used[0]} runs; wrote {out}')
