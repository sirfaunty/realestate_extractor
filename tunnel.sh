#!/usr/bin/env bash
# Tunnel the staging instance to http://localhost:5050 (for pre-TLS or
# operator work you'd rather not do over the public URL). Leave running.
VM_HOST="${CAPACTIVE_VM:-root@91.98.30.65}"
echo "Staging → http://localhost:5050   (Ctrl+C to close tunnel)"
ssh -N -L 5050:127.0.0.1:5000 "$VM_HOST"
