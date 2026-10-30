#!/bin/bash
# Reference copy of a working 2026-10-30 build (stock presto.spark + one Java patch). Edit B, REV, STOCK, V and the patch path before reuse.
# Ephemeral, untagged presto.spark (Sapphire Velox, Spark 3) fbpkg = stock package + 1b.patch.
# Derived from /data/users/mkarrmann/builds/sapphire-ab/build.sh (+ its pkg/ assembly steps).
# Usage: build.sh [src|mvn|assemble|publish|verify|all]   (default: all)
set -euo pipefail
B=/data/users/mkarrmann/builds/sapphire-1b
REV=0cc259228cb3d26b076e1b769f4bfbb90449586b
FBS=/home/mkarrmann/checkout1/fbsource          # read-only: sl archive only, working copy untouched
V=0.301-edge140-SNAPSHOT
STOCK=presto.spark:3c9705239b9d46c4a780b79c8077bdac
S=$B/settings.xml                               # copy of fbcode/javafoundations/mvn/src/main/resources/settings.xml
M2=$B/m2                                        # private local repo (seeded by reflink-copy of ~/.m2 minus com/facebook/presto)
export JAVA_HOME=/usr/local/java-runtime/impl/17 PATH=/usr/local/java-runtime/impl/17/bin:$PATH
export MAVEN_OPTS="-Dmaven.gitcommitid.skip=true -Djava.net.preferIPv6Addresses=true -Xms16g -Xmx16g -XX:+UseG1GC"
export YARN_YARN_OFFLINE_MIRROR=$B/yarn-offline-mirror; mkdir -p $YARN_YARN_OFFLINE_MIRROR
step() { echo "=== $(date +%T) $*"; }

do_src() {
  step "export presto trees at $REV"
  rm -rf $B/src && mkdir -p $B/src
  (cd $FBS && sl archive -r $REV -I fbcode/github/presto-trunk -I fbcode/github/presto-facebook-trunk $B/src/archive)
  (cd $B/src/archive && patch -p1 < $B/1b.patch)
  mv $B/src/archive/fbcode/github/presto-trunk $B/src/archive/fbcode/github/presto-facebook-trunk $B/src/ && rm -r $B/src/archive
}

do_mvn() {
  for R in presto-trunk presto-facebook-trunk; do
    step "versions:set $R"
    (cd $B/src/$R && mvn -q -s $S -Dmaven.repo.local=$M2 versions:set -DnewVersion=$V -DprocessAllModules=true -DgenerateBackupPoms=false)
  done
  sed -i "s#<dep.presto.version>[^<]*</dep.presto.version>#<dep.presto.version>$V</dep.presto.version>#" $B/src/presto-facebook-trunk/pom.xml
  grep -n "<dep.presto.version>" $B/src/presto-facebook-trunk/pom.xml
  F="-DskipTests -DskipUI -Dair.check.skip-all -Dspark-version=3 -Dlicense.report.skip=true -Dout-of-tree-build=true -T 16 -s $S -Dmaven.repo.local=$M2"
  step "presto-trunk clean install"
  (cd $B/src/presto-trunk && mvn clean install $F -pl '!presto-docs' -Dout-of-tree-build-root=$B/oot/presto-trunk)
  step "presto-facebook-trunk spark package"
  (cd $B/src/presto-facebook-trunk && mvn clean install $F -nsu -pl presto-facebook-spark-package -am -Dout-of-tree-build-root=$B/oot/presto-facebook-trunk)
  step "mvn done"
}

PKGDIR=$B/oot/presto-facebook-trunk/com.facebook.presto:presto-facebook-spark-package/presto-facebook-spark-package-$V/presto-facebook-spark-package-$V
do_assemble() {
  step "fetch stock $STOCK"
  mkdir -p $B/pkg/stock; [ -f $B/pkg/stock/sapphire_cpp ] || fbpkg fetch $STOCK -d $B/pkg/stock
  # Same layout as sapphire-ab/pkg/build: Java parts (launcher.jar, lib, plugin) from our build,
  # native/ops parts (sapphire_cpp, bin, cron) from the stock package.
  step "assemble $B/pkg/build/sapphire"
  rm -rf $B/pkg/build && mkdir -p $B/pkg/build/sapphire $B/pkg/build/materialized_configs
  cp -a $PKGDIR/launcher.jar $PKGDIR/lib $PKGDIR/plugin $B/pkg/build/sapphire/
  cp -a $B/pkg/stock/sapphire_cpp $B/pkg/stock/bin $B/pkg/stock/cron $B/pkg/build/sapphire/
  python3 - "$B/pkg/build" > $B/pkg/build/materialized_configs/presto.spark.fbpkg.materialized_JSON <<'EOF'
import json, sys
d = sys.argv[1] + "/sapphire"
names = ["bin", "lib", "cron", "plugin", "sapphire_cpp", "launcher.jar"]
print(json.dumps({"paths": [f"{d}/{n}" for n in names], "path_mapping": {f"{d}/{n}": n for n in names}}, indent=2))
EOF
}

do_publish() {
  step "fbpkg build (ephemeral, 7d, no tags)"
  (cd $B/pkg/build && fbpkg build presto.spark --configerator-path $B/pkg/build -E --expire 7d --yes --json \
      -m "sapphire-1b: stock $STOCK + 1b.patch (StreamPropertyDerivations.visitMergeJoin singleStream under native)" \
      > $B/pkg/build/out.json 2> $B/pkg/build/fbpkg_build.log)
  cat $B/pkg/build/out.json
}

do_verify() {
  UUID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[0]["uuid"])' $B/pkg/build/out.json)
  step "fetch back presto.spark:$UUID"
  rm -rf $B/pkg/verify && mkdir -p $B/pkg/verify && fbpkg fetch presto.spark:$UUID -d $B/pkg/verify
}

case "${1:-all}" in
  src) do_src ;; mvn) do_mvn ;; assemble) do_assemble ;; publish) do_publish ;; verify) do_verify ;;
  all) do_src; do_mvn; do_assemble; do_publish; do_verify ;;
esac
