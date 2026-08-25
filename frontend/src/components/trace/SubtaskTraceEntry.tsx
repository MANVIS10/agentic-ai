import type { SubtaskTrace } from "../../api/types";
import styles from "./SubtaskTraceEntry.module.css";

const SPECIALIST_LABELS: Record<SubtaskTrace["specialist"], string> = {
  research: "Research Agent",
  knowledge: "Knowledge Agent",
  analysis: "Analysis Agent",
};

const SPECIALIST_CLASSES: Record<SubtaskTrace["specialist"], string> = {
  research: styles.specialistResearch,
  knowledge: styles.specialistKnowledge,
  analysis: styles.specialistAnalysis,
};

interface SubtaskTraceEntryProps {
  entry: SubtaskTrace;
}

// Renders only SubtaskTrace's five fields - no path exists here (or
// anywhere in api/types.ts) for a system prompt, credential, or raw tool
// output to flow through (spec §3.2, §10).
export function SubtaskTraceEntry({ entry }: SubtaskTraceEntryProps) {
  const toolsLabel = entry.tools_used.length > 0 ? entry.tools_used.join(", ") : "no tool used";
  const verdictLabel = entry.retry_count > 0 ? "needed one retry" : "passed";

  return (
    <div className={styles.entry}>
      <p className={styles.subtask}>{entry.subtask}</p>
      <div className={styles.row}>
        <span className={`${styles.badge} ${SPECIALIST_CLASSES[entry.specialist]}`}>
          {SPECIALIST_LABELS[entry.specialist]}
        </span>
        <span
          className={`${styles.badge} ${entry.retry_count > 0 ? styles.verdictRetry : styles.verdictPass}`}
        >
          {verdictLabel}
        </span>
      </div>
      <span className={styles.tools}>Tool: {toolsLabel}</span>
    </div>
  );
}
