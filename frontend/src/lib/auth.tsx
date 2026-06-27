"use client";

import { createContext, useContext, useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getCurrentUser, login, logout } from "@/lib/api";
import type { AuthSession } from "@/lib/types";

type AuthContextValue = {
  session?: AuthSession;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<AuthSession>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["auth", "me"],
    queryFn: getCurrentUser,
    retry: false
  });
  const loginMutation = useMutation({
    mutationFn: ({ email, password }: { email: string; password: string }) => login(email, password),
    onSuccess: (session) => {
      queryClient.setQueryData(["auth", "me"], session);
    }
  });
  const logoutMutation = useMutation({
    mutationFn: logout,
    onSuccess: () => {
      queryClient.setQueryData(["auth", "me"], undefined);
      queryClient.clear();
    }
  });
  const value = useMemo<AuthContextValue>(
    () => ({
      session: query.data,
      isLoading: query.isLoading,
      isAuthenticated: Boolean(query.data?.authenticated),
      login: (email, password) => loginMutation.mutateAsync({ email, password }),
      logout: async () => {
        await logoutMutation.mutateAsync();
      }
    }),
    [loginMutation, logoutMutation, query.data, query.isLoading]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useCurrentUser() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useCurrentUser must be used inside AuthProvider");
  }
  return context;
}
