#!/bin/bash
# Builds Grim the way cobweb/pr-check.ps1 does on the PC: :bukkit:shadowJar at <base>, then the PR's own changes (diff
# from its merge-base with base to its head) applied on base with git apply --3way and built again. Gradle on Temurin
# 25.0.4.1+1, Grim's Java 21 toolchain on Temurin 21.0.12.1+1, the PC's two JDKs. 11-01: stock eb4c657 and PR #2789 built
# here matched the PC's jars on all 6024 classes (rig/jar_diff.py).
#   build_grim.sh <grim git checkout> <out dir> <base> [<pr> [<head sha>]]   -> <out>/stock.jar, <out>/pr.jar
# <pr> is a GrimAnticheat/Grim PR number, or owner/repo:branch for a branch that isn't a PR (11-01: the #2312 update on
# aidanvalero/Grim, which Axionize pulls into his PR himself). Built the same way, its diff from its merge-base with base
set -eu
SRC=$1; OUT=$2; BASE=$3; PR=${4:-}; SHA=${5:-}
# Checked before the stock build so a typo fails in seconds, not after a build
if [[ $PR =~ ^[0-9]*$ ]]; then
  NAME="PR #$PR"; TAG=pr$PR; URL=
elif [[ $PR =~ ^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+):([A-Za-z0-9_./-]+)$ ]]; then
  NAME=$PR; TAG=$(printf %s "$PR" | tr -c 'A-Za-z0-9._-' '-'); URL=https://github.com/${BASH_REMATCH[1]}.git; REF=refs/heads/${BASH_REMATCH[2]}
else
  echo "pr has to be a PR number or owner/repo:branch, not $PR"; exit 1
fi
export JAVA_HOME=/opt/jdk GRADLE_USER_HOME=${GRADLE_USER_HOME:-/root/.gradle}
GIT=(git -c safe.directory='*' -c user.name=rig -c user.email=rig@localhost)
GRADLE=(./gradlew :bukkit:shadowJar --console=plain --no-daemon
        -Porg.gradle.java.installations.paths=/opt/jdk21 -Porg.gradle.java.installations.auto-download=false)
mkdir -p "$OUT"
cd "$SRC"
jar() { ls -t bukkit/build/libs/grimac-bukkit-*.jar | grep -v -E 'sources|javadoc' | head -1; }
"${GIT[@]}" checkout -q --detach "$BASE"
"${GRADLE[@]}" > "$OUT/build-stock.log" 2>&1 || { tail -30 "$OUT/build-stock.log"; exit 1; }
cp "$(jar)" "$OUT/stock.jar"
echo "stock: $("${GIT[@]}" rev-parse --short HEAD) -> $(basename "$(jar)")"
if [ -n "$PR" ]; then
  if [ -z "$URL" ]; then
    "${GIT[@]}" fetch -q origin "pull/$PR/head:refs/remotes/origin/pr/$PR"
    TIP=origin/pr/$PR
  else
    "${GIT[@]}" fetch -q "$URL" "$REF"
    TIP=FETCH_HEAD
  fi
  HEAD_SHA=${SHA:-$("${GIT[@]}" rev-parse "$TIP")}
  MB=$("${GIT[@]}" merge-base HEAD "$HEAD_SHA")
  "${GIT[@]}" diff "$MB" "$HEAD_SHA" > "$OUT/$TAG.diff"
  "${GIT[@]}" apply --3way "$OUT/$TAG.diff" || { echo "$NAME doesn't apply on $BASE (needs a rebase)"; exit 1; }
  rm -rf bukkit/build/libs
  "${GRADLE[@]}" > "$OUT/build-pr.log" 2>&1 || { tail -30 "$OUT/build-pr.log"; exit 1; }
  cp "$(jar)" "$OUT/pr.jar"
  echo "pr: $NAME head $("${GIT[@]}" rev-parse --short "$HEAD_SHA") on $("${GIT[@]}" rev-parse --short HEAD) -> $(basename "$(jar)")"
fi
