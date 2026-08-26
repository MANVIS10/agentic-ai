import { AppLayout } from "./components/layout/AppLayout";
import { ChatArea } from "./components/chat/ChatArea";
import { DocumentSidebar } from "./components/documents/DocumentSidebar";
import { ExecutionTracePanel } from "./components/trace/ExecutionTracePanel";
import { IdentityPrompt } from "./components/common/IdentityPrompt";
import { AppProvider, useAppContext } from "./state/AppContext";

function AppShell() {
  const { auth } = useAppContext();

  // Gate on the token, not the name: a persisted user_id with no valid token
  // would mount a shell whose every request 401s.
  if (!auth.token) {
    return (
      <IdentityPrompt
        initialUserId={auth.userId}
        pending={auth.status === "authenticating"}
        error={auth.error}
        onSubmit={auth.signIn}
      />
    );
  }

  return (
    <AppLayout
      sidebar={<DocumentSidebar />}
      chat={<ChatArea />}
      trace={<ExecutionTracePanel />}
    />
  );
}

export default function App() {
  return (
    <AppProvider>
      <AppShell />
    </AppProvider>
  );
}
