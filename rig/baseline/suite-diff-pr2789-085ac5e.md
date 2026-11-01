# Real-client regression net: PR #2789 Enforce jump delay (085ac5e on 2.0 eb4c657)

Same 120 random scenarios (seeds 7000000-7000119, fuzz.scenario) on both builds, 2 runs each, 4 vanilla 26.3 clients.

| | stock | PR |
|---|---|---|
| build | 2.3.75-test_2944-on-2.0-eb4c657 | 2.3.75-HEAD-eb4c657 |
| runs | 264 | 264 |
| runs with any flag | 0 | 21 |
| minutes | 10.7 | 10.8 |
| reruns of differing seeds | 24 | 24 |
| driver errors / seeds not run | 0 / 0 | 0 / 0 |

- gone (flags less on the PR, Fisher p < 0.05): **0**
- new (flags more on the PR, Fisher p < 0.05): **4**
- noisy (differs, not significant): 1
- same flag rate on both: 0

## New on the PR

| seed | check | stock | PR | p | offset seen | repro |
|---|---|---|---|---|---|---|
| 7000092 | GroundSpoof | 0/10 | 10/10 | 0.000 | claimed | `python -I fuzz/fuzz.py repro --seed 7000092` |
| 7000092 | Simulation | 0/10 | 10/10 | 0.000 | .310000 | `python -I fuzz/fuzz.py repro --seed 7000092` |
| 7000100 | GroundSpoof | 0/10 | 10/10 | 0.000 | claimed | `python -I fuzz/fuzz.py repro --seed 7000100` |
| 7000100 | Simulation | 0/10 | 10/10 | 0.000 | .021000 | `python -I fuzz/fuzz.py repro --seed 7000100` |

## Noisy

| seed | check | stock | PR | p | offset seen | repro |
|---|---|---|---|---|---|---|
| 7000026 | Simulation | 0/10 | 1/10 | 1.000 | .001203 | `python -I fuzz/fuzz.py repro --seed 7000026` |

## Server log and unit tests

- unit tests (:common:test) on the PR build: 48 run, 0 failed, gradle exit 0
- stock-eb4c657: 0 exception/error lines in the server log
- pr2789-085ac5e: 0 exception/error lines in the server log
