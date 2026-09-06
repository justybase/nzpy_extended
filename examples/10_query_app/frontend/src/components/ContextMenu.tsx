import type { ReactElement } from 'react';

export type MenuItem = { label: string; action: () => void; danger?: boolean; disabled?: boolean };

export function ContextMenu({ x, y, items, onClose }: { x: number; y: number; items: MenuItem[]; onClose: () => void }): ReactElement {
  return <div className="context-menu" style={{ left: x, top: y }} onClick={event => event.stopPropagation()}>
    {items.map(item => <button key={item.label} disabled={item.disabled} className={item.danger ? 'danger' : ''} onClick={() => { item.action(); onClose(); }}>{item.label}</button>)}
  </div>;
}
