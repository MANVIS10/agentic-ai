import type { SubtaskTrace } from "../../api/types";
import styles from "./SubtaskTraceEntry.module.css";

const SPECIALIST_LABELS: Record<SubtaskTrace["specialist"], string> = {
  research: "Research Agent",
  knowledge: "Knowledge Agent",
  analysis: "Analysis Agent",
  unknown: "No agent ran",
};

const SPECIALIST_CLASSES: Record<SubtaskTrace["specialist"], string> = {
  research: styles.specialistResearch,
  knowledge: styles.specialistKnowledge,
  analysis: styles.specialistAnalysis,
  unknown: styles.verdictRetry,
};

// The outcome badge reports what actually happened. It previously derived
// its text from retry_count alone, which labelled anything with a retry as
// "needed one retry" - reading as eventual success even when the critic
// never accepted the answer.
const STATUS_LABELS: Record<SubtaskTrace["status"], string> = {
  completed: "passed",
  needs_review: "needs review",
  failed: "failed",
};

interface SubtaskTraceEntryProps {
  entry: SubtaskTrace;
}

// Renders only SubtaskTrace's five fields - no path exists here (or
// anywhere in api/types.ts) for a system prompt, credential, or raw tool
// output to flow through (spec §3.2, §10).
export function SubtaskTraceEntry({ entry }: SubtaskTraceEntryProps) {
  const toolsLabel = entry.tools_used.length > 0 ? entry.tools_used.join(", ") : "no tool used";
  const verdictLabel =
    entry.status === "completed" && entry.retry_count > 0
      ? "passed after one retry"
      : STATUS_LABELS[entry.status];

  return (
    <div className={styles.entry}>
      <p className={styles.subtask}>{entry.subtask}</p>
      <div className={styles.row}>
        <span className={`${styles.badge} ${SPECIALIST_CLASSES[entry.specialist]}`}>
          {SPECIALIST_LABELS[entry.specialist]}
        </span>
        <span
          className={`${styles.badge} ${entry.status === "completed" ? styles.verdictPass : styles.verdictRetry}`}
        >
          {verdictLabel}
        </span>
      </div>
      <span className={styles.tools}>Tool: {toolsLabel}</span>
    </div>
  );
}
