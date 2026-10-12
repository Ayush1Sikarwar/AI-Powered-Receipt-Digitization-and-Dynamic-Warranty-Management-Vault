import { Link } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';

export default function Landing() {
  const { user } = useAuth();
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-4">
          <span className="text-lg font-semibold text-vault-700">WarrantyVault AI</span>
          <div className="flex gap-3">
            {user ? (
              <Link to="/app/dashboard" className="btn-primary">
                Open dashboard
              </Link>
            ) : (
              <>
                <Link to="/login" className="btn-secondary">
                  Log in
                </Link>
                <Link to="/register" className="btn-primary">
                  Create account
                </Link>
              </>
            )}
          </div>
        </div>
      </header>
      <main className="flex-1">
        <section className="mx-auto max-w-3xl px-4 py-20 text-center">
          <h1 className="text-4xl font-bold text-slate-900">
            Upload the receipt once.
            <br />
            <span className="text-vault-600">Know what's covered — and when.</span>
          </h1>
          <p className="mt-6 text-lg text-slate-600">
            WarrantyVault AI turns physical purchase evidence into structured, warranty-aware records. We
            enhance, read, and verify your receipts, identify the product, map the manufacturer warranty,
            track expiries, and help you prepare a claim-ready package.
          </p>
          <div className="mt-10 grid gap-4 sm:grid-cols-3 text-left">
            <h2 className="sr-only">How WarrantyVault works</h2>
            <div className="card">
              <h3 className="font-semibold text-slate-800">AI-assisted, human-verified</h3>
              <p className="mt-1 text-sm text-slate-600">
                Extracted fields carry confidence and provenance. Low-confidence values are flagged for your
                review — never silently invented.
              </p>
            </div>
            <div className="card">
              <h3 className="font-semibold text-slate-800">Warranty intelligence</h3>
              <p className="mt-1 text-sm text-slate-600">
                Manufacturer coverage is mapped from approved sources with full provenance, and clearly
                labeled as manufacturer warranty (not legal advice).
              </p>
            </div>
            <div className="card">
              <h3 className="font-semibold text-slate-800">Alerts & claims</h3>
              <p className="mt-1 text-sm text-slate-600">
                Get reminders before coverage lapses and generate a claim-ready package with verified
                evidence.
              </p>
            </div>
          </div>
        </section>
      </main>
      <footer className="border-t border-slate-200 bg-white py-6 text-center text-xs text-slate-400">
        WarrantyVault AI · MVP
      </footer>
    </div>
  );
}
