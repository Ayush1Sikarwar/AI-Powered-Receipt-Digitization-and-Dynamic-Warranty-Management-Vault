import '@testing-library/jest-dom';
import { afterEach, vi } from 'vitest';
import { cleanup } from '@testing-library/react';

Object.defineProperty(URL, 'createObjectURL', {
  writable: true,
  value: () => 'blob:vitest-test',
});

Object.defineProperty(URL, 'revokeObjectURL', {
  writable: true,
  value: () => { },
});

// Clean up rendered components and reset mocks/state between tests to
// prevent test pollution (leaked fetch spies, stale localStorage, etc.).
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  localStorage.clear();
});