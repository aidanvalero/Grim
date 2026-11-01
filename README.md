# rig

Grim's real-client regression net, on GitHub's free runners. Vanilla 26.3 clients play fixed random scenarios against
a Paper server running Grim; their keys are pressed tick by tick over the Java debugger (`rig/stage/cobweb/KeyDriver.java`),
so a scenario replays the same way every time. A vanilla client can't cheat, so every flag is a false flag.

- `rig/Dockerfile`: Ubuntu 24.04, Temurin 25.0.4.1+1, portablemc 5.0.6, Xvfb + Mesa software OpenGL. No GPU.
- `rig/run.sh`: one job, a fresh server with the given Grim jar, 2 clients, `fuzz/suite.py`, then a tick-pacing probe.
- `rig/ci_gate.py`: fails a job if any kept result came from a client hitch (a run that tripped Grim's Timer check).
- `.github/workflows/rig-proof.yml`: the A/B the PC rig ran on 2026-11-01 (stock 2.0 eb4c657 vs PR #2789), rerun here
  and lined up seed by seed against the PC's results in `rig/baseline`.

Two things a GPU-less 26.3 client needs: `SDL_VIDEO_FORCE_EGL=1` (26.3 asks for an sRGB-capable framebuffer and
Xvfb's GLX has no sRGB visual), and Netty on NIO for server and clients, like the Windows rig.
