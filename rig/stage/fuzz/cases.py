# Known bugs as fixed suite cases (11-07). Each one is a bug the rig reproduced for a fix, kept as one scenario a client
# runs next to the random seeds: suite.py --cases runs them and files the results under case:<name>, so suite_diff.py
# lines them up between two builds the same way as seeds. A case flags on 2.0 until its fix merges and has to stay at 0
# after, and a PR that fixes one shows it as gone. A case with a 'version' needs that client (through ViaBackwards on
# the 26.3 server, rig/run_old.sh), the rest run on 26.3. The old client ones guard fixes that are in already: they
# flag on a 2.0 from before the fix and have to stay at 0 from it on.
#   build(name, client) -> (scenario, rcon commands to run before it, rcon commands to run after it)
#   pick('all' | 'old' | 'a,b' | 'shard:K/N') -> case names. shard:K/N is every Nth 26.3 case starting at K, old is
#   every case that needs an older client
import fuzz

NETH5 = 'minecraft:netherite_pickaxe[minecraft:enchantments={"minecraft:efficiency":5}]'


def dig_down(client, height=60, y0=100):
    # fastbreak_sweep '1281 dig down 60, haste II': on a stone pillar, digging straight down with haste II and an eff V
    # netherite pick. 2.0 d8d7b37: 3/3 runs flag FastBreak, then GroundSpoof and Simulation as the cancelled blocks come back
    ox, oz = fuzz.origin(client)
    p = f'AidanFuzz{client}'
    ev = [(1, 'grab', 'on')] + [(2, 'rcon', f'item replace entity {p} hotbar.{h} with {NETH5}') for h in range(9)]
    # bedrock under the pillar so a finished dig stops at y0; the tp puts the player on top looking straight down
    ev += [(4, 'rcon', f'setblock {ox} {y0 - 1} {oz} minecraft:bedrock'),
           (4, 'rcon', f'fill {ox} {y0} {oz} {ox} {y0 + height - 1} {oz} minecraft:stone'),
           (6, 'rcon', f'tp {p} {ox + 0.5} {y0 + height} {oz + 0.5} 0 90'),
           (20, 'down', 'attack'), (20 + 11 * height, 'up', 'attack'), (30 + 11 * height, 'grab', 'off')]
    sc = {'build': [], 'effects': [('haste', 1)], 'yaw': 0.0, 'ticks': 36 + 11 * height, 'events': ev}
    items = f'kill @e[type=minecraft:item,x={ox - 12},y={fuzz.FLOOR_Y - 3},z={oz - 12},dx=24,dy=330,dz=24]'
    # back on the plot before the column goes, so nothing falls 160 blocks between runs
    after = [f'tp {p} {ox + 0.5} {fuzz.FLOOR_Y + 1} {oz + 0.5} 0 0',
             f'fill {ox} {y0 - 1} {oz} {ox} {y0 + height - 1} {oz} minecraft:air', f'clear {p}', items]
    return sc, [f'clear {p}', items], after


def dismount(client, vehicle):
    # dismount_sweep 'armor stand, off, no hit' and 'minecart, off, no hit': sprint north, /ride onto a seat the player
    # doesn't steer, and the server takes them off 20 ticks later with forward and sprint still held. 2.0 d8d7b37: 12/12
    # runs flag Simulation, sprint speed stays off after a server dismount. No vehicle is the control, 0 on every build
    ox, oz = fuzz.origin(client)
    p, tag = f'AidanFuzz{client}', f'dv{client}'
    start_z = oz + 0.5
    ev = [(0, 'flick', '180.0 20.0')]
    if vehicle:
        y, vz = fuzz.FLOOR_Y + 1, start_z - 2.5
        what = {'stand': f'armor_stand {ox + 0.5} {y} {vz} {{Marker:1b,Invisible:1b,NoGravity:1b,Tags:["{tag}"]}}',
                'minecart': f'minecart {ox + 0.5} {y} {vz} {{Rotation:[0f,0f],Tags:["{tag}"]}}'}[vehicle]
        ev += [(1, 'rcon', f'summon minecraft:{what}'), (4, 'down', 'forward'), (5, 'down', 'sprint'),
               (15, 'rcon', f'ride {p} mount @e[tag={tag},limit=1]'), (35, 'rcon', f'ride {p} dismount')]
    else:
        ev += [(5, 'down', 'sprint'), (35, 'down', 'forward')]
    ev += [(80, 'up', 'forward'), (80, 'up', 'sprint')]
    # the run leaves the 10 block plot build_world clears: a 5 wide lane north to 32 out
    lane = [('fill', (-2, 1, -32, 2, 6, -10), 'air'), ('fill', (-2, 0, -32, 2, 0, -10), 'grass_block')]
    sc = {'build': lane, 'effects': [], 'yaw': 180.0, 'ticks': 80, 'events': ev}
    before = [f'clear {p}', f'kill @e[type=!minecraft:player,x={ox - 12},y={fuzz.FLOOR_Y - 3},z={oz - 34},dx=24,dy=24,dz=46]',
              f'kill @e[tag={tag}]']
    # a minecart left on the lane would be in the way of the next random scenario on this plot
    return sc, before, [f'kill @e[tag={tag}]']


def scaffold_refused(client, height=8, clicks=5, first=30):
    # scaffold_protect.py v2 with BreakGuard (#2961): an 8 high scaffolding column, the player on top looking straight
    # down, a click every 12 ticks, and every scaffolding break refused the way a protection plugin refuses it, so the
    # block comes back after the client already dropped into it. 2.0 d8d7b37 on the PC: 10/20 runs flag Simulation
    # (one client every run, the other none), PR #3004. BreakGuard only refuses scaffolding breaks and nothing else in
    # the suite breaks scaffolding, so it stays on once a run turns it on
    ox, oz = fuzz.origin(client)
    p = f'AidanFuzz{client}'
    top = fuzz.FLOOR_Y + 1 + height
    ev = [(2, 'rcon', f'tp {p} {ox + 0.5} {top} {oz + 0.5} 0 90'), (8, 'flick', '0 90')]
    for i in range(clicks):
        ev += [(first + 12 * i, 'down', 'attack'), (first + 12 * i + 2, 'up', 'attack')]
    sc = {'build': [('fill', (0, 1, 0, 0, height, 0), 'scaffolding')], 'effects': [], 'yaw': 0.0,
          'ticks': first + 12 * clicks + 20, 'events': ev}
    before = [f'clear {p}', f'kill @e[type=!minecraft:player,x={ox - 12},y={fuzz.FLOOR_Y - 3},z={oz - 12},dx=24,dy=40,dz=24]',
              'breakguard on']
    return sc, before, []


def line(client, tool, block, hold, rounds=4):
    # fastbreak_sweep's line cases: 4 blocks in a line at eye height straight ahead (z +2..+5, yaw 0 pitch 0), so
    # breaking one puts the crosshair on the next. Each round refills the line and holds attack for `hold` ticks
    ox, oz = fuzz.origin(client)
    p = f'AidanFuzz{client}'
    ev = [(1, 'grab', 'on')] + [(2, 'rcon', f'item replace entity {p} hotbar.{h} with {tool}') for h in range(9)]
    t = 10
    for _ in range(rounds):
        ev += [(t, 'rcon', f'fill {ox} {fuzz.FLOOR_Y + 2} {oz + 2} {ox} {fuzz.FLOOR_Y + 2} {oz + 5} minecraft:{block}'),
               (t + 2, 'down', 'attack'), (t + 2 + hold, 'up', 'attack')]
        t += hold + 12
    ev.append((t, 'grab', 'off'))
    sc = {'build': [], 'effects': [], 'yaw': 0.0, 'ticks': t + 6, 'events': ev}
    items = f'kill @e[type=minecraft:item,x={ox - 12},y={fuzz.FLOOR_Y - 3},z={oz - 12},dx=24,dy=30,dz=24]'
    return sc, [f'clear {p}', items], [f'clear {p}', items]


def hit_villager(client, dist):
    # 11-12: upstream 2f785fd (a vector allocation cleanup pushed straight to 2.0) made Reach compare the squared distance.
    # PC rig, 26.3: a villager whose near face is 2.5 blocks from the eyes flagged Reach 6.24650 on 6/6 hits (2.9:
    # 8.40594, 1.9: 3.60734), 0/24 on e457d55 and on the fix (#3017, merged as a10bbf8). Standing still, looking straight
    # ahead, three left clicks through Minecraft.startAttack. NoAI and Invulnerable so it stays put and takes every hit
    ox, oz = fuzz.origin(client)
    tag = f'rv{client}'
    z = oz + 0.5 + dist + 0.3          # a villager is 0.6 wide: its near face is 0.3 in front of its centre
    nbt = '{NoAI:1b,Silent:1b,Invulnerable:1b,Tags:["%s"]}' % tag
    ev = [(2, 'rcon', f'summon minecraft:villager {ox + 0.5} {fuzz.FLOOR_Y + 1} {z:.2f} {nbt}'),
          (14, 'startattack', ''), (24, 'startattack', ''), (34, 'startattack', '')]
    sc = {'build': [], 'effects': [], 'yaw': 0.0, 'ticks': 44, 'events': ev}
    return sc, [f'kill @e[tag={tag}]'], [f'kill @e[tag={tag}]']


def plain(sc):
    return lambda client: (dict(sc), [], [])


CASES = {
    'dig-down-60': {'make': dig_down, 'expect': 'FastBreak on 2.0 until #2987 merges (#1281)'},
    'dismount-stand': {'make': lambda c: dismount(c, 'stand'), 'expect': 'Simulation on 2.0 until #3006 merges'},
    'dismount-minecart': {'make': lambda c: dismount(c, 'minecart'), 'expect': 'Simulation on 2.0 until #3006 merges'},
    'dismount-control': {'make': lambda c: dismount(c, None), 'expect': '0 on every build'},
    # find s3000117, minimized: on slime, forward+right, let go of right and jump the next tick. 2.0 d8d7b37: 5/5 runs
    # flag Simulation (.001718 decaying), fixed on fix/slime-direction-change-2.0
    'slime-release-right': {'make': plain({
        'build': [('fill', (-4, 0, -5, 1, 0, -1), 'slime_block')], 'effects': [], 'yaw': -201.94885, 'ticks': 20,
        'events': [(7, 'down', 'forward'), (7, 'down', 'right'), (16, 'up', 'right'), (17, 'down', 'jump'),
                   (20, 'up', 'forward'), (20, 'up', 'jump')]}),
        'expect': 'Simulation on 2.0 until the slime direction fix merges'},
    # find s2022986, minimized: water and a white carpet next to a pit, strafing and sprinting. 2.0 d8d7b37: 5/5 runs flag
    # Simulation .040000. No fix yet
    'water-carpet': {'make': plain({
        'build': [('fill', (-1, -1, 1, 2, 0, 3), 'air'), ('set', (-3, 1, -1), 'water'), ('set', (2, 1, 2), 'white_carpet')],
        'effects': [], 'yaw': 308.76273, 'ticks': 57,
        'events': [(17, 'down', 'forward'), (17, 'down', 'left'), (17, 'down', 'sprint'), (25, 'up', 'left'),
                   (29, 'up', 'forward'), (29, 'up', 'sprint'), (29, 'down', 'right'), (54, 'up', 'right'),
                   (54, 'down', 'forward'), (54, 'down', 'sneak'), (54, 'down', 'sprint'), (57, 'up', 'forward'),
                   (57, 'up', 'sneak'), (57, 'up', 'sprint')]}),
        'expect': 'Simulation on 2.0, no fix yet'},
    'scaffold-refused': {'make': scaffold_refused, 'expect': 'Simulation on 2.0 some runs until #3004 merges (#2961)'},
    # combat, so a change to Reach shows up here: the random seeds and every case above are movement or digging only,
    # which is why a replay of them matched to the last digit on the commit that broke Reach (11-12)
    'reach-villager-2.5': {'make': lambda c: hit_villager(c, 2.5), 'expect': '0 on every build (Reach on 2f785fd only, #3017)'},
    'reach-villager-2.9': {'make': lambda c: hit_villager(c, 2.9), 'expect': '0 on every build (Reach on 2f785fd only, #3017)'},
    # old clients through ViaBackwards, all on the PC rig first (fastbreak_sweep.py). #2858: end stone bricks and the
    # wall 1.14 added were 0.8 hardness up to 1.14.4 and grim used 3, so every block flagged (1.14.4, iron pick: 41
    # FastBreak in 3/3 runs). Fixed by #3005
    'end-bricks-1.14.4': {'make': lambda c: line(c, 'minecraft:iron_pickaxe', 'end_stone_bricks', 40), 'version': '1.14.4',
                          'expect': 'FastBreak before #3005 (#2858), 0 after'},
    'end-brick-wall-1.14.4': {'make': lambda c: line(c, 'minecraft:iron_pickaxe', 'end_stone_brick_wall', 40),
                              'version': '1.14.4', 'expect': 'FastBreak before #3005 (#2858), 0 after'},
    # #2993: Via shows 1.21.8 and older a plain chest for copper chests, so a pickaxe mines chests at pickaxe speed there
    # and grim expected hand speed (1.21.8: 59 FastBreak, 1.18.1: 54, copper chests 15). Fixed by #3001
    'chest-pickaxe-1.21.8': {'make': lambda c: line(c, 'minecraft:iron_pickaxe', 'chest', 90), 'version': '1.21.8',
                             'expect': 'FastBreak before #3001 (#2993), 0 after'},
    'copper-chest-pickaxe-1.21.8': {'make': lambda c: line(c, 'minecraft:iron_pickaxe', 'copper_chest', 90),
                                    'version': '1.21.8', 'expect': 'FastBreak before #3001 (#2993), 0 after'},
    # 1.18.1's chest case is out with its client (rig/run_old.sh): 1.21.8 guards the same #3001 path
}


def build(name, client):
    return CASES[name]['make'](client)


def version(name):
    return CASES[name].get('version', '26.3') if name in CASES else '26.3'


def pick(spec):
    names = list(CASES)
    if not spec:
        return []
    if spec == 'all':
        return names
    if spec == 'old':
        return [c for c in names if version(c) != '26.3']
    if spec.startswith('shard:'):
        k, n = (int(x) for x in spec[6:].split('/'))
        return [c for i, c in enumerate(c for c in names if version(c) == '26.3') if i % n == k]
    unknown = [c for c in spec.split(',') if c not in CASES]
    if unknown:
        raise SystemExit(f'unknown cases {unknown}, known: {names}')
    return spec.split(',')
