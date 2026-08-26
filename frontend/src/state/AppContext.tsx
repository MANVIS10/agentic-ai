import { createContext, useContext, useEffect, useRef, type ReactNode } from "react";
import { useChat } from "../hooks/useChat";
import { useDocuments } from "../hooks/useDocuments";
import { useAuth } from "../hooks/useAuth";

interface AppContextValue {
  auth: ReturnType<typeof useAuth>;
  documents: ReturnType<typeof useDocuments>;
  chat: ReturnType<typeof useChat>;
}

const AppContext = createContext<AppContextValue | null>(null);

// The one place userId is read from for every API call (spec §9). Every
// hook below is instantiated here, keyed off the same auth.userId, so
// switching identity naturally clears and re-fetches everything downstream.
export function AppProvider({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const documents = useDocuments(auth.userId, auth.token);
  const chat = useChat(auth.userId);

  // The document list (useDocuments) already resets/re-fetches on userId
  // change on its own. The chat thread doesn't naturally know about
  // identity changes, so it's reset explicitly here whenever the active
  // userId changes - never mixing two identities' data (spec §9).
  const previousUserId = useRef(auth.userId);
  useEffect(() => {
    if (previousUserId.current !== auth.userId) {
      chat.newChat();
      previousUserId.current = auth.userId;
    }
  }, [auth.userId, chat]);

  return (
    <AppContext.Provider value={{ auth, documents, chat }}>{children}</AppContext.Provider>
  );
}

export function useAppContext(): AppContextValue {
  const context = useContext(AppContext);
  if (!context) {
    throw new Error("useAppContext must be used within an AppProvider");
  }
  return context;
}
