#!/bin/bash
# Cleanup existing vegetation data + apply migration
# Run on server after deploy

set -e

TENANTS="montiko asociacion-allotarra platform"
PGPOD=$(sudo kubectl get pods -n nekazari -l app=timescaledb -o jsonpath='{.items[0].metadata.name}')

echo "=== 1. Deleting all VegetationJob and VegetationScene records in PostgreSQL ==="
for tenant in $TENANTS; do
  echo "Cleaning tenant: $tenant"
  sudo kubectl exec -n nekazari "$PGPOD" -- psql -U postgres -d nekazari -c "
    DELETE FROM vegetation_jobs WHERE tenant_id='$tenant';
    DELETE FROM vegetation_scenes WHERE tenant_id='$tenant';
    DELETE FROM vegetation_index_cache WHERE tenant_id='$tenant';
    DELETE FROM vegetation_crop_seasons WHERE tenant_id='$tenant';
    DELETE FROM vegetation_subscriptions WHERE tenant_id='$tenant';
  "
done

echo ""
echo "=== 2. Deleting VegetationIndex entities from Orion-LD ==="
for tenant in $TENANTS; do
  echo "Querying Orion-LD for VegetationIndex entities in tenant $tenant..."
  ENTITIES=$(sudo kubectl exec -n nekazari deploy/orion-ld -c orion -- curl -s "http://localhost:1026/ngsi-ld/v1/entities?type=VegetationIndex&options=keyValues&limit=1000" -H "NGSILD-Tenant: $tenant" -H "Accept: application/json" | python3 -c "import sys,json; print('\n'.join(e['id'] for e in json.load(sys.stdin)) if sys.stdin.read(1) else '')" 2>/dev/null || true)
  for eid in $ENTITIES; do
    echo "  Deleting $eid from tenant $tenant"
    sudo kubectl exec -n nekazari deploy/orion-ld -c orion -- curl -s -X DELETE "http://localhost:1026/ngsi-ld/v1/entities/$eid" -H "NGSILD-Tenant: $tenant" > /dev/null 2>&1 || true
  done
done

echo ""
echo "=== 3. Deleting EOProduct entities from Orion-LD ==="
for tenant in $TENANTS; do
  ENTITIES=$(sudo kubectl exec -n nekazari deploy/orion-ld -c orion -- curl -s "http://localhost:1026/ngsi-ld/v1/entities?type=EOProduct&options=keyValues&limit=500" -H "NGSILD-Tenant: $tenant" -H "Accept: application/json" | python3 -c "import sys,json; data=json.load(sys.stdin); print('\n'.join(e['id'] for e in data))" 2>/dev/null || true)
  for eid in $ENTITIES; do
    echo "  Deleting $eid from tenant $tenant"
    sudo kubectl exec -n nekazari deploy/orion-ld -c orion -- curl -s -X DELETE "http://localhost:1026/ngsi-ld/v1/entities/$eid" -H "NGSILD-Tenant: $tenant" > /dev/null 2>&1 || true
  done
done

echo ""
echo "=== 4. Deleting AgriParcelRecord entities from Orion-LD ==="
for tenant in $TENANTS; do
  ENTITIES=$(sudo kubectl exec -n nekazari deploy/orion-ld -c orion -- curl -s "http://localhost:1026/ngsi-ld/v1/entities?type=AgriParcelRecord&options=keyValues&limit=500" -H "NGSILD-Tenant: $tenant" -H "Accept: application/json" | python3 -c "import sys,json; data=json.load(sys.stdin); print('\n'.join(e['id'] for e in data))" 2>/dev/null || true)
  for eid in $ENTITIES; do
    echo "  Deleting $eid from tenant $tenant"
    sudo kubectl exec -n nekazari deploy/orion-ld -c orion -- curl -s -X DELETE "http://localhost:1026/ngsi-ld/v1/entities/$eid" -H "NGSILD-Tenant: $tenant" > /dev/null 2>&1 || true
  done
done

echo ""
echo "=== 5. Cleaning MinIO rasters ==="
sudo kubectl exec -n nekazari deploy/minio -- mc rm --recursive --force internal/vegetation-prime/ 2>/dev/null || true

echo ""
echo "=== 6. Running migration 009_drop_crop_type ==="
sudo kubectl cp backend/migrations/009_drop_crop_type.sql nekazari/"$PGPOD":/tmp/009_drop_crop_type.sql
sudo kubectl exec -n nekazari "$PGPOD" -- psql -U postgres -d nekazari -f /tmp/009_drop_crop_type.sql

echo ""
echo "=== 7. Rolling out new vegetation-prime images ==="
sudo kubectl apply -f k8s/backend-deployment.yaml
sudo kubectl apply -f k8s/worker-deployment.yaml
sudo kubectl apply -f k8s/beat-deployment.yaml

echo ""
echo "=== Done! ==="
