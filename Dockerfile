# Trading Data Center — hosted (multi-user) image.
# Pure standard-library Python, so no pip install step is needed.
FROM python:3.12-slim

WORKDIR /app
COPY . /app

# HOSTED=1 turns on per-browser private workspaces, binds 0.0.0.0, and starts the
# shared market-data refresh loop. PORT is read from the environment (hosts inject it).
ENV HOSTED=1 \
    PORT=8765

EXPOSE 8765
CMD ["python", "app.py"]
