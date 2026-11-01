#!/bin/bash
# One CI-shaped job: a fresh server with the given Grim jar, 2 vanilla 26.3 clients under Xvfb + Mesa, then suite.py.
#   run.sh <grim jar> <label> "<suite args>" ["<second suite args>"] ["<extra command run from /rig>"]
set -u
JAR=$1; LABEL=$2; SUITE1=$3; SUITE2=${4:-}; EXTRA=${5:-}
T0=$(date +%s)
stamp() { echo "$(( $(date +%s) - T0 ))s $*" | tee -a /out/timing.txt; }
rm -rf /rig && cp -r /stage/rig /rig
# drive.py hard-codes the rig's Windows java path
mkdir -p /rig/jdk25/jdk-25.0.4.1+1/bin && ln -sf /opt/jdk/bin/java /rig/jdk25/jdk-25.0.4.1+1/bin/java.exe
cp /rig/grim-jars/$JAR /rig/repro263/plugins/
# Windows has no epoll, so the rig's server and clients run Netty on NIO; keep this run on NIO too
sed -i 's/^use-native-transport=.*/use-native-transport=false/' /rig/repro263/server.properties
sed -i 's/^useNativeTransport:.*/useNativeTransport:false/' /rig/options.txt
# 26.3 asks SDL for an sRGB-capable GL framebuffer (GlBackend: SDL_GL_FRAMEBUFFER_SRGB_CAPABLE=1) and Xvfb's GLX has
# no sRGB visual, so the OpenGL backend fails over GLX; through EGL it comes up on Mesa llvmpipe, OpenGL 4.5 core
export DISPLAY=:99 LIBGL_ALWAYS_SOFTWARE=1 ALSOFT_DRIVERS=null SDL_VIDEO_FORCE_EGL=1
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp > /out/xvfb.log 2>&1 &
stamp "start: $(nproc) cpus, $(/opt/jdk/bin/java -version 2>&1 | sed -n 2p)"
cd /rig/repro263 && /opt/jdk/bin/java -Xms1G -Xmx3G -jar paper.jar --nogui > console.log 2>&1 &
/opt/pmc/bin/portablemc --main-dir /cache/mc start 26.3 --dry > /out/pmc-install.log 2>&1
stamp "client 26.3 installed (exit $?)"
for i in $(seq 1 180); do grep -q 'Done (' /rig/repro263/console.log && break; sleep 1; done
stamp "server: $(grep -o 'Done ([0-9.]*s)' /rig/repro263/console.log)"
for i in 1 2; do
  mkdir -p /rig/mc-fuzz/$i && cp /rig/options.txt /rig/mc-fuzz/$i/options.txt
  JAVA_TOOL_OPTIONS="-agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=127.0.0.1:$((5005 + i))" \
    /opt/pmc/bin/portablemc --main-dir /cache/mc start --mc-dir /rig/mc-fuzz/$i 26.3 -u AidanFuzz$i --join-server localhost \
    --join-server-port 25600 --jvm /opt/jdk/bin/java --resolution 320x240 > /out/client$i.log 2>&1 &
  sleep 2
done
for i in $(seq 1 300); do [ "$(grep -c 'joined the game' /rig/repro263/console.log)" -ge 2 ] && break; sleep 1; done
stamp "clients joined: $(grep -c 'joined the game' /rig/repro263/console.log)"
grep -h -E 'Using graphics (backend|device)' /rig/mc-fuzz/1/logs/latest.log | tee -a /out/timing.txt
cd /rig && python3 -I fuzz/fuzz.py setup 2 > /out/setup.log 2>&1
( sleep 30; python3 /cpu.py > /out/cpu.txt 2>&1 ) &
stamp "suite 1: $SUITE1"
python3 -I fuzz/suite.py $LABEL $SUITE1 2>&1 | tail -3 | tee -a /out/timing.txt
if [ -n "$SUITE2" ]; then
  stamp "suite 2: $SUITE2"
  python3 -I fuzz/suite.py $LABEL-b $SUITE2 2>&1 | tail -3 | tee -a /out/timing.txt
fi
if [ -n "$EXTRA" ]; then
  stamp "extra: $EXTRA"
  bash -c "$EXTRA" 2>&1 | tail -5 | tee -a /out/timing.txt
fi
# tick pacing: probe each client for 40 s while the other one runs scenarios
for pair in "1 2" "2 1"; do
  set -- $pair
  python3 -I fuzz/suite.py $LABEL-pace$2 --seeds 7000001,7000002,7000003,7000004 --runs 1 --clients $2 > /out/pace-bg$2.log 2>&1 &
  BG=$!
  sleep 3
  /opt/jdk/bin/java -cp /probe TickProbe $((5005 + $1)) 40 2>&1 | tee -a /out/timing.txt
  wait $BG
done
stamp "done"
cp /rig/fuzz/out/suite-*.json /rig/fuzz/out/voided.jsonl /out/ 2>/dev/null
cp /rig/repro263/console.log /out/console.log
cat /out/cpu.txt >> /out/timing.txt
