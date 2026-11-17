/**
 * A floating box the user moves by its title bar. The position is in viewport
 * pixels, kept inside the window and remembered per box across reloads.
 */
import { useCallback, useEffect, useState } from 'react';
import type React from 'react';

export interface Point {
  x: number;
  y: number;
}

/** Part of the box that must stay on screen, so it can always be grabbed again. */
const KEEP_VISIBLE = 48;

const storageKey = (key: string) => `nkz-vegetation-float-${key}`;

function clamp(p: Point): Point {
  const maxX = Math.max(0, window.innerWidth - KEEP_VISIBLE);
  const maxY = Math.max(0, window.innerHeight - KEEP_VISIBLE);
  return { x: Math.min(Math.max(p.x, 0), maxX), y: Math.min(Math.max(p.y, 0), maxY) };
}

function load(key: string): Point | null {
  try {
    const raw = window.localStorage.getItem(storageKey(key));
    const p = raw ? JSON.parse(raw) : null;
    return p && Number.isFinite(p.x) && Number.isFinite(p.y) ? p : null;
  } catch {
    return null;
  }
}

function save(key: string, p: Point): void {
  try {
    window.localStorage.setItem(storageKey(key), JSON.stringify(p));
  } catch { /* storage unavailable: the position just is not remembered */ }
}

export function useDraggable(key: string, initial: () => Point) {
  const [pos, setPos] = useState<Point>(() => clamp(load(key) ?? initial()));

  // A smaller window must not leave the box out of reach.
  useEffect(() => {
    const onResize = () => setPos((p) => clamp(p));
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    if (e.button !== 0 || (e.target as HTMLElement).closest('button')) return;
    e.preventDefault();
    const startX = e.clientX;
    const startY = e.clientY;
    const origin = pos;
    let last = origin;
    const move = (ev: PointerEvent) => {
      last = clamp({ x: origin.x + ev.clientX - startX, y: origin.y + ev.clientY - startY });
      setPos(last);
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      save(key, last);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }, [key, pos]);

  return {
    /** Inline position of the floating box (computed, so it cannot be a utility class). */
    style: { left: pos.x, top: pos.y } as React.CSSProperties,
    /** Spread on the title bar. */
    handleProps: { onPointerDown, className: 'cursor-grab select-none touch-none' },
  };
}
