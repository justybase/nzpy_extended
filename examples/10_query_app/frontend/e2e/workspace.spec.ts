import { expect, test } from '@playwright/test';

test.skip(!process.env.NZ_E2E, 'Set NZ_E2E=1 to run the Netezza browser suite.');

test.describe('Netezza SQL workspace', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText('Netezza SQL Workspace')).toBeVisible();
  });

  test('keeps independent SQL tabs and exposes editor completion', async ({ page }) => {
    await expect(page.locator('.sql-tab')).toHaveCount(1);
    await page.getByRole('button', { name: '＋' }).click();
    await expect(page.locator('.sql-tab')).toHaveCount(2);

    const editor = page.locator('.monaco-editor').last();
    await editor.click();
    await page.keyboard.press('Control+A');
    await page.keyboard.type('sel');
    await expect(page.locator('.suggest-widget')).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('.suggest-widget .monaco-list-row')).not.toHaveCount(0);
  });

  test('executes the isolated fixture and renders a result tab', async ({ page }) => {
    const table = process.env.NZ_E2E_TABLE;
    test.skip(!table, 'The fixture is created by global setup when NZ_E2E=1.');
    const editor = page.locator('.monaco-editor').first();
    await editor.click();
    await page.keyboard.press('Control+A');
    await page.keyboard.type(`SELECT ID, LABEL, AMOUNT FROM ${table} ORDER BY ID;`);
    await page.getByRole('button', { name: '▶ Run' }).click();
    await expect(page.locator('.result-grid-shell')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText('3 rows')).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('.data-grid')).toContainText('alpha');
  });
});
