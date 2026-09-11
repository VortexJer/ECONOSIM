# El mundo: internet falso (DNS + HTTP + TLS), gemelos y API de control.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY econosim ./econosim
COPY data ./data
ENV PYTHONUNBUFFERED=1
CMD python -m econosim.run \
    --real-start "${ECONOSIM_REAL_START:-1998-10-14T09:30}" \
    --initial-eur "${ECONOSIM_INITIAL_EUR:-50}" \
    --http 80 --https 443 --dns 53 --answer-ip 10.66.0.2 --ca-dir /ca \
    --control 8080 --control-bind auto --econet 10.66.0.0/24 \
    --agent-env /shared/agent.env --faketime-file /shared/faketime.rc --ledger /data/episodes \
    --speed "${ECONOSIM_SPEED:-1}" --hang "${ECONOSIM_HANG:-300}"
