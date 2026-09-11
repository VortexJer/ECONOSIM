# El VPS de la IA: un Debian normal con lo que trae un VPS recién instalado.
# Sin nada del simulador dentro. Solo la CA "corporativa" y sus credenciales.
FROM debian:bookworm-slim AS ftbuild
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates build-essential && git clone --depth 1 --branch v0.9.11 https://github.com/wolfcw/libfaketime /tmp/lft && make -C /tmp/lft/src

FROM debian:bookworm-slim
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl wget git vim-tiny nano less procps net-tools iproute2 dnsutils \
        python3 python3-pip python3-venv nodejs npm sqlite3 cron jq unzip tmux openssh-client python3-requests \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -s /bin/bash agent
COPY --from=ftbuild /tmp/lft/src/libfaketime.so.1 /usr/local/lib/faketime/libfaketime.so.1
COPY sandbox/agent-entrypoint.sh /usr/local/sbin/agent-entrypoint
RUN chmod 755 /usr/local/sbin/agent-entrypoint
# El agente que el dueño dejó instalado como servicio.
COPY agent /opt/agent
RUN chmod 755 /opt/agent/run.sh && mkdir -p /var/log/agent
# Reloj del sistema = reloj del mundo (libfaketime lee el desfase que publica el mundo).
ENV FAKETIME_TIMESTAMP_FILE=/shared/faketime.rc FAKETIME_NO_CACHE=1 DONT_FAKE_MONOTONIC=1 FAKETIME_DONT_RESET=1
WORKDIR /home/agent
ENTRYPOINT ["/usr/local/sbin/agent-entrypoint"]
CMD ["/opt/agent/run.sh"]
