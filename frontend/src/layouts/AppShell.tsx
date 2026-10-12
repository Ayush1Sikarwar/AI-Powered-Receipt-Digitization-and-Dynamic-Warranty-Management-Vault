import { useQuery } from '@tanstack/react-query';
import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { listNotifications } from '../services/notifications';

const NAV = [
  { to: '/app/dashboard', label: 'Dashboard', end: false },
  { to: '/app/upload', label: 'Upload', end: false },
  { to: '/app/documents', label: 'Documents', end: false },
  { to: '/app/purchases', label: 'Purchases', end: false },
  { to: '/app/claims', label: 'Claims', end: false },
  { to: '/app/notifications', label: 'Notifications', end: false },
  { to: '/app/settings', label: 'Settings', end: false },
];

export default function AppShell() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const { data: notifs } = useQuery({
    queryKey: ['notifications'],
    queryFn: () => listNotifications({ page_size: 1 }),
    staleTime: 30_000,
  });
  const unread = notifs?.unread_count ?? 0;

  const onLogout = async () => {
    await logout();
    navigate('/login');
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2 px-4 py-3">
          <div className="flex items-center gap-6">
            <span className="text-lg font-semibold text-vault-700">WarrantyVault AI</span>
            <nav className="flex gap-1 text-sm">
              {NAV.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) =>
                    `px-3 py-1.5 rounded-md ${isActive ? 'bg-vault-50 text-vault-700 font-medium' : 'text-slate-600 hover:bg-slate-100'}`
                  }
                >
                  {item.label}
                  {item.to === '/app/notifications' && unread > 0 && (
                    <span className="ml-1.5 rounded-full bg-vault-600 px-1.5 py-0.5 text-[10px] font-semibold text-white">
                      {unread}
                    </span>
                  )}
                </NavLink>
              ))}
            </nav>
          </div>
          <div className="flex items-center gap-3 text-sm text-slate-600">
            <span>{user?.display_name}</span>
            <button className="btn-secondary" onClick={onLogout}>
              Log out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-8">
        <Outlet />
      </main>
    </div>
  );
}
