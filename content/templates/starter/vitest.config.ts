import { coverageConfigDefaults, defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'jsdom',
    coverage: {
      provider: 'v8',
      reporter: ['text-summary', 'lcov'],
      exclude: [...coverageConfigDefaults.exclude, 'src/api/generated/**', 'src/main.tsx'],
      thresholds: { lines: 95, statements: 95, functions: 95, branches: 90 },
    },
  },
});
