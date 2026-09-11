#!/bin/sh
# Arranque del VPS de la IA: instala la CA de la red y carga sus credenciales.
set -e

# CA "corporativa" (la del internet falso), confiable para el sistema, Python y Node.
for i in $(seq 1 60); do [ -f /ca/ca.crt ] && break; sleep 1; done
if [ -f /ca/ca.crt ]; then
  cp /ca/ca.crt /usr/local/share/ca-certificates/corp-root.crt
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

service cron start >/dev/null 2>&1 || true
exec "$@"
