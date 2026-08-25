import { useAppContext } from "../../state/AppContext";
import styles from "./ExecutionTracePanel.module.css";
import { SubtaskTraceEntry } from "./SubtaskTraceEntry";
import { TraceEmptyState } from "./TraceEmptyState";

// Built entirely from the most recently completed turn's trace, available
// only once /approve returns "completed" - a panel that populates once,
// after the fact, not a live feed (spec §7, since the backend has no
// streaming transport).
export function ExecutionTracePanel() {
  const { chat } = useAppContext();
  const completedTurn = [...chat.turns].reverse().find((turn) => turn.status === "completed");

  return (
    <div className={styles.panel}>
      <p className={styles.heading}>Execution trace</p>
      {!completedTurn || !completedTurn.trace ? (
        <TraceEmptyState />
      ) : (
        <div className={styles.list}>
          {completedTurn.trace.map((entry, index) => (
            <SubtaskTraceEntry key={index} entry={entry} />
          ))}
        </div>
      )}
    </div>
  );
}
