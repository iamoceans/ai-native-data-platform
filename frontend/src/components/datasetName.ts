import type { DatasetSummary } from "../api/endpoints";

/**
 * Namespace a dataset is addressed by: the schema, or the catalog for engines
 * without a schema level (MySQL registers an empty schema_name). Mirrors the
 * backend's dataset_qualifier so the Catalog's 库 filter and its labels agree.
 */
export function datasetNamespace(
  dataset: Pick<DatasetSummary, "schema_name" | "catalog">,
): string {
  return dataset.schema_name || dataset.catalog;
}

/** `namespace.object_name`, the qualified name shown in the Catalog. */
export function datasetName(
  dataset: Pick<DatasetSummary, "schema_name" | "catalog" | "object_name">,
): string {
  return `${datasetNamespace(dataset)}.${dataset.object_name}`;
}
