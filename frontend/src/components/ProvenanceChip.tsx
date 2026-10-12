import type { Provenance } from '../types/purchases';

/**
 * Provenance chip for the TSD §42 traceability chain.
 *
 * Each category is visually distinct on purpose — the dashboard must make it
 * impossible to mistake AI-inferred data for a verified fact:
 *   - AI Extracted   : purple (machine-produced, unconfirmed)
 *   - User Confirmed : blue   (the user stands behind it)
 *   - Verified       : green  (deterministic, reproducible match)
 *   - External Source: teal   (obtained from a manufacturer/official source)
 *   - Inferred       : amber  (heuristic — explicitly NOT verified)
 *   - Computed       : slate  (deterministic computation from inputs)
 */
const STYLES: Record<Provenance, string> = {
  AI_EXTRACTED: 'bg-purple-50 text-purple-700 ring-purple-200',
  USER_CONFIRMED: 'bg-blue-50 text-blue-700 ring-blue-200',
  VERIFIED: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  EXTERNAL_SOURCE: 'bg-teal-50 text-teal-700 ring-teal-200',
  INFERRED: 'bg-amber-50 text-amber-700 ring-amber-200',
  COMPUTED: 'bg-slate-100 text-slate-600 ring-slate-300',
};

const DOT: Record<Provenance, string> = {
  AI_EXTRACTED: 'bg-purple-500',
  USER_CONFIRMED: 'bg-blue-500',
  VERIFIED: 'bg-emerald-500',
  EXTERNAL_SOURCE: 'bg-teal-500',
  INFERRED: 'bg-amber-500',
  COMPUTED: 'bg-slate-400',
};

const LABELS: Record<Provenance, string> = {
  AI_EXTRACTED: 'AI Extracted',
  USER_CONFIRMED: 'User Confirmed',
  VERIFIED: 'Verified',
  EXTERNAL_SOURCE: 'External Source',
  INFERRED: 'Inferred',
  COMPUTED: 'Computed',
};

export default function ProvenanceChip({
  provenance,
  label,
  className = '',
}: {
  provenance: Provenance;
  label?: string;
  className?: string;
}) {
  const text = label ?? LABELS[provenance] ?? provenance;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium ring-1 ${
        STYLES[provenance] ?? STYLES.COMPUTED
      } ${className}`}
      title={`Data provenance: ${text}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${DOT[provenance] ?? DOT.COMPUTED}`} />
      {text}
    </span>
  );
}

export function ProvenanceLegend({ legend }: { legend?: Record<string, string> }) {
  const entries: [Provenance, string][] = [
    ['AI_EXTRACTED', 'AI Extracted'],
    ['USER_CONFIRMED', 'User Confirmed'],
    ['VERIFIED', 'Verified'],
    ['EXTERNAL_SOURCE', 'External Source'],
    ['INFERRED', 'Inferred'],
    ['COMPUTED', 'Computed'],
  ];
  return (
    <div className="flex flex-wrap items-center gap-2">
      {entries.map(([key, fallback]) => (
        <ProvenanceChip key={key} provenance={key} label={legend?.[key] ?? fallback} />
      ))}
    </div>
  );
}
