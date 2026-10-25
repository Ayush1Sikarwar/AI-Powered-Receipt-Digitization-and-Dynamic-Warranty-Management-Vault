import { Link, Navigate, Route, Routes } from 'react-router-dom';
import { useAuth, type SessionError } from './hooks/useAuth';
import AppShell from './layouts/AppShell';
import Landing from './pages/Landing';
import Login from './pages/Login';
import Register from './pages/Register';
import Dashboard from './pages/Dashboard';
import Upload from './pages/Upload';
import Documents from './pages/Documents';
import DocumentDetail from './pages/DocumentDetail';
import Purchases from './pages/Purchases';
import PurchaseDetail from './pages/PurchaseDetail';
import Claims from './pages/Claims';
import ClaimDetail from './pages/ClaimDetail';
import Notifications from './pages/Notifications';
import Settings from './pages/Settings';
import NotFound from './pages/NotFound';

function SessionProblem({ error, onRetry }: { error: SessionError; onRetry: () => void }) {
  const unreachable = error.kind === 'unreachable';
  return (
    <div className="mx-auto max-w-md p-10">
      <div className="card">
        <h1 className="text-lg font-semibold text-slate-900">
          {unreachable ? "Can't reach the server" : 'You were signed out'}
        </h1>
        <p className="mt-2 text-sm text-slate-600">
          {unreachable
            ? 'Your session is still saved on this device. Check that the backend is running, then retry.'
            : 'The server rejected your saved session:'}
        </p>
        <p className="mt-2 break-words rounded-md bg-slate-50 px-3 py-2 font-mono text-xs text-slate-600">
          {error.message}
        </p>
        <div className="mt-4 flex gap-3">
          {unreachable && (
            <button className="btn-primary" onClick={onRetry}>
              Retry
            </button>
          )}
          <Link className={unreachable ? 'btn-secondary' : 'btn-primary'} to="/login">
            Log in again
          </Link>
        </div>
      </div>
    </div>
  );
}

function RequireAuth({ children }: { children: JSX.Element }) {
  const { user, initializing, sessionError, refresh } = useAuth();
  if (initializing) return <div className="p-10 text-slate-500">Loading…</div>;
  if (!user && sessionError) return <SessionProblem error={sessionError} onRetry={() => void refresh()} />;
  if (!user) return <Navigate to="/login" replace />;
  return children;
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route
        path="/app"
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="/app/dashboard" replace />} />
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="upload" element={<Upload />} />
        <Route path="documents" element={<Documents />} />
        <Route path="documents/:id" element={<DocumentDetail />} />
        <Route path="purchases" element={<Purchases />} />
        <Route path="purchases/:id" element={<PurchaseDetail />} />
        <Route path="claims" element={<Claims />} />
        <Route path="claims/:id" element={<ClaimDetail />} />
        <Route path="notifications" element={<Notifications />} />
        <Route path="settings" element={<Settings />} />
      </Route>
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}