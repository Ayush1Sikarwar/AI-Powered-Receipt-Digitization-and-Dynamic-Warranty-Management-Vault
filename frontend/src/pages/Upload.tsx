import { useRef, useState, type DragEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { uploadDocument } from '../services/documents';
import ScanCapture from '../components/ScanCapture';

const ALLOWED = ['jpg', 'jpeg', 'png', 'webp', 'pdf'];
const MAX_BYTES = 15 * 1024 * 1024;

function validateFile(file: File): string | null {
  const ext = file.name.split('.').pop()?.toLowerCase() ?? '';
  if (!ALLOWED.includes(ext)) return 'File type not allowed. Use JPG, PNG, WEBP, or PDF.';
  if (file.size > MAX_BYTES) return 'File is too large (max 15 MB).';
  if (file.size === 0) return 'File is empty.';
  return null;
}

export default function Upload() {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [fileName, setFileName] = useState<string | null>(null);

  const doUpload = async (file: File) => {
    const problem = validateFile(file);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setBusy(true);
    setFileName(file.name);
    try {
      const doc = await uploadDocument(file);
      navigate(`/app/documents/${doc.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setBusy(false);
      setFileName(null);
    }
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) void doUpload(file);
  };

  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-semibold text-slate-900">Upload a document</h1>
      <p className="mt-1 text-sm text-slate-500">
        JPG, JPEG, PNG, WEBP, or PDF · up to 15 MB. Your original is stored securely and never modified.
      </p>

      {error && (
        <div role="alert" className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        className={`mt-6 flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-6 py-16 text-center transition ${dragOver ? 'border-vault-500 bg-vault-50' : 'border-slate-300 bg-white hover:border-vault-400'
          }`}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".jpg,.jpeg,.png,.webp,.pdf"
          aria-label="Receipt file (JPG, PNG, WEBP or PDF)"
          className="absolute opacity-0"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void doUpload(f);
          }}
        />
        {busy ? (
          <>
            <div className="text-lg font-medium text-slate-700">Uploading {fileName}…</div>
            <div className="mt-1 text-sm text-slate-500">Validating and storing your document</div>
          </>
        ) : (
          <>
            <div className="text-lg font-medium text-slate-700">Drag &amp; drop a receipt here</div>
            <div className="mt-1 text-sm text-slate-500">or click to browse</div>
          </>
        )}
      </div>

      <div className="mt-4 text-xs text-slate-400">
        Files are validated by content signature (not just the name), scanned for executable/script content,
        and hashed. Originals are immutable; AI processing happens in the background.
      </div>

      <div className="mt-6 border-t border-slate-200 pt-4">
        <div className="text-sm font-medium text-slate-700">No scanner? Use your camera</div>
        <p className="mt-1 text-sm text-slate-500">
          Take a photo instead — the same cleanup (deskew, crop, contrast) that a physical scanner would give you
          runs automatically once it's uploaded.
        </p>
        <ScanCapture onCapture={(file) => void doUpload(file)} disabled={busy} />
      </div>
    </div>
  );
}