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

/**
 * Display formatting only. A bar is a length, not a container for its own
 * label - the signed value is text next to it, so a narrow panel cannot make
 * the two overlap. The exact Decimal strings stay in `title` and in the table
 * view, so rounding never hides the audited number.
 */
function formatAmount(raw: string): string {
  const value = Number(raw);
  if (!Number.isFinite(value)) return raw;
  const rounded = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value);
  return `${value < 0 ? "▼" : "▲"} ${rounded}`;
}

function formatShare(raw: string | null): string | null {
  if (!raw) return null;
  const value = Number(raw);
  if (!Number.isFinite(value)) return raw;
  return new Intl.NumberFormat("en-US", { style: "percent", maximumFractionDigits: 1 }).format(value);
}

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
  // The bounded read window is not reflected in `truncated` (that flag covers
  // the stored chart's own cap), so the panel reports the missing points itself
  // rather than letting a "共 N 点" footer contradict the rows on screen.
  const notShown = Math.max(0, chart.total_points - data.length - chart.offset);
  const droppedByCap = chart.truncated ? chart.dropped_points : 0;

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
            {data.map((row) => {
              const share = formatShare(row.net_change_share);
              return (
                <tr key={row.key}>
                  <th scope="row">{row.key}</th>
                  <td title={`${row.delta}${unit ? ` ${unit}` : ""}`}>
                    {formatAmount(row.delta)}
                    {unit ? ` ${unit}` : ""}
                  </td>
                  <td title={row.net_change_share ?? undefined}>{share ?? "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : (
        <ul className="chart-bars" data-testid="chart-bars">
          {data.map((row) => {
            const value = Number(row.delta);
            const width = Math.max(2, Math.round((Math.abs(value) / extent) * 100));
            const share = formatShare(row.net_change_share);
            return (
              <li key={row.key}>
                <div className="chart-row-head">
                  <span className="chart-label">{row.key}</span>
                  <span className="chart-value" title={`${row.delta}${unit ? ` ${unit}` : ""}`}>
                    {formatAmount(row.delta)}
                    {unit ? ` ${unit}` : ""}
                  </span>
                  {share ? (
                    <span className="chart-share muted small" title={row.net_change_share ?? undefined}>
                      净变化占比 {share}
                    </span>
                  ) : (
                    <span className="muted small">基准为零，无法计算占比</span>
                  )}
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
                </div>
                <span
                  className={`chart-bar ${value < 0 ? "negative" : "positive"}`}
                  style={{ width: `${width}%` }}
                  aria-hidden="true"
                />
              </li>
            );
          })}
        </ul>
      )}
      {notShown || droppedByCap ? (
        <p className="notice warn">
          {[
            notShown ? `按变化量从大到小只显示前 ${data.length} 个分组，另有 ${notShown} 个未显示` : null,
            droppedByCap ? `存储图表时已丢弃 ${droppedByCap} 个分组` : null,
          ]
            .filter(Boolean)
            .join("；")}
          （未合并、未补零）。
        </p>
      ) : null}
      <p className="muted small">
        {unit ? `单位 ${unit} · ` : ""}证据 {spec.evidence_ids.length} 条 · 数据引用 {spec.data_ref.type}:{spec.data_ref.id.slice(0, 8)} · 图表 {data.length} / 共 {chart.total_points} 点
        · 数值按显示精度取整，精确值见悬停或表格视图
      </p>
    </section>
  );
}
