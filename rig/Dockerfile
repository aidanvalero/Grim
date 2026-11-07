# Mirrors a GitHub ubuntu-latest runner for the grim-repro rig: no GPU, Xvfb + Mesa software OpenGL. Also builds Grim
# with the PC's exact JDKs: Gradle runs on Temurin 25.0.4.1+1, Grim's Java 21 toolchain is Temurin 21.0.12.1+1
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git python3 python3-venv procps xvfb xauth mesa-utils \
      libgl1 libgl1-mesa-dri libglx-mesa0 libegl1 libgles2 \
      libx11-6 libxext6 libxi6 libxrandr2 libxrender1 libxtst6 libxinerama1 libxcursor1 libxxf86vm1 libxss1 libxfixes3 \
      libxkbcommon0 libxkbcommon-x11-0 libwayland-client0 libwayland-cursor0 libwayland-egl1 libdecor-0-0 \
      libasound2t64 libfreetype6 libfontconfig1 \
 && rm -rf /var/lib/apt/lists/*
# Same JDK builds as the PC; latest 25 / 21 only if an exact build is gone
RUN set -e; \
    curl -fsSL -o /tmp/jdk.tgz "https://api.adoptium.net/v3/binary/version/jdk-25.0.4.1%2B1/linux/x64/jdk/hotspot/normal/eclipse?project=jdk" \
      || curl -fsSL -o /tmp/jdk.tgz "https://api.adoptium.net/v3/binary/latest/25/ga/linux/x64/jdk/hotspot/normal/eclipse"; \
    mkdir -p /opt/jdk && tar -xzf /tmp/jdk.tgz -C /opt/jdk --strip-components=1 && rm /tmp/jdk.tgz && /opt/jdk/bin/java -version
RUN set -e; \
    curl -fsSL -o /tmp/jdk21.tgz "https://api.adoptium.net/v3/binary/version/jdk-21.0.12.1%2B1/linux/x64/jdk/hotspot/normal/eclipse?project=jdk" \
      || curl -fsSL -o /tmp/jdk21.tgz "https://api.adoptium.net/v3/binary/latest/21/ga/linux/x64/jdk/hotspot/normal/eclipse"; \
    mkdir -p /opt/jdk21 && tar -xzf /tmp/jdk21.tgz -C /opt/jdk21 --strip-components=1 && rm /tmp/jdk21.tgz && /opt/jdk21/bin/java -version
RUN python3 -m venv /opt/pmc && /opt/pmc/bin/pip install --no-cache-dir portablemc==5.0.6 && /opt/pmc/bin/portablemc --version
# Old clients (run_old.sh) run on the Java they shipped with, the same builds as the PC rig's fuzz/launch.ps1: 1.14.4 on
# 8u504-b01, 1.18.1 on 17.0.20.1+1 (it crashes in its texture atlas on 25)
RUN set -e; \
    curl -fsSL -o /tmp/jre8.tgz "https://api.adoptium.net/v3/binary/version/jdk8u504-b01/linux/x64/jre/hotspot/normal/eclipse?project=jdk" \
      || curl -fsSL -o /tmp/jre8.tgz "https://api.adoptium.net/v3/binary/latest/8/ga/linux/x64/jre/hotspot/normal/eclipse"; \
    mkdir -p /opt/jre8 && tar -xzf /tmp/jre8.tgz -C /opt/jre8 --strip-components=1 && rm /tmp/jre8.tgz && /opt/jre8/bin/java -version
RUN set -e; \
    curl -fsSL -o /tmp/jre17.tgz "https://api.adoptium.net/v3/binary/version/jdk-17.0.20.1%2B1/linux/x64/jre/hotspot/normal/eclipse?project=jdk" \
      || curl -fsSL -o /tmp/jre17.tgz "https://api.adoptium.net/v3/binary/latest/17/ga/linux/x64/jre/hotspot/normal/eclipse"; \
    mkdir -p /opt/jre17 && tar -xzf /tmp/jre17.tgz -C /opt/jre17 --strip-components=1 && rm /tmp/jre17.tgz && /opt/jre17/bin/java -version
COPY probe /probe
RUN /opt/jdk/bin/javac -d /probe /probe/TickProbe.java
COPY run.sh run_old.sh cpu.py build_grim.sh /
