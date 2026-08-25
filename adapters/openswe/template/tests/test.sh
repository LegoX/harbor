#!/bin/bash
# Harbor verifier for OpenSWE tasks.
#
# Flow:
#   1. Apply the test patch (test-only changes) on top of the agent's edits.
#   2. Run the transformed evaluation body, which activates the conda
#      environment, runs the relevant tests, and emits an OPENSWE_EXIT_CODE
#      marker (the OpenSWE rule-based grading signal).
#   3. Parse the marker and convert it into a Harbor reward.
set -uo pipefail

cd /testbed

# (1) Apply the test patch, if one was provided for this instance.
#
# The test patch carries the test-only changes that the OpenSWE grading
# depends on. If it fails to apply, grading would run against the wrong tests
# and could produce false positives/negatives, so treat that as a hard
# verification failure (reward=0) rather than silently continuing.
if [ -s /tests/test_patch.diff ]; then
  echo "Applying test patch..."
  if ! git apply -v --allow-empty /tests/test_patch.diff; then
    echo "FAILED: could not apply test patch /tests/test_patch.diff" >&2
    mkdir -p /logs/verifier
    echo 0 > /logs/verifier/reward.txt
    exit 0
  fi
fi

# (2) Run the transformed evaluation body and capture its output.
LOG_FILE=$(mktemp)
export LOG_FILE
bash /tests/eval_body.sh 2>&1 | tee "$LOG_FILE"

# (3) Extract the OPENSWE_EXIT_CODE marker (last occurrence wins).
rc=$(grep -oE 'OPENSWE_EXIT_CODE=[0-9]+' "$LOG_FILE" | tail -1 | cut -d= -f2)

echo "OpenSWE results start here"
mkdir -p /logs/verifier
if [ "${rc:-1}" = "0" ]; then
  echo "PASSED"
  echo 1 > /logs/verifier/reward.txt
else
  echo "FAILED (OPENSWE_EXIT_CODE=${rc:-<missing>})"
  echo 0 > /logs/verifier/reward.txt
fi
echo "OpenSWE results end here"

exit 0
