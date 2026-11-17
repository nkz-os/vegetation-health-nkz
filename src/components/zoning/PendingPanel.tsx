/**
 * A proposal made after the zoning was accepted waits here for the technician:
 * which images it uses, what changes per level, where the noted causes go,
 * and its outline on the map. Technicians and admins accept or discard it.
 */
import React, { useEffect, useState } from 'react';
import { useTranslation } from '@nekazari/sdk';
import { Button, Spinner, Switch } from '@nekazari/ui-kit';
import { useVegetationApi } from '../../services/api';
import type { PendingZoning, ZoningLevel } from '../../types';
import { useDraggable } from './useDraggable';

interface PendingPanelProps {
  parcelId: string;
  canEdit: boolean;
  /** Outline of the proposal on the map; null removes it. */
  onPreview: (geojson: PendingZoning['geojson'] | null) => void;
  /** The proposal was accepted or discarded: the layer is redrawn. */
  onDone: () => void;
  /** The zoning changed underneath (409/404): the parent shows the message and redraws. */
  onStale: (message: string) => void;
  onClose: () => void;
}

const LEVEL_ORDER = ['high', 'medium', 'low', 'critical'];

export const PendingPanel: React.FC<PendingPanelProps> = ({
  parcelId, canEdit, onPreview, onDone, onStale, onClose,
}) => {
  const { t, i18n } = useTranslation();
  const api = useVegetationApi();
  // Floats over the map, to the left of its centre; the user moves it by the title bar.
  const drag = useDraggable('pending-panel', () => ({ x: Math.max(0, window.innerWidth / 2 - 384 - 24), y: 96 }));
  const [data, setData] = useState<PendingZoning | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.getPendingZoning(parcelId)
      .then((d) => { if (!cancelled) setData(d); })
      .catch(() => { if (!cancelled) setError(t('zoning.pending.loadFailed', 'The proposal could not be loaded.')); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [api, parcelId, t]);

  // The outline goes away with the panel.
  useEffect(() => () => onPreview(null), []); // eslint-disable-line react-hooks/exhaustive-deps

  const levelName = (level: string | null) => (level ? t(`zoning.level.${level}`, level) : '—');
  const fmtDate = (d: string) => new Date(d).toLocaleDateString(i18n?.language, { timeZone: 'UTC' });
  const fmtLevel = (l: ZoningLevel | undefined) => {
    if (!l) return '—';
    const pct = l.relative_pct == null ? '' : ` · ${l.relative_pct > 0 ? '+' : ''}${l.relative_pct} %`;
    return `${l.area_ha ?? '—'} ha${pct}`;
  };

  const decide = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      onPreview(null);
      onDone();
    } catch (err: any) {
      const status = err?.response?.status;
      if (status === 409 || status === 404) {
        onStale(t('zoning.edit.changed', 'The zoning has changed; open the zone again.'));
        return;
      }
      setError(status === 403
        ? t('zoning.edit.forbidden', 'You are not allowed to edit the zoning.')
        : t('zoning.edit.failed', 'The change could not be saved. Try again.'));
    } finally {
      setBusy(false);
    }
  };

  const pending = data?.pending;
  const empty = !!pending && pending.levels.length === 0;
  const byLevel = (levels: ZoningLevel[] | undefined) =>
    new Map((levels ?? []).map((l) => [l.level, l] as const));
  const before = byLevel(data?.current?.levels);
  const after = byLevel(pending?.levels);
  const rows = LEVEL_ORDER.filter((lv) => before.has(lv) || after.has(lv));

  return (
    <div style={drag.style} className="fixed z-nkz-modal w-96 max-h-96 overflow-y-auto bg-nkz-surface rounded-nkz-md shadow-lg border border-nkz-border p-3 text-nkz-xs text-nkz-text-primary flex flex-col gap-2">
      <div {...drag.handleProps} className={`flex items-start justify-between gap-2 ${drag.handleProps.className}`}>
        <span className="text-nkz-sm font-semibold">{t('zoning.pending.title', 'New zoning proposal')}</span>
        <button
          type="button"
          onClick={onClose}
          className="text-nkz-text-muted hover:text-nkz-text-primary"
          aria-label={t('common.close', 'Close')}
        >
          ×
        </button>
      </div>

      {loading && <Spinner />}
      {error && <p className="text-nkz-danger">{error}</p>}
      {!loading && !pending && !error && (
        <p className="text-nkz-text-muted">{t('zoning.pending.none', 'There is no proposal waiting for review.')}</p>
      )}

      {pending && (
        <>
          {pending.source_dates.length > 0 && (<p>
            {t('zoning.pending.origin', 'Uses {{n}} image dates ({{index}})', {
              n: pending.source_dates.length, index: pending.index_type ?? '—',
            })}
            {pending.new_dates.length > 0 && (
              <>
                {'. '}
                {t('zoning.pending.newDates', '{{n}} new: {{dates}}', {
                  n: pending.new_dates.length, dates: pending.new_dates.map(fmtDate).join(', '),
                })}
              </>
            )}
          </p>)}

          {empty ? (
            <div className="flex flex-col gap-1">
              <p className="font-semibold">{t('zoning.pending.empty', 'The new proposal finds no distinct zones.')}</p>
              {pending.outcome_reason && (
                <p className="text-nkz-text-muted">
                  {t(`zoning.noZonesReason.${pending.outcome_reason}`, pending.outcome_reason)}
                </p>
              )}
            </div>
          ) : (
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-nkz-border text-nkz-text-muted">
                  <th className="py-1 pr-2 font-medium">{t('zoning.pending.level', 'Level')}</th>
                  <th className="py-1 pr-2 font-medium">{t('zoning.pending.current', 'Current')}</th>
                  <th className="py-1 font-medium">{t('zoning.pending.proposed', 'Proposal')}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((lv) => {
                  const changedPresence = before.has(lv) !== after.has(lv);
                  return (
                    <tr key={lv} className={changedPresence ? 'bg-nkz-warning-soft font-semibold' : ''}>
                      <td className="py-1 pr-2">{levelName(lv)}</td>
                      <td className="py-1 pr-2">{fmtLevel(before.get(lv))}</td>
                      <td className="py-1">{fmtLevel(after.get(lv))}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}

          {data!.causes.length > 0 && (
            <div className="flex flex-col gap-1">
              <p className="text-nkz-text-muted">{t('zoning.pending.causes', 'Noted causes')}</p>
              <ul className="list-disc">
                {data!.causes.map((c) => (
                  <li key={c.cause} className="ml-4">
                    «{c.cause}»: {c.to_level
                      ? t('zoning.pending.causeMoves', '{{from}} → {{to}}', {
                          from: levelName(c.from_level), to: levelName(c.to_level),
                        })
                      : t('zoning.pending.causeLost', 'would be lost')}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {!empty && (
            <Switch
              checked={preview}
              onChange={(on: boolean) => { setPreview(on); onPreview(on ? data!.geojson : null); }}
              label={t('zoning.pending.preview', 'Show proposal on the map')}
            />
          )}

          {canEdit && (
            <div className="flex flex-wrap gap-1">
              {!empty && (
                <Button size="sm" variant="primary" loading={busy} disabled={busy}
                  onClick={() => decide(() => api.acceptZoning(parcelId, pending.run_id))}>
                  {t('zoning.pending.accept', 'Accept proposal')}
                </Button>
              )}
              <Button size="sm" variant="secondary" disabled={busy}
                onClick={() => decide(() => api.discardZoning(parcelId, pending.run_id))}>
                {t('zoning.pending.discard', 'Discard')}
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
};
