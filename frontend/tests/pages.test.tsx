import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Upload from '../src/pages/Upload';
import Documents from '../src/pages/Documents';

function makeClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderWithProviders(ui: JSX.Element, path = '/app/upload') {
  const qc = makeClient();
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('Upload page', () => {
  it('renders the drop zone and upload prompt', () => {
    renderWithProviders(<Upload />);
    expect(screen.getByText(/drag & drop a receipt/i)).toBeInTheDocument();
  });

  it('shows the camera-scan option for people without a physical scanner', () => {
    renderWithProviders(<Upload />);
    expect(screen.getByRole('button', { name: /scan with camera/i })).toBeInTheDocument();
  });

  it('falls back gracefully when camera capture is unsupported', async () => {
    const original = navigator.mediaDevices;
    Object.defineProperty(navigator, 'mediaDevices', { value: undefined, configurable: true });
    renderWithProviders(<Upload />);
    fireEvent.click(screen.getByRole('button', { name: /scan with camera/i }));
    await waitFor(() => {
      expect(screen.getByText(/not supported in this browser/i)).toBeInTheDocument();
    });
    Object.defineProperty(navigator, 'mediaDevices', { value: original, configurable: true });
  });

  it('shows a client-side validation error for disallowed types', async () => {
    // Stub the upload to ensure a .txt never reaches the network.
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('should not be called'));
    renderWithProviders(<Upload />);
    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
    const txt = new File(['hello'], 'notes.txt', { type: 'text/plain' });
    Object.defineProperty(fileInput, 'files', { value: [txt], configurable: true });
    fireEvent.change(fileInput, { target: { files: [txt] } });
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/file type not allowed/i);
    });
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe('Documents page', () => {
  it('renders the list from the API', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        text: async () =>
          JSON.stringify({
            data: {
              items: [
                {
                  id: 1,
                  uuid: 'u1',
                  document_type: 'RECEIPT',
                  original_filename: 'receipt.png',
                  mime_type: 'image/png',
                  file_size: 1000,
                  page_count: 1,
                  status: 'COMPLETED',
                  sha256: 'abc',
                  created_at: '2026-08-01T00:00:00Z',
                  updated_at: '2026-08-01T00:00:00Z',
                  error: null,
                },
              ],
              total: 1,
              page: 1,
              page_size: 50,
            },
            request_id: 'r',
          }),
      } as Response);
    renderWithProviders(<Documents />, '/app/documents');
    await waitFor(() => {
      expect(screen.getByText('receipt.png')).toBeInTheDocument();
    });
    expect(screen.getByText('COMPLETED')).toBeInTheDocument();
    vi.restoreAllMocks();
    void fetchSpy;
  });
});