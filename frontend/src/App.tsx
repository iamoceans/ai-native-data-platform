import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { onUnauthenticated } from "./api/client";
import { AppShell } from "./components/AppShell";
import { CatalogPage } from "./pages/CatalogPage";
import { HistoryPage } from "./pages/HistoryPage";
import { LoginPage } from "./pages/LoginPage";
import { SqlWorkspacePage } from "./pages/SqlWorkspacePage";
import { AskPage } from "./pages/AskPage";
import { AnalysisPage } from "./pages/AnalysisPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: false, staleTime: 5_000 },
  },
});

onUnauthenticated(() => {
  queryClient.clear();
  if (!window.location.pathname.startsWith("/login")) {
    window.location.assign("/login");
  }
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<AppShell />}>
            <Route path="/" element={<Navigate to="/ask" replace />} />
            <Route path="/ask" element={<AskPage />} />
            <Route path="/analyses/:id" element={<AnalysisPage />} />
            <Route path="/sql" element={<SqlWorkspacePage />} />
            <Route path="/history" element={<HistoryPage />} />
            <Route path="/catalog" element={<CatalogPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/ask" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
