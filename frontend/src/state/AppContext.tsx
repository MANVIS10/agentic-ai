import { createContext, useContext, useEffect, useRef, type ReactNode } from "react";
import { useChat } from "../hooks/useChat";
import { useDocuments } from "../hooks/useDocuments";
import { useIdentity } from "../hooks/useIdentity";

interface AppContextValue {
  identity: ReturnType<typeof useIdentity>;
  documents: ReturnType<typeof useDocuments>;
  chat: ReturnType<typeof useChat>;
}

const AppContext = createContext<AppContextValue | null>(null);

// The one place userId is read from for every API call (spec §9). Every
// hook below is instantiated here, keyed off the same identity.userId, so
// switching identity naturally clears and re-fetches everything downstream.
export function AppProvider({ children }: { children: ReactNode }) {
  const identity = useIdentity();
  const documents = useDocuments(identity.userId);
  const chat = useChat(identity.userId);

  // The document list (useDocuments) already resets/re-fetches on userId
  // change on its own. The chat thread doesn't naturally know about
  // identity changes, so it's reset explicitly here whenever the active
  // userId changes - never mixing two identities' data (spec §9).
  const previousUserId = useRef(identity.userId);
  useEffect(() => {
    if (previousUserId.current !== identity.userId) {
      chat.newChat();
      previousUserId.current = identity.userId;
    }
  }, [identity.userId, chat]);

  return (
    <AppContext.Provider value={{ identity, documents, chat }}>{children}</AppContext.Provider>
  );
}

export function useAppContext(): AppContextValue {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error("useAppContext must be used within an AppProvider");
  }
  return context;
}
