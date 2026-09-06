import type { ReactElement } from 'react';

export type MenuItem = { label: string; action: () => void; danger?: boolean; disabled?: boolean };

export function ContextMenu({ x, y, items, onClose }: { x: number; y: number; items: MenuItem[]; onClose: () => void }): ReactElement {
  const style = {
    left: Math.min(x, window.innerWidth - 230),
    top: Math.min(y, window.innerHeight - items.length * 34 - 24),
  };
  return <div className="context-menu" style={style} onClick={event => event.stopPropagation()} role="menu">
    {items.map(item => <button key={item.label} disabled={item.disabled} className={item.danger ? 'danger' : ''} role="menuitem" onClick={() => { item.action(); onClose(); }}>{item.label}</button>)}
  </div>;
}
