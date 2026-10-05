#!/bin/sh
set -eu

if [ ! -f "main.py" ] || [ ! -d "morning_digest" ] || [ ! -f "_scripts/send-test-email.sh" ]; then
    echo "Error: run this script from the newsletter repository root." >&2
    exit 1
fi

exec "${PYTHON:-python3}" -m morning_digest.test_email "$@"
