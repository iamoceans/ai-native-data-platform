#!/usr/bin/env python3
"""Publish declared demo pipeline lineage through the DataHub SDK (spec 11).

Runs inside the pinned ingestion image (it owns the matched DataHub SDK). The
manifest written by ``demo-generate`` names the source/target datasets and the
exact transform SQL hash; this script resolves the *observed* URNs from the
control database (never guessed) and emits one upstream edge per source.

Usage:
  python /opt/ainative/publish_lineage.py \
      --manifest /data/runtime/demo/<run>/pipeline_lineage.json [--env DEV]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import psycopg2  # provided by the official ingestion image

DEFAULT_MANIFEST = "/data/runtime/demo/pipeline_lineage.json"


def log(message: str, **fields) -> None:
    payload = {"message": message}
    payload.update({key: value for key, value in fields.items() if value is not None})
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def dsn_from_env() -> str:
    url = os.environ.get("AIND_DATABASE_URL")
    if not url:
        raise SystemExit("AIND_DATABASE_URL is required")
    return url.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )


def dataset_urns() -> dict[str, str]:
    """qualifier ('namespace.table') -> observed DataHub URN."""
    connection = psycopg2.connect(dsn_from_env())
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT d.catalog_name, d.schema_name, d.object_name, d.datahub_urn
                  FROM datasets d
                 WHERE d.datahub_urn IS NOT NULL AND d.active
                """
            )
            urns: dict[str, str] = {}
            for catalog, schema, name, urn in cursor.fetchall():
                namespace = schema or catalog
                urns[f"{namespace}.{name}"] = urn
            return urns
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="publish declared demo lineage")
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument("--gms-url", default=os.environ.get("DATAHUB_GMS_URL", "http://datahub-gms:8080"))
    parser.add_argument("--token", default=os.environ.get("DATAHUB_TOKEN", ""))
    args = parser.parse_args()

    import urllib.request

    from datahub.emitter.mcp import MetadataChangeProposalWrapper
    from datahub.emitter.rest_emitter import DatahubRestEmitter
    from datahub.metadata.schema_classes import (
        DatasetLineageTypeClass,
        DatasetPropertiesClass,
        UpstreamClass,
        UpstreamLineageClass,
    )

    manifest = json.loads(open(args.manifest, encoding="utf-8").read())
    declared = manifest.get("declared") or []
    if not declared:
        print("manifest declares no lineage edges; nothing to publish")
        return 0

    urns = dataset_urns()
    emitter = DatahubRestEmitter(gms_server=args.gms_url, token=args.token or None)
    failures = 0

    def existing_properties(urn: str) -> dict:
        """Read current custom properties so the merge never drops metadata."""
        query = (
            "query props($urn: String!) { dataset(urn: $urn) { "
            "properties { customProperties { key value } } } }"
        )
        request = urllib.request.Request(
            f"{args.gms_url.rstrip('/')}/api/graphql",
            data=json.dumps({"query": query, "variables": {"urn": urn}}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {args.token}"} if args.token else {}),
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8"))
        if body.get("errors"):
            raise RuntimeError(f"GraphQL errors while reading properties: {body['errors'][:2]}")
        entries = (
            ((body.get("data") or {}).get("dataset") or {}).get("properties") or {}
        ).get("customProperties") or []
        return {entry["key"]: entry["value"] for entry in entries}

    declared_by_target: dict[str, set[str]] = {}
    for edge in declared:
        declared_by_target.setdefault(edge["target_dataset"], set()).add(edge["source_dataset"])

    # Emitting upstreamLineage replaces the whole aspect, so all sources of one
    # target must be grouped into a single emission.
    for target_qualifier, sources in declared_by_target.items():
        target_urn = urns.get(target_qualifier)
        upstreams = []
        for source_qualifier in sorted(sources):
            source_urn = urns.get(source_qualifier)
            if not source_urn or not target_urn:
                failures += 1
                log(
                    "skipped: dataset is not mapped in the platform registry",
                    source=source_qualifier,
                    target=target_qualifier,
                    source_mapped=bool(source_urn),
                    target_mapped=bool(target_urn),
                )
                continue
            upstreams.append(
                UpstreamClass(dataset=source_urn, type=DatasetLineageTypeClass.TRANSFORMED)
            )
        if not upstreams:
            continue
        emitter.emit(
            MetadataChangeProposalWrapper(
                entityUrn=target_urn,
                aspect=UpstreamLineageClass(upstreams=upstreams),
            )
        )
        log(
            "published declared lineage",
            sources=sorted(urns[source] for source in sources if urns.get(source)),
            target=target_urn,
        )

    # The platform labels declared edges by reading this property back.
    for target_qualifier, sources in declared_by_target.items():
        target_urn = urns.get(target_qualifier)
        if not target_urn:
            continue
        properties = existing_properties(target_urn)
        properties["ainative.declared_upstreams"] = ",".join(sorted(sources))
        emitter.emit(
            MetadataChangeProposalWrapper(
                entityUrn=target_urn,
                aspect=DatasetPropertiesClass(customProperties=properties),
            )
        )
        log("published declared-upstream property", target=target_urn, sources=sorted(sources))

    if failures:
        print(f"{failures} edge(s) skipped; run catalog-refresh + metadata-sync first", file=sys.stderr)
        return 1
    print(f"published {len(declared)} declared lineage edge(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
