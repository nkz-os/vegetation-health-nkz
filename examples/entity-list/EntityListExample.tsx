// =============================================================================
// Entity List Example
// =============================================================================
// Example module showing how to fetch and display entities from the platform.
// This demonstrates:
// - Using NKZClient to fetch data
// - Error handling
// - Loading states
// - Displaying entity data in a list

import React, { useState, useEffect } from 'react';
import { NKZClient, useAuth, useTranslation } from '@nekazari/sdk';
import { Card, Button } from '@nekazari/ui-kit';
import { RefreshCw, AlertCircle, Package } from 'lucide-react';

interface Entity {
  id: string;
  type: string;
  name?: string;
  [key: string]: any;
}

const EntityListExample: React.FC = () => {
  const { getToken, tenantId } = useAuth();
  const { t } = useTranslation('common');
  const [entities, setEntities] = useState<Entity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchEntities = async () => {
    setLoading(true);
    setError(null);

    try {
      const client = new NKZClient({
        baseUrl: '/api',
        getToken: getToken,
        getTenantId: () => tenantId,
      });

      const data = await client.get<Entity[]>('/entities');
      setEntities(Array.isArray(data) ? data : []);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to fetch entities';
      setError(errorMessage);
      console.error('Error fetching entities:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchEntities();
  }, [getToken, tenantId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="text-center">
          <RefreshCw className="w-8 h-8 animate-spin text-blue-600 mx-auto mb-4" />
          <p className="text-gray-600">Loading entities...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-6">
        <Card padding="lg" className="border-red-200 bg-red-50">
          <div className="flex items-start gap-3">
            <AlertCircle className="w-6 h-6 text-red-600 flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <h3 className="text-lg font-semibold text-red-900 mb-2">Error Loading Entities</h3>
              <p className="text-red-700 mb-4">{error}</p>
              <Button variant="primary" onClick={fetchEntities}>
                <RefreshCw className="w-4 h-4 mr-2" />
                Retry
              </Button>
            </div>
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="max-w-7xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-3xl font-bold text-gray-900 mb-2">Entities</h1>
            <p className="text-gray-600">
              Found {entities.length} {entities.length === 1 ? 'entity' : 'entities'}
            </p>
          </div>
          <Button variant="primary" onClick={fetchEntities}>
            <RefreshCw className="w-4 h-4 mr-2" />
            Refresh
          </Button>
        </div>

        {entities.length === 0 ? (
          <Card padding="lg" className="text-center">
            <Package className="w-12 h-12 text-gray-400 mx-auto mb-4" />
            <h3 className="text-lg font-semibold text-gray-900 mb-2">No Entities Found</h3>
            <p className="text-gray-600">
              There are no entities in your tenant. Create some entities to see them here.
            </p>
          </Card>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {entities.map((entity) => (
              <Card key={entity.id} padding="md" className="hover:shadow-lg transition-shadow">
                <div className="space-y-2">
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <h3 className="font-semibold text-gray-900">
                        {entity.name || entity.id.split(':').pop()}
                      </h3>
                      <p className="text-sm text-gray-500 mt-1">{entity.type}</p>
                    </div>
                  </div>
                  <div className="pt-2 border-t border-gray-200">
                    <p className="text-xs text-gray-500 font-mono truncate">
                      {entity.id}
                    </p>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default EntityListExample;

