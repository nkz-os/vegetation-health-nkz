/**
 * Card of a clicked zone of the parcel's zoning in force.
 *
 * Everyone reads it; technicians and admins also accept the zoning, note or
 * remove the cause of a zone and merge two levels (the backend enforces the
 * same roles: hiding the buttons is only a convenience).
 */
import React, { useMemo, useState } from 'react';
import { useTranslation } from '@nekazari/sdk';
import { Badge, Button, Textarea } from '@nekazari/ui-kit';
import { useVegetationApi } from '../../services/api';

/** Roles that may change a zoning; field workers (Farmer) only read it. */
export const ZONING_EDITOR_ROLES = ['TechnicalConsultant', 'TenantAdmin', 'PlatformAdmin'];

export const canEditZoning = (roles: string[] | undefined): boolean =>
  (roles ?? []).some((r) => ZONING_EDITOR_ROLES.includes(r));

export interface ZoneCardZone {
  entityId: string;
  runId: string;
  /** False for zonings from before versions (no ids): read-only card. */
  editable: boolean;
  rank: number;
  level: string;
  pct: number;
  area: number | null;
  confirming: number | null;
  total: number | null;
  accepted: boolean;
  acceptedAt: string | null;
  cause: string | null;
}

export interface ZoneCardLevel {
  rank: number;
  level: string;
  area: number | null;
}

interface ZoneCardProps {
  parcelId: string;
  zone: ZoneCardZone;
  /** Levels present in the zoning (one zone per level). */
  levels: ZoneCardLevel[];
  canEdit: boolean;
  hasPending: boolean;
  /** After an action: idsChanged = the zoning got new zone ids (merge), so the card closes. */
  onChanged: (idsChanged: boolean) => void;
  /** The zoning changed underneath (409/404): the parent shows the message and redraws. */
  onStale: (message: string) => void;
  onOpenPending: () => void;
  onClose: () => void;
}

type Mode = 'view' | 'note' | 'merge' | 'confirmMerge';

const MAX_CAUSE = 500;

export const ZoneCard: React.FC<ZoneCardProps> = ({
  parcelId, zone, levels, canEdit, hasPending, onChanged, onStale, onOpenPending, onClose,
}) => {
  const { t, i18n } = useTranslation();
  const api = useVegetationApi();
  const [mode, setMode] = useState<Mode>('view');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [causeText, setCauseText] = useState(zone.cause ?? '');
  const others = useMemo(() => levels.filter((l) => l.rank !== zone.rank), [levels, zone.rank]);
  const [otherRank, setOtherRank] = useState<number | null>(others[0]?.rank ?? null);
  const [targetRank, setTargetRank] = useState<number | null>(null);

  const levelName = (level: string) => t(`zoning.level.${level}`, level);
  const pctText = `${zone.pct > 0 ? '+' : ''}${zone.pct} %`;
  const self: ZoneCardLevel = { rank: zone.rank, level: zone.level, area: zone.area };
  const other = others.find((l) => l.rank === otherRank) ?? null;
  // Default result: the level with more hectares (owner decision, spec §11).
  const defaultTarget = other && (other.area ?? 0) > (self.area ?? 0) ? other.rank : self.rank;
  const chosenTarget = targetRank ?? defaultTarget;

  const run = async (action: () => Promise<unknown>, idsChanged: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      setMode('view');
      onChanged(idsChanged);
    } catch (err: any) {
      const status = err?.response?.status;
      if (status === 409 || status === 404) {
        onStale(t('zoning.edit.changed', 'The zoning has changed; open the zone again.'));
      } else if (status === 403) {
        setError(t('zoning.edit.forbidden', 'You are not allowed to edit the zoning.'));
      } else {
        setError(t('zoning.edit.failed', 'The change could not be saved. Try again.'));
      }
    } finally {
      setBusy(false);
    }
  };

  const acceptedText = zone.accepted
    ? zone.acceptedAt
      ? t('zoning.edit.acceptedOn', 'Accepted on {{date}}', {
          date: new Date(zone.acceptedAt).toLocaleDateString(i18n?.language),
        })
      : t('zoning.edit.accepted', 'Accepted')
    : t('zoning.edit.notAccepted', 'Proposed by the platform, not accepted');

  const trimmed = causeText.trim();

  return (
    <div className="fixed top-24 right-4 z-nkz-modal w-80 overflow-y-auto bg-nkz-surface rounded-nkz-md shadow-lg border border-nkz-border p-3 text-nkz-xs text-nkz-text-primary flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <span className="text-nkz-sm font-semibold">
          {levelName(zone.level)} {pctText}
        </span>
        <button
          type="button"
          onClick={onClose}
          className="text-nkz-text-muted hover:text-nkz-text-primary"
          aria-label={t('common.close', 'Close')}
        >
          ×
        </button>
      </div>

      {hasPending && (
        <button type="button" onClick={onOpenPending} className="block">
          <Badge intent="info">{t('zoning.pending.badge', 'New proposal')}</Badge>
        </button>
      )}

      <div className="flex flex-col gap-1">
        {zone.area != null && <p>{zone.area} ha</p>}
        <p>{t('zoning.relativeVigour', 'Vigour {{pct}} vs parcel mean', { pct: pctText })}</p>
        {zone.total != null && (
          <p>{t('zoning.confirmedDates', 'Consistent with its level on {{n}} of {{total}} dates', {
            n: zone.confirming, total: zone.total,
          })}</p>
        )}
        {zone.level === 'critical' && (
          <p className="font-semibold text-nkz-warning">{t('zoning.reviewInField', 'Review in the field')}</p>
        )}
        {zone.cause && (
          <p>
            <span className="text-nkz-text-muted">{t('zoning.edit.cause', 'Cause')}: </span>
            {zone.cause}
          </p>
        )}
        <p className="text-nkz-text-muted">{acceptedText}</p>
      </div>

      {error && <p className="text-nkz-danger">{error}</p>}

      {canEdit && mode === 'view' && (
        <div className="flex flex-wrap gap-1">
          {!zone.accepted && (
            <Button size="sm" variant="primary" loading={busy} disabled={busy}
              onClick={() => run(() => api.acceptZoning(parcelId, zone.runId), false)}>
              {t('zoning.edit.accept', 'Accept zoning')}
            </Button>
          )}
          <Button size="sm" variant="secondary" disabled={busy}
            onClick={() => { setCauseText(zone.cause ?? ''); setMode('note'); }}>
            {zone.cause ? t('zoning.edit.editCause', 'Edit cause') : t('zoning.edit.addCause', 'Note cause')}
          </Button>
          {zone.cause && (
            <Button size="sm" variant="ghost" disabled={busy}
              onClick={() => run(() => api.removeZoneNote(parcelId, zone.entityId), false)}>
              {t('zoning.edit.removeCause', 'Remove cause')}
            </Button>
          )}
          {others.length > 0 && (
            <Button size="sm" variant="secondary" disabled={busy}
              onClick={() => { setTargetRank(null); setMode('merge'); }}>
              {t('zoning.edit.mergeWith', 'Merge with…')}
            </Button>
          )}
        </div>
      )}

      {canEdit && mode === 'note' && (
        <div className="flex flex-col gap-1">
          <Textarea
            value={causeText}
            maxLength={MAX_CAUSE}
            rows={3}
            onChange={(e) => setCauseText(e.target.value)}
            placeholder={t('zoning.edit.causePlaceholder', 'E.g. waterlogging in spring')}
          />
          <div className="flex gap-1">
            <Button size="sm" variant="primary" loading={busy} disabled={busy || trimmed.length === 0}
              onClick={() => run(() => api.noteZone(parcelId, zone.entityId, trimmed), false)}>
              {t('zoning.edit.save', 'Save')}
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setMode('view')}>
              {t('zoning.edit.cancel', 'Cancel')}
            </Button>
          </div>
        </div>
      )}

      {canEdit && mode === 'merge' && other && (
        <div className="flex flex-col gap-1">
          <p className="text-nkz-text-muted">{t('zoning.edit.mergeOther', 'Level to merge with')}</p>
          <div className="flex flex-wrap gap-1">
            {others.map((l) => (
              <Button key={l.rank} size="sm" variant={l.rank === otherRank ? 'primary' : 'ghost'}
                onClick={() => { setOtherRank(l.rank); setTargetRank(null); }}>
                {levelName(l.level)}
              </Button>
            ))}
          </div>
          <p className="text-nkz-text-muted">{t('zoning.edit.mergeResult', 'Resulting level')}</p>
          <div className="flex flex-wrap gap-1">
            {[self, other].map((l) => (
              <Button key={l.rank} size="sm" variant={l.rank === chosenTarget ? 'primary' : 'ghost'}
                onClick={() => setTargetRank(l.rank)}>
                {levelName(l.level)}
              </Button>
            ))}
          </div>
          <div className="flex gap-1">
            <Button size="sm" variant="primary" disabled={busy} onClick={() => setMode('confirmMerge')}>
              {t('zoning.edit.continue', 'Continue')}
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setMode('view')}>
              {t('zoning.edit.cancel', 'Cancel')}
            </Button>
          </div>
        </div>
      )}

      {canEdit && mode === 'confirmMerge' && other && (
        <div className="flex flex-col gap-1">
          <p>{t('zoning.edit.mergeConfirm',
            'A new zoning is created. FaST doses and GIS-routing prescriptions made on the current one stop applying.')}</p>
          <div className="flex gap-1">
            <Button size="sm" variant="danger" loading={busy} disabled={busy}
              onClick={() => run(() => api.mergeZoning(parcelId, zone.runId, [zone.rank, other.rank], chosenTarget), true)}>
              {t('zoning.edit.merge', 'Merge')}
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setMode('merge')}>
              {t('zoning.edit.cancel', 'Cancel')}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
};
