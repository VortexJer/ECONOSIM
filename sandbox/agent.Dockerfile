# El VPS de la IA: un Debian normal con lo que trae un VPS recién instalado.
# Sin nada del simulador dentro. Solo la CA "corporativa" y sus credenciales.
FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl wget git vim-tiny nano less procps net-tools iproute2 dnsutils \
        python3 python3-pip python3-venv nodejs npm sqlite3 cron jq unzip tmux openssh-client \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -s /bin/bash agent
COPY agent-entrypoint.sh /usr/local/sbin/agent-entrypoint
RUN chmod 755 /usr/local/sbin/agent-entrypoint
WORKDIR /home/agent
ENTRYPOINT ["/usr/local/sbin/agent-entrypoint"]
CMD ["sleep", "infinity"]
