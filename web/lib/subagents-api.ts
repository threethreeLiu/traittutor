/** Compatibility for sessions created before external-agent support was removed. */
export async function getSubagentSettings(): Promise<{ consult_budget: number }> {
  return { consult_budget: 0 };
}
