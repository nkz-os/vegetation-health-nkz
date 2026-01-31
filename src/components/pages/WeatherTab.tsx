/**
 * Weather Tab - Ferrari Frontend
 * 
 * Show weather and interpretation for the selected parcel:
 * - Summary card (GDD, water balance, frost/heat days)
 * - Interpretation text
 * - List of soil sensors
 */

import React, { useState, useEffect } from 'react';
import { useVegetationContext } from '../../services/vegetationContext';
import { useVegetationApi } from '../../services/api';
import { WeatherData, WeatherInterpretation, WeatherSensor } from '../../types';
import { Cloud, Droplets, Thermometer, Wind, AlertCircle, Radio } from 'lucide-react';

const WeatherTab: React.FC = () => {
  const { selectedEntityId } = useVegetationContext();
  const api = useVegetationApi();
  
  const [weather, setWeather] = useState<WeatherData | null>(null);
  const [interpretation, setInterpretation] = useState<WeatherInterpretation | null>(null);
  const [sensors, setSensors] = useState<WeatherSensor[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedEntityId) {
      setLoading(false);
      return;
    }

    const fetchWeatherData = async () => {
      setLoading(true);
      setError(null);

      try {
        const [weatherData, interpretData, sensorsData] = await Promise.allSettled([
          api.getWeather(selectedEntityId),
          api.getWeatherInterpretation(selectedEntityId),
          api.getWeatherSensors(selectedEntityId)
        ]);

        if (weatherData.status === 'fulfilled') {
          setWeather(weatherData.value);
        }
        if (interpretData.status === 'fulfilled') {
          setInterpretation(interpretData.value);
        }
        if (sensorsData.status === 'fulfilled') {
          setSensors(sensorsData.value);
        }

        // If all failed, show error
        if (weatherData.status === 'rejected' && 
            interpretData.status === 'rejected' && 
            sensorsData.status === 'rejected') {
          setError('No se pudieron cargar los datos meteorológicos');
        }
      } catch (err) {
        console.error('Weather fetch error:', err);
        setError('Error al cargar datos meteorológicos');
      } finally {
        setLoading(false);
      }
    };

    fetchWeatherData();
  }, [selectedEntityId, api]);

  if (!selectedEntityId) {
    return (
      <div className="p-6 text-center">
        <Cloud className="w-12 h-12 text-slate-300 mx-auto mb-3" />
        <div className="text-slate-400 text-lg mb-2">Selecciona una parcela</div>
        <p className="text-slate-500 text-sm">
          Vuelve al listado y selecciona una parcela para ver información meteorológica.
        </p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="p-6 flex items-center justify-center">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-emerald-600"></div>
        <span className="ml-3 text-slate-500">Cargando datos meteorológicos...</span>
      </div>
    );
  }

  if (error && !weather) {
    return (
      <div className="p-6">
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
          <div>
            <h3 className="font-medium text-red-800">Error al cargar datos</h3>
            <p className="text-sm text-red-700 mt-1">{error}</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 max-w-4xl mx-auto">
      <header className="mb-6">
        <h1 className="text-xl font-bold text-slate-900 flex items-center gap-2">
          <Cloud className="w-6 h-6 text-sky-500" />
          Condiciones Meteorológicas
        </h1>
        <p className="text-slate-600 text-sm mt-1">
          Resumen climático y su impacto en la vegetación de la parcela seleccionada.
        </p>
      </header>

      {/* Weather Summary Cards */}
      {weather && (
        <section className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          {/* GDD */}
          <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-4">
            <div className="flex items-center gap-2 mb-2">
              <Thermometer className="w-5 h-5 text-orange-500" />
              <span className="text-sm font-medium text-slate-600">GDD</span>
            </div>
            <div className="text-2xl font-bold text-slate-900">{weather.gdd?.toFixed(0) || '-'}</div>
            <div className="text-xs text-slate-500">Grados día acumulados</div>
          </div>

          {/* Water Balance */}
          <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-4">
            <div className="flex items-center gap-2 mb-2">
              <Droplets className="w-5 h-5 text-blue-500" />
              <span className="text-sm font-medium text-slate-600">Balance Hídrico</span>
            </div>
            <div className="text-2xl font-bold text-slate-900">{weather.water_balance?.toFixed(1) || '-'} mm</div>
            <div className="text-xs text-slate-500">Precipitación - ET</div>
          </div>

          {/* Frost Days */}
          <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-4">
            <div className="flex items-center gap-2 mb-2">
              <Wind className="w-5 h-5 text-cyan-500" />
              <span className="text-sm font-medium text-slate-600">Días Helada</span>
            </div>
            <div className="text-2xl font-bold text-slate-900">{weather.frost_days || 0}</div>
            <div className="text-xs text-slate-500">T° mín &lt; 0°C</div>
          </div>

          {/* Heat Days */}
          <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-4">
            <div className="flex items-center gap-2 mb-2">
              <Thermometer className="w-5 h-5 text-red-500" />
              <span className="text-sm font-medium text-slate-600">Días Calor</span>
            </div>
            <div className="text-2xl font-bold text-slate-900">{weather.heat_days || 0}</div>
            <div className="text-xs text-slate-500">T° máx &gt; 30°C</div>
          </div>
        </section>
      )}

      {/* Additional Stats */}
      {weather && (
        <section className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 mb-6">
          <h2 className="text-lg font-semibold text-slate-800 mb-4">Datos del Periodo</h2>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
            <div>
              <div className="text-sm text-slate-500">Precipitación Total</div>
              <div className="text-lg font-semibold text-slate-800">{weather.precipitation_total?.toFixed(1) || '-'} mm</div>
            </div>
            <div>
              <div className="text-sm text-slate-500">Evapotranspiración</div>
              <div className="text-lg font-semibold text-slate-800">{weather.evapotranspiration?.toFixed(1) || '-'} mm</div>
            </div>
            <div>
              <div className="text-sm text-slate-500">Periodo</div>
              <div className="text-lg font-semibold text-slate-800">
                {weather.period?.start && weather.period?.end 
                  ? `${new Date(weather.period.start).toLocaleDateString()} - ${new Date(weather.period.end).toLocaleDateString()}`
                  : 'Últimos 30 días'}
              </div>
            </div>
          </div>
        </section>
      )}

      {/* Interpretation */}
      {interpretation && (
        <section className="bg-white rounded-xl shadow-sm border border-slate-200 p-6 mb-6">
          <h2 className="text-lg font-semibold text-slate-800 mb-4 flex items-center gap-2">
            Interpretación
            <span className={`px-2 py-0.5 rounded text-xs font-medium ${
              interpretation.impact_on_vegetation === 'positive' ? 'bg-emerald-100 text-emerald-800' :
              interpretation.impact_on_vegetation === 'negative' ? 'bg-red-100 text-red-800' :
              'bg-slate-100 text-slate-800'
            }`}>
              {interpretation.impact_on_vegetation === 'positive' ? 'Impacto positivo' :
               interpretation.impact_on_vegetation === 'negative' ? 'Impacto negativo' :
               'Impacto neutro'}
            </span>
          </h2>
          
          <p className="text-slate-700 mb-4">{interpretation.interpretation}</p>
          
          {interpretation.recommendations && interpretation.recommendations.length > 0 && (
            <div>
              <h3 className="text-sm font-medium text-slate-600 mb-2">Recomendaciones:</h3>
              <ul className="space-y-1">
                {interpretation.recommendations.map((rec, idx) => (
                  <li key={idx} className="text-sm text-slate-600 flex items-start gap-2">
                    <span className="text-emerald-500 mt-1">•</span>
                    {rec}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      )}

      {/* Sensors */}
      <section className="bg-white rounded-xl shadow-sm border border-slate-200 p-6">
        <h2 className="text-lg font-semibold text-slate-800 mb-4 flex items-center gap-2">
          <Radio className="w-5 h-5 text-emerald-600" />
          Sensores de Suelo
        </h2>
        
        {sensors.length > 0 ? (
          <div className="space-y-3">
            {sensors.map(sensor => (
              <div 
                key={sensor.id} 
                className="flex items-center justify-between p-3 bg-slate-50 rounded-lg border border-slate-100"
              >
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 bg-emerald-100 rounded-lg flex items-center justify-center">
                    <Radio className="w-5 h-5 text-emerald-600" />
                  </div>
                  <div>
                    <div className="font-medium text-slate-800">{sensor.name}</div>
                    <div className="text-xs text-slate-500">{sensor.type}</div>
                  </div>
                </div>
                {sensor.last_reading && (
                  <div className="text-right">
                    <div className="font-semibold text-slate-800">
                      {sensor.last_reading.value} {sensor.last_reading.unit}
                    </div>
                    <div className="text-xs text-slate-500">
                      {new Date(sensor.last_reading.timestamp).toLocaleString()}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center py-8 text-slate-400">
            <Radio className="w-8 h-8 mx-auto mb-2 opacity-50" />
            <p>No hay sensores de suelo configurados para esta parcela</p>
          </div>
        )}
      </section>
    </div>
  );
};

export default WeatherTab;
