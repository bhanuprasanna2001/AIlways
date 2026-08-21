"use client";

import { useState, useEffect, useCallback } from "react";
import type { Conversation, Message } from "@/lib/types";
import { CONVERSATIONS_STORAGE_KEY, MAX_CONVERSATIONS } from "@/lib/constants";

// ---------------------------------------------------------------------------
// localStorage helpers — isolated for error handling
// ---------------------------------------------------------------------------

function load(): Conversation[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(CONVERSATIONS_STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function save(conversations: Conversation[]): void {
  try {
    localStorage.setItem(
      CONVERSATIONS_STORAGE_KEY,
      JSON.stringify(conversations),
    );
  } catch (e) {
    // QuotaExceededError — trim oldest conversations and retry
    if (e instanceof DOMException && e.name === "QuotaExceededError") {
      const trimmed = conversations.slice(0, MAX_CONVERSATIONS - 10);
      try {
        localStorage.setItem(
          CONVERSATIONS_STORAGE_KEY,
          JSON.stringify(trimmed),
        );
      } catch {
        // Storage completely full — clear all
        localStorage.removeItem(CONVERSATIONS_STORAGE_KEY);
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useConversations() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [isHydrated, setIsHydrated] = useState(false);

  // Hydrate from localStorage on mount — must complete before
  // consumers can rely on getConversation() returning real data.
  useEffect(() => {
    setConversations(load());
    setIsHydrated(true);
  }, []);

  // Sync across browser tabs
  useEffect(() => {
    function handleStorage(e: StorageEvent) {
      if (e.key === CONVERSATIONS_STORAGE_KEY) {
        setConversations(load());
      }
    }
    window.addEventListener("storage", handleStorage);
    return () => window.removeEventListener("storage", handleStorage);
  }, []);

  const addConversation = useCallback((conv: Conversation) => {
    setConversations((prev) => {
      const updated = [conv, ...prev].slice(0, MAX_CONVERSATIONS);
      save(updated);
      return updated;
    });
  }, []);

  const updateConversation = useCallback(
    (id: string, messages: Message[]) => {
      setConversations((prev) => {
        const updated = prev.map((c) =>
          c.id === id
            ? { ...c, messages, updated_at: new Date().toISOString() }
            : c,
        );
        save(updated);
        return updated;
      });
    },
    [],
  );

  const removeConversation = useCallback((id: string) => {
    setConversations((prev) => {
      const updated = prev.filter((c) => c.id !== id);
      save(updated);
      return updated;
    });
  }, []);

  const clearAll = useCallback(() => {
    setConversations([]);
    save([]);
  }, []);

  const getConversation = useCallback(
    (id: string): Conversation | null => {
      return conversations.find((c) => c.id === id) ?? null;
    },
    [conversations],
  );

  return {
    conversations,
    isHydrated,
    addConversation,
    updateConversation,
    removeConversation,
    clearAll,
    getConversation,
  };
}
