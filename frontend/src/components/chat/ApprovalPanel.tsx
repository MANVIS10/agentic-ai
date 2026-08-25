import type { ChatTurn } from "../../hooks/useChat";
import styles from "./ApprovalPanel.module.css";

interface ApprovalPanelProps {
  turn: ChatTurn;
  disabled: boolean;
  onApprove: () => void;
  onReject: () => void;
}

// Two explicit controls, not a toggle or a y/n text field (spec §8). Only
// reachable for the specific thread that just received an
// approval_prompt - there's no other way to resume a paused thread.
export function ApprovalPanel({ turn, disabled, onApprove, onReject }: ApprovalPanelProps) {
  return (
    <div className={styles.panel}>
      {turn.approvalPrompt && <p className={styles.prompt}>{turn.approvalPrompt}</p>}
      <ol className={styles.subtasks}>
        {turn.subtasks.map((subtask, index) => (
          <li key={index}>{subtask}</li>
        ))}
      </ol>
      <div className={styles.actions}>
        <button className={styles.approve} type="button" disabled={disabled} onClick={onApprove}>
          Approve
        </button>
        <button className={styles.reject} type="button" disabled={disabled} onClick={onReject}>
          Reject
        </button>
      </div>
    </div>
  );
}
