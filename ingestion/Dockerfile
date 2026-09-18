# Ingestion worker image (spec sections 10.1, 28).
#
# Built from the pinned official DataHub ingestion image so the connector
# dependency tree (postgres/mysql/doris SQLAlchemy drivers, DataHub SDK) never
# touches the API/query-worker images. The platform runner is copied in; it uses
# only stdlib + psycopg2 (bundled in the base image) + the `datahub` CLI.
#
# Base image pin is the lock: acryldata/datahub-ingestion:v1.7.0.1
# (digest recorded in docs/compatibility.md after the first pull).

ARG INGESTION_BASE_IMAGE=acryldata/datahub-ingestion:v1.7.0.1
FROM ${INGESTION_BASE_IMAGE}

# The official image runs as a non-root user; the runner only writes inside the
# mounted work volume. `publish_lineage.py` is a one-shot tool invoked with
# `docker compose run --entrypoint python ingestion /opt/ainative/publish_lineage.py`.
COPY runner.py /opt/ainative/runner.py
COPY publish_lineage.py /opt/ainative/publish_lineage.py

ENTRYPOINT ["python", "/opt/ainative/runner.py"]
