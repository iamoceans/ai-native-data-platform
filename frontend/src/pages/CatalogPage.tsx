import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { getDatasetContext, getDatasetSchema, listDatasets } from "../api/endpoints";
import { StatusBadge } from "../components/StatusBadge";

export function CatalogPage() {
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const datasets = useQuery({
    queryKey: ["datasets", search],
    queryFn: () => listDatasets(search || undefined),
  });
  const context = useQuery({
    queryKey: ["dataset", selected],
    queryFn: () => getDatasetContext(selected as string),
    enabled: Boolean(selected),
  });
  const schema = useQuery({
    queryKey: ["dataset-schema", selected],
    queryFn: () => getDatasetSchema(selected as string),
    enabled: Boolean(selected),
  });

  return (
    <div className="catalog">
      <section className="card">
        <div className="card-head">
          <h1>Catalog</h1>
          <span className="muted small">Only datasets you are authorized to discover are listed.</span>
        </div>
        <input
          placeholder="Search tables…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          data-testid="catalog-search"
        />
        <ul className="dataset-list">
          {(datasets.data ?? []).map((dataset) => (
            <li key={dataset.id}>
              <button
                className={dataset.id === selected ? "dataset active" : "dataset"}
                onClick={() => setSelected(dataset.id)}
              >
                <span className="dataset-name">
                  {dataset.schema_name}.{dataset.object_name}
                </span>
                <span className="dataset-meta">
                  {dataset.object_type} · <StatusBadge status={dataset.sync_status} />
                </span>
              </button>
            </li>
          ))}
        </ul>
        {datasets.data && datasets.data.length === 0 ? (
          <p className="muted">
            No datasets visible. An administrator must register the data source, refresh its catalog and
            grant your role the discover permission.
          </p>
        ) : null}
      </section>

      {selected ? (
        <section className="card">
          <div className="card-head">
            <h2>{context.data ? `${context.data.schema_name}.${context.data.object_name}` : "Dataset"}</h2>
          </div>
          {context.data?.schema_hash ? (
            <p className="muted small">
              schema hash <code>{context.data.schema_hash.slice(0, 16)}…</code>
              {schema.data ? <> · source {schema.data.source}</> : null}
            </p>
          ) : null}
          {schema.data ? (
            <div className="table-scroll">
              <table className="result-table plain">
                <thead>
                  <tr>
                    <th>Column</th>
                    <th>Type</th>
                    <th>Nullable</th>
                  </tr>
                </thead>
                <tbody>
                  {schema.data.columns.map((column) => (
                    <tr key={column.name}>
                      <td>{column.name}</td>
                      <td>{column.type}</td>
                      <td>{column.nullable === null ? "—" : column.nullable ? "yes" : "no"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted">Loading schema…</p>
          )}
          <p className="muted small">
            Lineage is not available in M1 (DataHub integration lands in M3) — the status is reported as
            unknown rather than simulated.
          </p>
        </section>
      ) : null}
    </div>
  );
}
