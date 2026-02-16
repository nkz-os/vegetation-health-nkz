import React, { useState } from 'react';
import { createPortal } from 'react-dom';
import { Button } from '@nekazari/ui-kit';
import { Calendar, Layers, Activity, CheckCircle, ArrowRight, ArrowLeft, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

// Simple Modal component with fixed header/footer and scrollable content
const Modal: React.FC<{
    isOpen: boolean;
    onClose: () => void;
    title: string;
    size?: string;
    children: React.ReactNode;
    footer?: React.ReactNode;
}> = ({ isOpen, onClose, title, children, footer }) => {
    if (!isOpen) return null;

    try {
        return createPortal(
            <div className="fixed inset-0 z-[9999] flex items-center justify-center p-4">
                <div className="fixed inset-0 bg-black/50 backdrop-blur-sm" onClick={onClose} />
                <div className="relative bg-white rounded-lg shadow-xl max-w-2xl w-full flex flex-col max-h-[85vh] animate-in fade-in zoom-in-95 duration-200">
                    {/* Fixed Header */}
                    <div className="flex items-center justify-between p-4 border-b flex-shrink-0">
                        <h2 className="text-lg font-semibold">{title}</h2>
                        <button onClick={onClose} className="p-1 hover:bg-slate-100 rounded transition-colors">
                            <X className="w-5 h-5" />
                        </button>
                    </div>
                    {/* Scrollable Content */}
                    <div className="flex-1 overflow-y-auto">
                        {children}
                    </div>
                    {/* Fixed Footer */}
                    {footer && (
                        <div className="border-t border-slate-200 p-4 flex-shrink-0 bg-white rounded-b-lg">
                            {footer}
                        </div>
                    )}
                </div>
            </div>,
            document.body
        );
    } catch (e) {
        console.error('Portal creation failed:', e);
        return null;
    }
};

import { useVegetationApi } from '../../services/api';

interface SetupWizardProps {
    open: boolean;
    onClose: () => void;
    entityId: string;
    entityName?: string;
    geometry: any; // GeoJSON
    onComplete: () => void;
}

import { getEntityGeometry } from '../../utils/geometry';

export const SetupWizard: React.FC<SetupWizardProps> = ({
    open, onClose, entityId, entityName, geometry, onComplete
}) => {
    const { t } = useTranslation();
    const api = useVegetationApi();
    const [step, setStep] = useState(1);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [localGeometry, setLocalGeometry] = useState<any>(geometry);
    const [fetchingGeometry, setFetchingGeometry] = useState(false);

    const displayName = entityName || t('setup.stepParcel');

    // Fetch geometry if missing
    React.useEffect(() => {
        if (open && entityId && !geometry && !localGeometry) {
            setFetchingGeometry(true);
            api.getEntity(entityId)
                .then(entity => {
                    const geom = getEntityGeometry(entity);
                    if (geom) {
                        setLocalGeometry(geom);
                    } else {
                        setError(t('setup.geometryError'));
                    }
                })
                .catch(err => {
                    console.error("Error fetching entity:", err);
                    setError(t('setup.entityLoadError'));
                })
                .finally(() => setFetchingGeometry(false));
        } else if (geometry) {
            setLocalGeometry(geometry);
        }
    }, [entityId, geometry, open, api, t]);

    const [startDate, setStartDate] = useState<string>(() => {
        const date = new Date();
        date.setDate(date.getDate() - 21); // Default to 21 days ago for Sentinel-2 coverage
        return date.toISOString().split('T')[0];
    });

    // Calculate days from start date to today for validation
    const daysFromStartToToday = React.useMemo(() => {
        const start = new Date(startDate);
        const today = new Date();
        const diffTime = today.getTime() - start.getTime();
        return Math.ceil(diffTime / (1000 * 60 * 60 * 24));
    }, [startDate]);

    const isDateRangeTooShort = daysFromStartToToday < 14;

    const [selectedIndices, setSelectedIndices] = useState<string[]>(['NDVI', 'EVI']);
    const [frequency, setFrequency] = useState<'weekly' | 'daily' | 'biweekly'>('weekly');

    const availableIndices = [
        { id: 'NDVI', name: 'NDVI (Vigor General)', desc: t('calculations.formulaHelp') },
        { id: 'EVI', name: 'EVI (Alta Densidad)', desc: t('calculations.formulaHelp') },
        { id: 'GNDVI', name: 'GNDVI (Clorofila)', desc: t('calculations.formulaHelp') },
        { id: 'SAVI', name: 'SAVI (Suelo)', desc: t('calculations.formulaHelp') },
        { id: 'NDRE', name: 'NDRE (Borde Rojo)', desc: t('calculations.formulaHelp') },
    ];

    const handleToggleIndex = (id: string) => {
        setSelectedIndices(prev =>
            prev.includes(id)
                ? prev.filter(i => i !== id)
                : [...prev, id]
        );
    };

    const handleSubmit = async () => {
        if (!localGeometry) {
            setError(t('setup.missingGeometry'));
            return;
        }

        setLoading(true);
        setError(null);
        const payload = {
            entity_id: entityId,
            geometry: localGeometry,
            start_date: startDate,
            index_types: selectedIndices,
            frequency: frequency,
            is_active: true
        };

        console.log('[SetupWizard] Creating subscription with payload:', payload);

        try {
            await api.createSubscription(payload);
            onComplete();
            onClose();
        } catch (err: any) {
            console.error('[SetupWizard] Error creating subscription:', err);
            if (err.response && err.response.status === 422) {
                console.error('[SetupWizard] Validation Error Data:', err.response.data);
                setError(t('setup.validationError'));
            } else {
                setError(err.message || t('setup.saveError'));
            }
        } finally {
            setLoading(false);
        }
    };

    return (
        <Modal
            isOpen={open}
            onClose={onClose}
            title={t('setup.configureMonitoring', { name: displayName })}
            size="lg"
            footer={
                <div className="flex justify-between">
                    {step > 1 ? (
                        <Button variant="ghost" onClick={() => setStep(step - 1)} disabled={loading}>
                            <ArrowLeft className="w-4 h-4 mr-2" /> {t('setup.back')}
                        </Button>
                    ) : (
                        <div />
                    )}

                    {step < 3 ? (
                        <Button variant="primary" onClick={() => setStep(step + 1)}>
                            {t('setup.continue')} <ArrowRight className="w-4 h-4 ml-2" />
                        </Button>
                    ) : (
                        <Button variant="primary" onClick={handleSubmit} disabled={loading} className="bg-green-600 hover:bg-green-700">
                            {loading ? t('setup.activating') : t('setup.activateMonitoring')}
                        </Button>
                    )}
                </div>
            }
        >
            <div className="p-6">
                {/* Progress Indicators */}
                <div className="flex items-center justify-between mb-8 px-8">
                    <div className={`flex flex-col items-center ${step >= 1 ? 'text-green-600' : 'text-slate-400'}`}>
                        <div className={`w-8 h-8 rounded-full flex items-center justify-center mb-2 ${step >= 1 ? 'bg-green-100 font-bold' : 'bg-slate-100'}`}>1</div>
                        <span className="text-xs font-medium">{t('setup.stepParcel')}</span>
                    </div>
                    <div className={`h-1 flex-1 mx-4 ${step >= 2 ? 'bg-green-500' : 'bg-slate-200'}`} />
                    <div className={`flex flex-col items-center ${step >= 2 ? 'text-green-600' : 'text-slate-400'}`}>
                        <div className={`w-8 h-8 rounded-full flex items-center justify-center mb-2 ${step >= 2 ? 'bg-green-100 font-bold' : 'bg-slate-100'}`}>2</div>
                        <span className="text-xs font-medium">{t('setup.stepConfig')}</span>
                    </div>
                    <div className={`h-1 flex-1 mx-4 ${step >= 3 ? 'bg-green-500' : 'bg-slate-200'}`} />
                    <div className={`flex flex-col items-center ${step >= 3 ? 'text-green-600' : 'text-slate-400'}`}>
                        <div className={`w-8 h-8 rounded-full flex items-center justify-center mb-2 ${step >= 3 ? 'bg-green-100 font-bold' : 'bg-slate-100'}`}>3</div>
                        <span className="text-xs font-medium">{t('setup.stepConfirm')}</span>
                    </div>
                </div>

                {/* Step Content */}
                <div className="min-h-[300px]">
                    {step === 1 && (
                        <div className="text-center space-y-6">
                            <div className="w-16 h-16 bg-green-100 rounded-full flex items-center justify-center mx-auto text-green-600">
                                <Activity size={32} />
                            </div>
                            <div>
                                <h3 className="text-xl font-bold text-slate-800">{t('setup.activateAutoMonitoring')}</h3>
                                <p className="text-slate-500 mt-2 max-w-md mx-auto">
                                    {t('setup.autoMonitoringDesc')}
                                </p>
                            </div>
                            {fetchingGeometry && (
                                <div className="text-sm text-blue-600 animate-pulse">
                                    {t('setup.fetchingGeometry')}
                                </div>
                            )}
                            {error && step === 1 && (
                                <div className="text-sm text-red-600 bg-red-50 p-2 rounded">
                                    {error}
                                </div>
                            )}
                            <div className="bg-slate-50 p-4 rounded-lg inline-block text-left border border-slate-200">
                                <p className="text-sm font-semibold text-slate-700">{t('setup.selectedParcel')}</p>
                                <p className="text-slate-600">{displayName}</p>
                                <p className="text-xs text-slate-400 mt-1">ID: {entityId}</p>
                            </div>
                        </div>
                    )}

                    {step === 2 && (
                        <div className="space-y-6">
                            {/* Date Selection */}
                            <div>
                                <label className="block text-sm font-medium text-slate-700 mb-2">
                                    <Calendar className="inline w-4 h-4 mr-1" /> {t('setup.startDateLabel')}
                                </label>
                                <input
                                    type="date"
                                    value={startDate}
                                    onChange={(e) => setStartDate(e.target.value)}
                                    max={new Date().toISOString().split('T')[0]}
                                    className={`w-full rounded-md shadow-sm focus:border-green-500 focus:ring-green-500 ${isDateRangeTooShort ? 'border-amber-400' : 'border-slate-300'}`}
                                />
                                <p className="text-xs text-slate-500 mt-1">
                                    {t('setup.startDateHelp')}
                                </p>
                                {isDateRangeTooShort && (
                                    <div className="mt-2 p-2 bg-amber-50 border border-amber-200 rounded text-xs text-amber-700">
                                        ⚠️ <strong>{t('setup.shortRange', { days: daysFromStartToToday })}</strong>: {t('setup.shortRangeWarning')}
                                    </div>
                                )}
                            </div>

                            {/* Index Selection */}
                            <div>
                                <label className="block text-sm font-medium text-slate-700 mb-2">
                                    <Layers className="inline w-4 h-4 mr-1" /> {t('setup.indicesLabel')}
                                </label>
                                <div className="space-y-2 max-h-48 overflow-y-auto border border-slate-200 rounded-md p-2">
                                    {availableIndices.map(idx => (
                                        <div key={idx.id} className="flex items-start p-2 hover:bg-slate-50 rounded cursor-pointer" onClick={() => handleToggleIndex(idx.id)}>
                                            <input
                                                type="checkbox"
                                                checked={selectedIndices.includes(idx.id)}
                                                onChange={() => { }}
                                                className="mt-1 h-4 w-4 text-green-600 focus:ring-green-500 border-gray-300 rounded"
                                            />
                                            <div className="ml-3">
                                                <span className="block text-sm font-medium text-slate-700">{idx.name}</span>
                                                <span className="block text-xs text-slate-500">{idx.desc}</span>
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>

                            {/* Frequency */}
                            <div>
                                <label className="block text-sm font-medium text-slate-700 mb-2">{t('setup.frequencyLabel')}</label>
                                <div className="flex gap-4">
                                    <label className="flex items-center">
                                        <input
                                            type="radio"
                                            name="frequency"
                                            value="weekly"
                                            checked={frequency === 'weekly'}
                                            onChange={() => setFrequency('weekly')}
                                            className="focus:ring-green-500 h-4 w-4 text-green-600 border-gray-300"
                                        />
                                        <span className="ml-2 text-sm text-slate-700">{t('setup.weeklyRecommended')}</span>
                                    </label>
                                    <label className="flex items-center">
                                        <input
                                            type="radio"
                                            name="frequency"
                                            value="daily"
                                            checked={frequency === 'daily'}
                                            onChange={() => setFrequency('daily')}
                                            className="focus:ring-green-500 h-4 w-4 text-green-600 border-gray-300"
                                        />
                                        <span className="ml-2 text-sm text-slate-700">{t('setup.dailyCredits')}</span>
                                    </label>
                                </div>
                            </div>
                        </div>
                    )}

                    {step === 3 && (
                        <div className="space-y-6">
                            <div className="bg-green-50 border border-green-200 rounded-lg p-4">
                                <h4 className="font-semibold text-green-800 flex items-center">
                                    <CheckCircle className="w-5 h-5 mr-2" /> {t('setup.summaryTitle')}
                                </h4>
                                <ul className="mt-2 space-y-1 text-sm text-green-700">
                                    <li>• <strong>{t('setup.summaryParcel')}</strong> {displayName}</li>
                                    <li>• <strong>{t('setup.summaryStartDate')}</strong> {startDate}</li>
                                    <li>• <strong>{t('setup.summaryIndices')}</strong> {selectedIndices.join(', ')}</li>
                                    <li>• <strong>{t('setup.summaryFrequency')}</strong> {frequency === 'weekly' ? t('setup.summaryWeekly') : t('setup.summaryDaily')}</li>
                                </ul>
                            </div>

                            <div className="text-sm text-slate-600">
                                <p>{t('setup.activateDesc')}</p>
                                <ol className="list-decimal list-inside mt-2 space-y-1 ml-2">
                                    <li>{t('setup.activateStep1')}</li>
                                    <li>{t('setup.activateStep2')}</li>
                                    <li>{t('setup.activateStep3')}</li>
                                </ol>
                                <p className="mt-4 text-xs text-slate-500 italic">
                                    {t('setup.activateNote')}
                                </p>
                            </div>

                            {error && (
                                <div className="bg-red-50 border border-red-200 text-red-700 p-3 rounded text-sm">
                                    {t('common.error')}: {error}
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </div>
        </Modal>
    );
};
