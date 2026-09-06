import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';

export default function globalSetup(): void {
  if (!process.env.NZ_E2E || process.env.NZ_E2E_TABLE) return;
  const fixture = resolve('e2e/fixture.py');
  const python = process.env.NZ_E2E_PYTHON || 'python3';
  process.env.NZ_E2E_TABLE = execFileSync(python, [fixture, 'create'], { encoding: 'utf8' }).trim();
  process.env.NZ_E2E_OWNS_FIXTURE = '1';
}
