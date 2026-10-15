import { t, useLocale, formatDate } from "../i18n";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { listQueries } from "../api/endpoints";
import { StatusBadge, statusLabel } from "../components/StatusBadge";

const STATUSES = [
  "",
  "QUEUED",
  "RUNNING",
  "SUCCEEDED",
  "FAILED",
  "CANCEL_REQUESTED",
  "CANCELLED",
  "TIMED_OUT",
  "LOST",
];

export function HistoryPage() {
  useLocale();
  const [status, setStatus] = useState("");
  const queries = useQuery({
    queryKey: ["queries", status],
    queryFn: () => listQueries(status || undefined),
  });

  return (
    <section className="card">
      <div className="card-head">
        <h1>{t("Query history")}</h1>
        <label className="inline">
          {t("Status")}{" "}
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            {STATUSES.map((item) => (
              <option key={item} value={item}>
                {item ? statusLabel(item) : t("all")}
              </option>
            ))}
          </select>
        </label>
      </div>
      {queries.isLoading ? <p className="muted">{t("Loading…")}</p> : null}
      {queries.data ? (
        <div className="table-scroll">
          <table className="result-table plain">
            <thead>
              <tr>
                <th>{t("Created")}</th>
                <th>{t("Status")}</th>
                <th>SQL</th>
                <th>{t("Rows")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {queries.data.items.map((item) => (
                <tr key={item.id}>
                  <td className="nowrap">{formatDate(item.created_at)}</td>
                  <td>
                    <StatusBadge status={item.status} />
                  </td>
                  <td className="sql-cell">{item.sql}</td>
                  <td>{item.result ? item.result.row_count : "—"}</td>
                  <td>
                    <Link to={`/sql?query=${item.id}`}>{t("Open")}</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}
