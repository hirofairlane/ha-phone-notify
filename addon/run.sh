#!/usr/bin/env bash
# WORK IN PROGRESS — placeholder entrypoint. Needs: start PipeWire/WirePlumber,
# apply the WirePlumber hfp_hf-exclusion config (see ../docs/ARCHITECTURE.md),
# start HandsFree-Linux, recreate the virtual null-sinks (they don't survive
# a PipeWire restart, see ARCHITECTURE.md), then bridge MQTT/HA events to
# scripts/phone_call_listener.py invocations.
set -e
echo "phone-notify addon: not implemented yet, see docs/ARCHITECTURE.md"
sleep infinity
