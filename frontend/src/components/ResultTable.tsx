import { t, useLocale } from "../i18n";
import type { ColumnInfo } from "../api/endpoints";

interface Props {
  columns: ColumnInfo[];
  rows: unknown[][];
  truncated: boolean;
  rowOffset?: number;
}

function renderCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function ResultTable({ columns, rows, truncated, rowOffset = 0 }: Props) {
  useLocale();
  if (columns.length === 0) {
    return <p className="muted">{t("No result columns.")}</p>;
  }
  return (
    <div className="result-wrap">
      {truncated ? (
        <p className="notice warn">
          {t(
            "Result was truncated at the row or byte limit. Do not use it for exact totals.",
          )}
        </p>
      ) : null}
      <div className="table-scroll">
        <table className="result-table">
          <thead>
            <tr>
              <th scope="col">#</th>
              {columns.map((column) => (
                <th key={column.id ?? column.name}>
                  <span className="col-name">{column.name}</span>
                  <span className="col-type">{column.type}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                <th scope="row" data-testid="result-row-number">{rowOffset + rowIndex + 1}</th>
                {row.map((cell, cellIndex) => (
                  <td key={cellIndex} title={renderCell(cell)}>
                    {renderCell(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length === 0 ? (
        <p className="muted">{t("No rows returned.")}</p>
      ) : null}
    </div>
  );
}
