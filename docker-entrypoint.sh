#!/bin/sh

set -e

. /venv/bin/activate

/app/kasa_plug_exporter/app.py "$@"
