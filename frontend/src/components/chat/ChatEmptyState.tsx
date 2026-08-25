export function ChatEmptyState() {
  return (
    <div
      style={{
        flex: 1,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: "var(--color-text-muted)",
        fontSize: "0.9rem",
        textAlign: "center",
        padding: "1rem",
      }}
    >
      Ask a research question to get started. Upload documents on the left
      if you'd like the Knowledge Agent to search them.
    </div>
  );
}
