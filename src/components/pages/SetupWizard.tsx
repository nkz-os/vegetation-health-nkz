import React, { useState } from 'react';
import { Card, Button } from '@nekazari/ui-kit';
import { Calendar, Layers, Activity, CheckCircle, ArrowRight, ArrowLeft, X } from 'lucide-react';

// Simple Modal component (ui-kit doesn't export Modal)
const Modal: React.FC<{
  isOpen: boolean;
  onClose: () => void;
  title: string;
  size?: string;
  children: React.ReactNode;
}> = ({ isOpen, onClose, title, children }) => {
  if (!isOpen) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="fixed inset-0 bg-black/50" onClick={onClose} />
      <div className="relative bg-white rounded-lg shadow-xl max-w-2xl w-full mx-4 max-h-[90vh] overflow-auto">
        <div className="flex items-center justify-between p-4 border-b">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button onClick={onClose} className="p-1 hover:bg-slate-100 rounded">
            <X className="w-5 h-5" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
};

// Simple Checkbox component (ui-kit doesn't export Checkbox)
const Checkbox: React.FC<{
  checked: boolean;
  onChange: (checked: boolean) => void;
  label?: string;
  className?: string;
}> = ({ checked, onChange, label, className }) => (
  <label className={`flex items-center gap-2 cursor-pointer ${className || ''}`}>
    <input
      type="checkbox"
      checked={checked}
      onChange={(e) => onChange(e.target.checked)}
      className="w-4 h-4 rounded border-slate-300 text-green-600 focus:ring-green-500"
    />
    {label && <span>{label}</span>}
  </label>
);
import { useVegetationApi } from '../../services/api';

interface SetupWizardProps {
    open: boolean;
    onClose: () => void;
    entityId: string;
    entityName?: string;
    geometry: any; // GeoJSON
    onComplete: () => void;
}

export const SetupWizard: React.FC<SetupWizardProps> = ({
    open, onClose, entityId, entityName = 'Parcela seleccionada', geometry, onComplete
}) => {
    const api = useVegetationApi();
    const [step, setStep] = useState(1);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Configuration state
    const [startDate, setStartDate] = useState<string>(() => {
        const date = new Date();
        date.setFullYear(date.getFullYear() - 1); // Default to 1 year ago
        return date.toISOString().split('T')[0];
    });

    const [selectedIndices, setSelectedIndices] = useState<string[]>(['NDVI', 'EVI']);
    const [frequency, setFrequency] = useState<'weekly' | 'daily' | 'biweekly'>('weekly');

    const availableIndices = [
        { id: 'NDVI', name: 'NDVI (Vigor General)', desc: 'Índice de Diferencia Normalizada de Vegetación. Estándar para salud vegetal.' },
        { id: 'EVI', name: 'EVI (Alta Densidad)', desc: 'Índice de Vegetación Mejorado. Mejor para zonas con mucha biomasa.' },
        { id: 'GNDVI', name: 'GNDVI (Clorofila)', desc: 'Sensible al contenido de clorofila y estrés hídrico.' },
        { id: 'SAVI', name: 'SAVI (Suelo)', desc: 'Ajustado al suelo. Recomendado para etapas tempranas de cultivo.' },
        { id: 'NDRE', name: 'NDRE (Borde Rojo)', desc: 'Usa banda Red Edge. Útil para cultivos permanentes y etapas finales.' },
    ];

    const handleToggleIndex = (id: string) => {
        setSelectedIndices(prev =>
            prev.includes(id)
                ? prev.filter(i => i !== id)
                : [...prev, id]
        );
    };

    const handleSubmit = async () => {
        setLoading(true);
        setError(null);
        try {
            await api.createSubscription({
                entity_id: entityId,
                geometry: geometry, // Important: Pass geometry for scene search
                start_date: startDate,
                index_types: selectedIndices,
                frequency: frequency,
                is_active: true
            });
            onComplete();
            onClose();
        } catch (err: any) {
            console.error(err);
            setError(err.message || 'Error al guardar la configuración');
        } finally {
            setLoading(false);
        }
    };

    return (
        <Modal
            isOpen={open}
            onClose={onClose}
            title={`Configurar Monitoreo: ${entityName}`}
            size="lg"
        >
            <div className="p-6">
                {/* Progress Indicators */}
                <div className="flex items-center justify-between mb-8 px-8">
                    <div className={`flex flex-col items-center ${step >= 1 ? 'text-green-600' : 'text-slate-400'}`}>
                        <div className={`w-8 h-8 rounded-full flex items-center justify-center mb-2 ${step >= 1 ? 'bg-green-100 font-bold' : 'bg-slate-100'}`}>1</div>
                        <span className="text-xs font-medium">Parcela</span>
                    </div>
                    <div className={`h-1 flex-1 mx-4 ${step >= 2 ? 'bg-green-500' : 'bg-slate-200'}`} />
                    <div className={`flex flex-col items-center ${step >= 2 ? 'text-green-600' : 'text-slate-400'}`}>
                        <div className={`w-8 h-8 rounded-full flex items-center justify-center mb-2 ${step >= 2 ? 'bg-green-100 font-bold' : 'bg-slate-100'}`}>2</div>
                        <span className="text-xs font-medium">Configuración</span>
                    </div>
                    <div className={`h-1 flex-1 mx-4 ${step >= 3 ? 'bg-green-500' : 'bg-slate-200'}`} />
                    <div className={`flex flex-col items-center ${step >= 3 ? 'text-green-600' : 'text-slate-400'}`}>
                        <div className={`w-8 h-8 rounded-full flex items-center justify-center mb-2 ${step >= 3 ? 'bg-green-100 font-bold' : 'bg-slate-100'}`}>3</div>
                        <span className="text-xs font-medium">Confirmar</span>
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
                                <h3 className="text-xl font-bold text-slate-800">Activar Monitoreo Automático</h3>
                                <p className="text-slate-500 mt-2 max-w-md mx-auto">
                                    Vamos a configurar la descarga automática de imágenes satelitales y el cálculo semanal de índices de vegetación para esta parcela.
                                </p>
                            </div>
                            <div className="bg-slate-50 p-4 rounded-lg inline-block text-left border border-slate-200">
                                <p className="text-sm font-semibold text-slate-700">Parcela seleccionada:</p>
                                <p className="text-slate-600">{entityName}</p>
                                <p className="text-xs text-slate-400 mt-1">ID: {entityId}</p>
                            </div>
                        </div>
                    )}

                    {step === 2 && (
                        <div className="space-y-6">
                            {/* Date Selection */}
                            <div>
                                <label className="block text-sm font-medium text-slate-700 mb-2">
                                    <Calendar className="inline w-4 h-4 mr-1" /> Fecha de inicio de datos históricos
                                </label>
                                <input
                                    type="date"
                                    value={startDate}
                                    onChange={(e) => setStartDate(e.target.value)}
                                    className="w-full border-slate-300 rounded-md shadow-sm focus:border-green-500 focus:ring-green-500"
                                />
                                <p className="text-xs text-slate-500 mt-1">Descargaremos imágenes disponibles desde esta fecha.</p>
                            </div>

                            {/* Index Selection */}
                            <div>
                                <label className="block text-sm font-medium text-slate-700 mb-2">
                                    <Layers className="inline w-4 h-4 mr-1" /> Índices a calcular automáticamente
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
                                <label className="block text-sm font-medium text-slate-700 mb-2">Frecuencia de actualización</label>
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
                                        <span className="ml-2 text-sm text-slate-700">Semanal (Recomendado)</span>
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
                                        <span className="ml-2 text-sm text-slate-700">Diaria (Consume más créditos)</span>
                                    </label>
                                </div>
                            </div>
                        </div>
                    )}

                    {step === 3 && (
                        <div className="space-y-6">
                            <div className="bg-green-50 border border-green-200 rounded-lg p-4">
                                <h4 className="font-semibold text-green-800 flex items-center">
                                    <CheckCircle className="w-5 h-5 mr-2" /> Resumen de Configuración
                                </h4>
                                <ul className="mt-2 space-y-1 text-sm text-green-700">
                                    <li>• <strong>Parcela:</strong> {entityName}</li>
                                    <li>• <strong>Inicio Histórico:</strong> {startDate}</li>
                                    <li>• <strong>Índices:</strong> {selectedIndices.join(', ')}</li>
                                    <li>• <strong>Frecuencia:</strong> {frequency === 'weekly' ? 'Semanal' : 'Diaria'}</li>
                                </ul>
                            </div>

                            <div className="text-sm text-slate-600">
                                <p>Al hacer clic en "Activar", el sistema comenzará a:</p>
                                <ol className="list-decimal list-inside mt-2 space-y-1 ml-2">
                                    <li>Buscar imágenes satelitales históricas disponibles.</li>
                                    <li>Calcular los índices seleccionados.</li>
                                    <li>Programar las próximas actualizaciones automáticas.</li>
                                </ol>
                                <p className="mt-4 text-xs text-slate-500 italic">
                                    Nota: El proceso inicial puede tomar unos minutos dependiendo del rango de fechas seleccionado. Recibirás una notificación cuando los datos estén listos.
                                </p>
                            </div>

                            {error && (
                                <div className="bg-red-50 border border-red-200 text-red-700 p-3 rounded text-sm">
                                    Error: {error}
                                </div>
                            )}
                        </div>
                    )}
                </div>

                {/* Footer Buttons */}
                <div className="mt-8 flex justify-between border-t border-slate-100 pt-4">
                    {step > 1 ? (
                        <Button variant="ghost" onClick={() => setStep(step - 1)} disabled={loading}>
                            <ArrowLeft className="w-4 h-4 mr-2" /> Atrás
                        </Button>
                    ) : (
                        <div /> // Spacer
                    )}

                    {step < 3 ? (
                        <Button variant="primary" onClick={() => setStep(step + 1)}>
                            Continuar <ArrowRight className="w-4 h-4 ml-2" />
                        </Button>
                    ) : (
                        <Button variant="primary" onClick={handleSubmit} disabled={loading} className="bg-green-600 hover:bg-green-700">
                            {loading ? 'Activando...' : 'Activar Monitoreo'}
                        </Button>
                    )}
                </div>
            </div>
        </Modal>
    );
};
