import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';

export default function globalTeardown(): void {
  const table = process.env.NZ_E2E_TABLE;
  if (!process.env.NZ_E2E || process.env.NZ_E2E_OWNS_FIXTURE !== '1' || !table) return;
  const fixture = resolve('e2e/fixture.py');
  const python = process.env.NZ_E2E_PYTHON || 'python3';
  execFileSync(python, [fixture, 'drop', table], { stdio: 'inherit' });
}
