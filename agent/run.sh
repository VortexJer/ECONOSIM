#!/bin/sh
# Servicio del agente: reinicia si se cae, con espera.
while true; do
  python3 /opt/agent/agent.py
  echo "agent.py terminó ($?); reinicio en 30 s" >&2
  sleep 30
done
