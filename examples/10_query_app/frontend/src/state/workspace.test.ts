import { describe, expect, it } from 'vitest';
import { initialState, reducer } from './workspace';

describe('workspace reducer', () => {
  it('keeps result tabs owned by their SQL tab', () => {
    const second = reducer(initialState, { type: 'tab.add' });
    const firstId = second.tabs[0].id;
    const secondId = second.tabs[1].id;
    const result = { id: 'r1', sessionId: 's1', queryId: 'q1', statementIndex: 0, label: 'Statement 1', status: 'complete' as const, columns: [], totalRows: 3, createdAt: new Date().toISOString() };
    const withResult = reducer(second, { type: 'query.result', tabId: firstId, result });
    expect(withResult.tabs.find(tab => tab.id === firstId)?.results).toHaveLength(1);
    expect(withResult.tabs.find(tab => tab.id === secondId)?.results).toHaveLength(0);
  });
});
