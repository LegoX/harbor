#!/bin/bash
set -euo pipefail

# Oracle solution: apply the gold patch to /testbed.

cat > /testbed/solution_patch.diff << '__OPENSWE_SOLUTION__'
{patch}
__OPENSWE_SOLUTION__

cd /testbed
git apply -v --allow-empty /testbed/solution_patch.diff || patch --fuzz=5 -p1 -i /testbed/solution_patch.diff
