import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, Navigate, useLocation } from "react-router-dom";
import { LogOut, UserRound } from "lucide-react";
import { api } from "@/api";
import { useAuth } from "@/vomitoria";
import { errorMessage, LoadingCard, ErrorCard, currentRoute } from "@/armamentarium";

export function ProfilePage() {
  const auth = useAuth();
  const location = useLocation();
  const playerQuery = useQuery({
    queryKey: ["player", auth.user?.pid],
    queryFn: () => api.getPlayer(auth.user!.pid),
    enabled: Boolean(auth.user?.pid),
  });

  if (auth.status === "anonymous") return <Navigate to="/auth" replace state={{ caller: currentRoute(location) }} />;

  return (
      <section className="section-panel narrow">
        <p className="eyebrow">Player Ledger</p>
        <h1>Profile</h1>
        {playerQuery.isLoading ? <LoadingCard label="Loading profile" /> : null}
        {playerQuery.error ? <ErrorCard message={errorMessage(playerQuery.error)} /> : null}
        {playerQuery.data ? (
          <div className="profile-card">
            <UserRound size={42} />
            <div>
              <h2>{playerQuery.data.display_name}</h2>
              <p className="muted">{playerQuery.data.pid}</p>
              <p>{playerQuery.data.solves.length} solved challenges</p>
            </div>
          </div>
        ) : null}
      </section>
  );
}

export function AccountMenu({ onLogout }: { onLogout: () => Promise<void> }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="account-menu">
      <button
        className="profile-trigger"
        type="button"
        aria-label="Open profile menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <UserRound size={22} />
      </button>
      {open ? (
        <div className="profile-dropdown" role="menu">
          <Link to="/profile" role="menuitem" onClick={() => setOpen(false)}>
            <UserRound size={16} /> Profile
          </Link>
          <button type="button" role="menuitem" onClick={async () => {
            setOpen(false); await onLogout();
            }
          }
          >
            <LogOut size={16} /> Logout
          </button>
        </div>
      ) : null}
    </div>
  );
}