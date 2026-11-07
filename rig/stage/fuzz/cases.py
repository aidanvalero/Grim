# Known bugs as fixed suite cases (11-07). Each one is a bug the rig reproduced for a fix, kept as one scenario a client
# runs next to the random seeds: suite.py --cases runs them and files the results under case:<name>, so suite_diff.py
# lines them up between two builds the same way as seeds. A case flags on 2.0 until its fix merges and has to stay at 0
# after, and a PR that fixes one shows it as gone. Everything here reproduces on 26.3 clients. Not in yet: the old client
# ones (#2858 end bricks on 1.14.4 and older, #2993 chests on 1.21.8 and older through Via) and #2961, which needs spawn
# protection on the plot and that would refuse the dig-down breaks too.
#   build(name, client) -> (scenario, rcon commands to run before it, rcon commands to run after it)
#   pick('all' | 'a,b' | 'shard:K/N') -> case names, shard:K/N being every Nth case starting at K
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
}


def build(name, client):
    return CASES[name]['make'](client)


def pick(spec):
    names = list(CASES)
    if not spec:
        return []
    if spec == 'all':
        return names
    if spec.startswith('shard:'):
        k, n = (int(x) for x in spec[6:].split('/'))
        return [c for i, c in enumerate(names) if i % n == k]
    unknown = [c for c in spec.split(',') if c not in CASES]
    if unknown:
        raise SystemExit(f'unknown cases {unknown}, known: {names}')
    return spec.split(',')
