import '@testing-library/jest-dom';

Object.defineProperty(URL, 'createObjectURL', {
  writable: true,
  value: () => 'blob:vitest-test',
});

Object.defineProperty(URL, 'revokeObjectURL', {
  writable: true,
  value: () => { },
});