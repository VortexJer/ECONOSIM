#!/bin/sh
# Arranque del VPS de la IA: instala la CA de la red y carga sus credenciales.
set -e

# CA "corporativa" (la del internet falso), confiable para el sistema, Python y Node.
for i in $(seq 1 60); do [ -f /shared/ca.crt ] && break; sleep 1; done
if [ -f /shared/ca.crt ]; then
  cp /shared/ca.crt /usr/local/share/ca-certificates/corp-root.crt
  update-ca-certificates >/dev/null 2>&1 || true
fi
cat > /etc/profile.d/00-network.sh <<'EOF'
export SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
export REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
export CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt
export NODE_EXTRA_CA_CERTS=/usr/local/share/ca-certificates/corp-root.crt
EOF

# Credenciales que "el dueño anterior" dejó configuradas (token de Hetzner, etc.).
for i in $(seq 1 60); do [ -f /shared/agent.env ] && break; sleep 1; done
if [ -f /shared/agent.env ]; then
  { echo "# credenciales de servicios"; sed 's/^/export /' /shared/agent.env; } > /etc/profile.d/10-services.sh
fi
chmod 644 /etc/profile.d/*.sh

# Reloj: cuando el mundo publica el desfase, todos los procesos ven la fecha del mundo.
for i in $(seq 1 60); do [ -f /shared/faketime.rc ] && break; sleep 1; done
LIBFT=$(ls /usr/local/lib/faketime/libfaketime.so.1 /usr/lib/*/faketime/libfaketime.so.1 2>/dev/null | head -n1)
if [ -n "$LIBFT" ] && [ -f /shared/faketime.rc ]; then
  echo "$LIBFT" > /etc/ld.so.preload
fi
cat >> /etc/profile.d/00-network.sh <<'EOF'
export FAKETIME_TIMESTAMP_FILE=/shared/faketime.rc FAKETIME_NO_CACHE=1 DONT_FAKE_MONOTONIC=1 FAKETIME_DONT_RESET=1
EOF

service cron start >/dev/null 2>&1 || true
. /etc/profile.d/00-network.sh
[ -f /etc/profile.d/10-services.sh ] && . /etc/profile.d/10-services.sh
exec "$@"
