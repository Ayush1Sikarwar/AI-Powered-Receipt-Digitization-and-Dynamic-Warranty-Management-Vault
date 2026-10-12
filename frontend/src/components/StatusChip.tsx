const STYLES: Record<string, string> = {
  // document / processing states
  UPLOADED: 'bg-slate-100 text-slate-700',
  QUEUED: 'bg-amber-100 text-amber-800',
  PROCESSING: 'bg-blue-100 text-blue-800',
  REVIEW_REQUIRED: 'bg-orange-100 text-orange-800',
  COMPLETED: 'bg-emerald-100 text-emerald-800',
  FAILED: 'bg-red-100 text-red-800',
  DELETED: 'bg-slate-200 text-slate-500',
  // warranty statuses
  ACTIVE: 'bg-emerald-100 text-emerald-800',
  EXPIRING: 'bg-amber-100 text-amber-800',
  EXPIRED: 'bg-red-100 text-red-800',
  NOT_STARTED: 'bg-slate-100 text-slate-600',
  UNKNOWN: 'bg-slate-100 text-slate-500',
  // verification
  DRAFT: 'bg-slate-100 text-slate-600',
  AI_EXTRACTED: 'bg-violet-100 text-violet-800',
  USER_CONFIRMED: 'bg-emerald-100 text-emerald-800',
  PARTIALLY_VERIFIED: 'bg-amber-100 text-amber-800',
  // claim statuses
  READY: 'bg-emerald-100 text-emerald-800',
  SUBMITTED_EXTERNALLY: 'bg-blue-100 text-blue-800',
  IN_PROGRESS: 'bg-blue-100 text-blue-800',
  RESOLVED: 'bg-emerald-100 text-emerald-800',
  CLOSED: 'bg-slate-200 text-slate-500',
};

export default function StatusChip({ status, label }: { status: string; label?: string }) {
  const cls = STYLES[status] ?? 'bg-slate-100 text-slate-600';
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${cls}`}>
      {label ?? status}
    </span>
  );
}
