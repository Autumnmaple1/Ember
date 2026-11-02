#!/usr/bin/env bash
# Stop only a cluster created by start_isolated_test_postgres.sh. Keep its files.
set -euo pipefail
runtime="$(realpath -e "${1:?Supply the printed EMBER_TEST_PG_RUNTIME path}")"
if [[ ! "$runtime" =~ ^/var/tmp/ember-pg\.[[:alnum:]]{8}$ ]]; then
  echo 'Refusing a path outside the generated test runtime' >&2
  exit 1
fi
test "$(cat "$runtime/cluster/PG_VERSION")" = 14
export LD_LIBRARY_PATH="$runtime/root/usr/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
"$runtime/root/usr/lib/postgresql/14/bin/pg_ctl" -D "$runtime/cluster" -m fast -w stop
