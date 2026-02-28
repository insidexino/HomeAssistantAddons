#!/bin/bash
set +u

CONFIG_PATH=/data/options.json

echo "START MasterHills Wallpad Controller"

python3 /master.py $CONFIG_PATH