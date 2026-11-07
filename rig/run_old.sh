#!/bin/bash
# Old clients through ViaBackwards (11-07): a fresh 26.3 server with the given Grim jar plus ViaVersion and ViaBackwards
# 5.12.2-SNAPSHOT (the PC rig's), one vanilla client per old version under Xvfb + Mesa, each on the Java the PC runs it
# on (fuzz/launch.ps1: 1.14.4 on 8, 1.18.1 on 17 since it crashes on 25, 1.21.8 on 25), then suite.py on the cases
# fuzz/cases.py marks for those versions. Plots go up at y 99, 1.14.4 only has y 0-255.
#   run_old.sh <grim jar> <label> "<suite args>"
set -u
JAR=$1; LABEL=$2; SUITE=$3
VERSIONS=(1.14.4 1.21.8 1.18.1)
declare -A JVM=([1.14.4]=/opt/jre8/bin/java [1.18.1]=/opt/jre17/bin/java [1.21.8]=/opt/jdk/bin/java)
T0=$(date +%s)
stamp() { echo "$(( $(date +%s) - T0 ))s $*" | tee -a /out/timing.txt; }
rm -rf /rig && cp -r /stage/rig /rig
# drive.py hard-codes the rig's Windows java path
mkdir -p /rig/jdk25/jdk-25.0.4.1+1/bin && ln -sf /opt/jdk/bin/java /rig/jdk25/jdk-25.0.4.1+1/bin/java.exe
cp /rig/grim-jars/$JAR /rig/via/*.jar /rig/repro263/plugins/
sed -i 's/^use-native-transport=.*/use-native-transport=false/' /rig/repro263/server.properties
sed -i 's/^useNativeTransport:.*/useNativeTransport:false/' /rig/options.txt
export DISPLAY=:99 LIBGL_ALWAYS_SOFTWARE=1 ALSOFT_DRIVERS=null
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp > /out/xvfb.log 2>&1 &
stamp "start: $(nproc) cpus"
cd /rig/repro263 && /opt/jdk/bin/java -Xms1G -Xmx3G -jar paper.jar --nogui > console.log 2>&1 &
for v in "${VERSIONS[@]}"; do
  /opt/pmc/bin/portablemc --main-dir /cache/mc start $v --dry >> /out/pmc-install.log 2>&1
  stamp "client $v installed (exit $?), java: $(${JVM[$v]} -version 2>&1 | sed -n 2p)"
done
for i in $(seq 1 180); do grep -q 'Done (' /rig/repro263/console.log && break; sleep 1; done
stamp "server: $(grep -o 'Done ([0-9.]*s)' /rig/repro263/console.log), plugins: $(ls /rig/repro263/plugins/*.jar | xargs -n1 basename | tr '\n' ' ')"
PAIRS=""
for i in 1 2 3; do
  v=${VERSIONS[$((i - 1))]}
  mkdir -p /rig/mc-fuzz/$i/logs && cp /rig/options.txt /rig/mc-fuzz/$i/options.txt
  PORT=25600
  # before 1.20 the client joins while it's still loading, and on software GL the join wins and the first block level
  # event kills it; fuzz/hold_join.py keeps it in the login phase until its log says it's done loading
  if [ "$v" != 1.21.8 ]; then
    PORT=$((25610 + i))
    python3 -I /rig/fuzz/hold_join.py $PORT 25600 /rig/mc-fuzz/$i/logs/latest.log 240 > /out/hold$i.log 2>&1 &
    sleep 1
  fi
  JAVA_TOOL_OPTIONS="-agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=127.0.0.1:$((5005 + i))" \
    /opt/pmc/bin/portablemc --main-dir /cache/mc start --mc-dir /rig/mc-fuzz/$i $v -u AidanFuzz$i --join-server localhost \
    --join-server-port $PORT --jvm ${JVM[$v]} --resolution 320x240 > /out/client$i.log 2>&1 &
  export "NAMES_$i=names-$v.properties"
  PAIRS="$PAIRS${PAIRS:+,}$i=$v"
  sleep 2
done
for i in $(seq 1 420); do [ "$(grep -c 'joined the game' /rig/repro263/console.log)" -ge 3 ] && break; sleep 1; done
stamp "clients joined: $(grep -c 'joined the game' /rig/repro263/console.log) ($PAIRS); held: $(cat /out/hold*.log 2>/dev/null | grep -o 'released after.*' | tr '\n' ' ')"
export FUZZ_FLOOR_Y=99
cd /rig && python3 -I fuzz/fuzz.py setup 3 > /out/setup.log 2>&1
stamp "suite: $SUITE"
python3 -I fuzz/suite.py $LABEL $SUITE --clients 1,2,3 --client-versions $PAIRS 2>&1 | tail -3 | tee -a /out/timing.txt
stamp "done"
cp /rig/fuzz/out/suite-*.json /rig/fuzz/out/voided.jsonl /out/ 2>/dev/null
cp /rig/repro263/console.log /out/console.log
for i in 1 2 3; do
  cp /rig/mc-fuzz/$i/logs/latest.log /out/client$i-latest.log 2>/dev/null
  cp /rig/mc-fuzz/$i/crash-reports/*.txt /out/ 2>/dev/null
done
