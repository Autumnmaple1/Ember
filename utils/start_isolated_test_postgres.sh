#!/usr/bin/env bash
# Docker-free WSL fallback. Extract packages into /var/tmp; never install system packages.
set -euo pipefail
port="${1:-55433}"
case "$port" in 55433) ;; *) echo 'Only the isolated test port 55433 is allowed' >&2; exit 1 ;; esac
runtime="${EMBER_TEST_PG_RUNTIME:-$(mktemp -d /var/tmp/ember-pg.XXXXXXXX)}"
runtime="$(realpath -e "$runtime")"
if [[ ! "$runtime" =~ ^/var/tmp/ember-pg\.[[:alnum:]]{8}$ ]]; then echo 'Unsafe test runtime path' >&2; exit 1; fi
if [ -f "$runtime/cluster/PG_VERSION" ]; then
  test "$(cat "$runtime/cluster/PG_VERSION")" = 14
  export LD_LIBRARY_PATH="$runtime/root/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
  bin="$runtime/root/usr/lib/postgresql/14/bin"
  if ! "$bin/pg_ctl" -D "$runtime/cluster" status > /dev/null 2>&1; then
    "$bin/pg_ctl" -D "$runtime/cluster" -l "$runtime/server.log" -o "-p $port -h 127.0.0.1 -k $runtime" -w start
  fi
  echo "EMBER_TEST_PG_RUNTIME=$runtime"
  echo "EMBER_TEST_PG_PORT=$port"
  exit 0
fi
echo "Isolated runtime: $runtime"
cd "$runtime"
apt-get download postgresql-14 postgresql-server-dev-14 postgresql-client-14 postgresql-common libllvm14
mkdir -p root
for package in ./*.deb; do dpkg-deb -x "$package" root; done
export LD_LIBRARY_PATH="$runtime/root/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
bin="$runtime/root/usr/lib/postgresql/14/bin"
pgxs="$(find "$runtime/root" -name pgxs.mk -print -quit)"
if [ -z "$pgxs" ]; then echo 'PostgreSQL build files missing' >&2; find "$runtime/root/usr/lib" -maxdepth 5 -type d; exit 1; fi
cat > pg_config <<EOF
#!/usr/bin/env bash
export LD_LIBRARY_PATH="$LD_LIBRARY_PATH"
case "\$1" in
--includedir-server) echo "$runtime/root/usr/include/postgresql/14/server" ;;
--includedir) echo "$runtime/root/usr/include/postgresql" ;;
--pkglibdir) echo "$runtime/root/usr/lib/postgresql/14/lib" ;;
--sharedir) echo "$runtime/root/usr/share/postgresql/14" ;;
--pgxs) echo "$pgxs" ;;
*) "$bin/pg_config" "\$@" ;;
esac
EOF
chmod +x pg_config
curl --fail --location --silent --show-error https://github.com/pgvector/pgvector/archive/refs/tags/v0.8.1.tar.gz --output pgvector.tar.gz
tar xzf pgvector.tar.gz
make_args=(PG_CONFIG="$runtime/pg_config" with_llvm=no
  PG_CPPFLAGS="-I$runtime/root/usr/include/postgresql/14/server"
  includedir_server="$runtime/root/usr/include/postgresql/14/server"
  pkglibdir="$runtime/root/usr/lib/postgresql/14/lib" datadir="$runtime/root/usr/share/postgresql/14")
make -C pgvector-0.8.1 "${make_args[@]}" -j2
make -C pgvector-0.8.1 "${make_args[@]}" install
"$bin/initdb" -D "$runtime/cluster" -U ember_test_admin -A trust --no-locale --encoding=UTF8 > init.log
"$bin/pg_ctl" -D "$runtime/cluster" -l "$runtime/server.log" -o "-p $port -h 127.0.0.1 -k $runtime" -w start
echo "EMBER_TEST_PG_RUNTIME=$runtime"
echo "EMBER_TEST_PG_PORT=$port"
