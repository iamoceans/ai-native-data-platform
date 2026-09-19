import { useState } from "react";
import type { ChartView } from "../api/endpoints";

/**
 * Controlled chart rendering (spec section 25).
 *
 * Only the kinds the spec allows are drawn, the spec's fields are the only
 * source of data, and colours never carry meaning on their own: every bar is
 * paired with a signed label and the same numbers are always available in the
 * table view. No formatter, no HTML and no URL from the payload is executed.
 */
export function ChartPanel({
  chart,
  onDrilldown,
  busy,
}: {
  chart: ChartView;
  onDrilldown?: (dimension: string, value: string) => void;
  busy?: boolean;
}) {
  const [showTable, setShowTable] = useState(false);
  const { spec, data } = chart;
  const unit = spec.encoding.y.unit ?? "";

  if (!data.length) {
    return (
      <section className="card" aria-label={spec.title} data-testid="chart-panel">
        <div className="card-head"><h2>{spec.title}</h2></div>
        <p className="notice">{chart.empty_state || spec.empty_state}</p>
      </section>
    );
  }

  const values = data.map((row) => Number(row.delta));
  const extent = Math.max(...values.map((value) => Math.abs(value)), 1);
  const drill = spec.interaction.drilldown_dimensions;

  return (
    <section className="card" aria-label={spec.title} data-testid="chart-panel">
      <div className="card-head">
        <h2>{spec.title}</h2>
        <button className="ghost" data-testid="chart-toggle-table" onClick={() => setShowTable((value) => !value)}>
          {showTable ? "显示图表" : "显示表格"}
        </button>
      </div>
      {showTable ? (
        <table className="chart-table" data-testid="chart-table">
          <caption className="muted small">
            {spec.encoding.x.field} → {spec.encoding.y.field}，单位 {unit || "按指标契约"}
          </caption>
          <thead>
            <tr>
              <th scope="col">{spec.encoding.x.field}</th>
              <th scope="col">{spec.encoding.y.field}</th>
              <th scope="col">净变化占比</th>
            </tr>
          </thead>
          <tbody>
            {data.map((row) => (
              <tr key={row.key}>
                <th scope="row">{row.key}</th>
                <td>{row.delta}</td>
                <td>{row.net_change_share ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <ul className="chart-bars" data-testid="chart-bars">
          {data.map((row) => {
            const value = Number(row.delta);
            const width = Math.max(2, Math.round((Math.abs(value) / extent) * 100));
            return (
              <li key={row.key}>
                <span className="chart-label">{row.key}</span>
                <span className={`chart-bar ${value < 0 ? "negative" : "positive"}`} style={{ width: `${width}%` }}>
                  {value < 0 ? "▼" : "▲"} {row.delta}
                  {unit ? ` ${unit}` : ""}
                </span>
                <span className="muted small">
                  {row.net_change_share ? `净变化占比 ${row.net_change_share}` : "基准为零，无法计算占比"}
                </span>
                {onDrilldown && drill.length ? (
                  <button
                    className="ghost small"
                    data-testid="chart-drilldown"
                    disabled={busy}
                    onClick={() => row.dimension_values[0] != null && onDrilldown(drill[0], String(row.dimension_values[0]))}
                  >
                    下钻 {drill[0]}
                  </button>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      {chart.truncated ? (
        <p className="notice warn">
          图表只显示前 {data.length} 个分组，另有 {chart.dropped_points} 个未显示（未合并、未补零）。
        </p>
      ) : null}
      <p className="muted small">
        证据 {spec.evidence_ids.length} 条 · 数据引用 {spec.data_ref.type}:{spec.data_ref.id.slice(0, 8)} · 共 {chart.total_points} 点
      </p>
    </section>
  );
}
