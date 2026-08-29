import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import styles from "./MessageBubble.module.css";

interface MessageBubbleProps {
  role: "user" | "assistant" | "rejected";
  content: string;
}

export function MessageBubble({ role, content }: MessageBubbleProps) {
  const rowClass = role === "user" ? styles.rowUser : styles.rowAssistant;
  const bubbleClass =
    role === "user" ? styles.user : role === "rejected" ? styles.rejected : styles.assistant;

  return (
    <div className={`${styles.row} ${rowClass}`}>
      <div className={`${styles.bubble} ${bubbleClass}`}>
        {/* Only the model's answer is parsed as Markdown. synthesize() asks
            for no particular format, so the model falls back on its trained
            habit and emits Markdown - which used to reach the screen as
            literal `**asterisks**` and `###` because this was a bare
            {content}.

            The user's own question and the rejection notice stay literal:
            someone who types `*` meant an asterisk, not emphasis. That also
            keeps the one string that comes from outside the app out of the
            renderer entirely.

            remark-gfm adds tables and strikethrough; without it a table the
            model writes renders as a wall of pipe characters. */}
        {role === "assistant" ? (
          <div className={styles.markdown}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
          </div>
        ) : (
          content
        )}
      </div>
    </div>
  );
}
