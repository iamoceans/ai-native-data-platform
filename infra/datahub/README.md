# DataHub pinned compose (M3)

`compose.pinned.yaml` is the official DataHub quickstart compose for release
**v1.7.0.1** (published 2026-09-03), downloaded verbatim from:

https://raw.githubusercontent.com/datahub-project/datahub/v1.7.0.1/docker/quickstart/docker-compose.quickstart-profile.yml

It is stored here so the DataHub service stack version cannot drift silently.
The images are not pulled yet; the DataHub integration (ingestion recipes, URN
mapping, lineage, catalog) is milestone M3. See `docs/todo.md`.

Environment (infra/versions.env):
- `DATAHUB_VERSION=v1.7.0.1`
- Application images: `acryldata/datahub-{gms,frontend-react,upgrade,actions}:v1.7.0.1`
- Internal services pinned by the official file: `mysql:8.2`,
  `opensearchproject/opensearch:2.19.3`, `confluentinc/cp-kafka:8.2.2`

The stack will join the shared external Docker network `datahub-net` so that the
backend and ingestion services can reach GMS while the browser only sees the API
and (for administrators) the DataHub UI on loopback.
