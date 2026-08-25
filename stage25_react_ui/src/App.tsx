import { AppLayout } from "./components/layout/AppLayout";
import { ChatArea } from "./components/chat/ChatArea";
import { DocumentSidebar } from "./components/documents/DocumentSidebar";
import { ExecutionTracePanel } from "./components/trace/ExecutionTracePanel";
import { IdentityPrompt } from "./components/common/IdentityPrompt";
import { AppProvider, useAppContext } from "./state/AppContext";

function AppShell() {
  const { identity } = useAppContext();

  if (!identity.userId) {
    return <IdentityPrompt onSubmit={identity.setUserId} />;
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
