import { useEffect, useRef, useState } from 'react';

/**
 * Lets someone without a physical scanner use their device camera instead.
 * This only handles capture — the existing server-side pipeline
 * (backend/app/ai/preprocessing/enhance.py) already does boundary
 * detection, perspective correction, deskew, denoise, and contrast
 * normalization on every uploaded image, so a live camera photo gets the
 * same "flattened receipt" treatment as a proper scanner would produce.
 */

interface ScanCaptureProps {
    onCapture: (file: File) => void;
    disabled?: boolean;
}

type CameraState = 'idle' | 'starting' | 'streaming' | 'unavailable' | 'denied';

export default function ScanCapture({ onCapture, disabled }: ScanCaptureProps) {
    const videoRef = useRef<HTMLVideoElement>(null);
    const streamRef = useRef<MediaStream | null>(null);
    const [state, setState] = useState<CameraState>('idle');
    const [errorMsg, setErrorMsg] = useState<string | null>(null);

    const stopStream = () => {
        streamRef.current?.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
    };

    useEffect(() => stopStream, []); // stop the camera if the page navigates away mid-scan

    const start = async () => {
        if (!navigator.mediaDevices?.getUserMedia) {
            setState('unavailable');
            setErrorMsg('Camera capture is not supported in this browser.');
            return;
        }
        setState('starting');
        setErrorMsg(null);
        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } },
                audio: false,
            });
            streamRef.current = stream;
            if (videoRef.current) {
                videoRef.current.srcObject = stream;
                await videoRef.current.play();
            }
            setState('streaming');
        } catch {
            stopStream();
            setState('denied');
            setErrorMsg('Camera access was denied or unavailable. You can still upload a file instead.');
        }
    };

    const cancel = () => {
        stopStream();
        setState('idle');
    };

    const capture = () => {
        const video = videoRef.current;
        if (!video || video.videoWidth === 0) return;
        const canvas = document.createElement('canvas');
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        canvas.toBlob(
            (blob) => {
                if (blob) {
                    onCapture(new File([blob], `scan-${Date.now()}.jpg`, { type: 'image/jpeg' }));
                }
                stopStream();
                setState('idle');
            },
            'image/jpeg',
            0.92,
        );
    };

    if (state === 'idle' || state === 'starting') {
        return (
            <div className="mt-4">
                <button
                    type="button"
                    onClick={start}
                    disabled={disabled || state === 'starting'}
                    className="inline-flex items-center gap-2 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:border-vault-400 hover:text-vault-700 disabled:cursor-not-allowed disabled:opacity-50"
                >
                    {state === 'starting' ? 'Starting camera…' : 'Scan with camera'}
                </button>
                {errorMsg && <p className="mt-2 text-xs text-slate-500">{errorMsg}</p>}
            </div>
        );
    }

    if (state === 'unavailable' || state === 'denied') {
        return (
            <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                {errorMsg}
                <button type="button" onClick={start} className="ml-2 underline">
                    Try again
                </button>
            </div>
        );
    }

    // streaming
    return (
        <div className="mt-4 overflow-hidden rounded-lg border border-slate-300 bg-black">
            <div className="relative">
                <video ref={videoRef} className="w-full" playsInline muted aria-label="Camera preview for scanning a receipt" />
                <div className="pointer-events-none absolute inset-6 rounded-md border-2 border-dashed border-white/70" />
            </div>
            <div className="flex items-center justify-between gap-2 bg-slate-900 px-3 py-2">
                <button
                    type="button"
                    onClick={cancel}
                    className="rounded-md px-3 py-1.5 text-sm font-medium text-slate-200 hover:bg-slate-800"
                >
                    Cancel
                </button>
                <p className="text-xs text-slate-400">Line up the receipt inside the frame, then capture.</p>
                <button
                    type="button"
                    onClick={capture}
                    className="rounded-md bg-vault-600 px-4 py-1.5 text-sm font-semibold text-white hover:bg-vault-700"
                >
                    Capture
                </button>
            </div>
        </div>
    );
}