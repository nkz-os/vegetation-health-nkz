#!/bin/bash
set -e

# Configuration
REMOTE_HOST="g@109.123.252.120"
REMOTE_DIR="~/vegetation-deploy"
IMAGE_NAME="vegetation-prime-api"
TAG="v1.44"
FULL_IMAGE="${IMAGE_NAME}:${TAG}"
MODULE_PATH="/home/g/Documents/nekazari/nekazari-module-vegetation-health"

echo "=== Starting Local Deployment Strategy ==="

# 1. Build Backend Docker Image
echo "[1/6] Building Docker image locally (this may take time)..."
cd "${MODULE_PATH}"
# Check if docker is available
if ! command -v docker &> /dev/null; then
    echo "Error: docker command not found."
    exit 1
fi
docker build -t "${FULL_IMAGE}" -f backend/Dockerfile .

# 2. Save Image to File
echo "[2/6] Saving image to compressed archive..."
docker save "${FULL_IMAGE}" | gzip > "backend/image.tar.gz"

# 3. Build Frontend
echo "[3/6] Building Frontend..."
cd "${MODULE_PATH}"
if ! command -v npm &> /dev/null; then
    echo "Error: npm command not found."
    exit 1
fi
npm install && npm run build
# Create tar of dist
if [ -d "dist" ]; then
    tar -czf "frontend/dist.tar.gz" -C dist .
else
    echo "Error: dist directory not found after build."
    exit 1
fi

# 4. Transfer to Remote
echo "[4/6] Transferring artifacts to remote server..."
ssh "${REMOTE_HOST}" "mkdir -p ${REMOTE_DIR}/k8s"
scp "backend/image.tar.gz" "${REMOTE_HOST}:${REMOTE_DIR}/image.tar.gz"
scp "frontend/dist.tar.gz" "${REMOTE_HOST}:${REMOTE_DIR}/dist.tar.gz"
scp backend/k8s/*.yaml "${REMOTE_HOST}:${REMOTE_DIR}/k8s/"

# 5. Remote Execution (Import Image & Update DB)
echo "[5/6] Executing remote deployment setup..."
ssh "${REMOTE_HOST}" << EOF
    set -e
    echo "--> Importing image into K3s (sudo required)..."
    # Import image
    sudo k3s ctr images import ${REMOTE_DIR}/image.tar.gz

    echo "--> Updating Database Schema (DROP old table for geometry fix)..."
    # Find postgres pod
    PG_POD=\$(sudo kubectl get pods -n nekazari -l app=postgres -o jsonpath="{.items[0].metadata.name}")
    echo "Found Postgres Pod: \$PG_POD"
    
    # Drop table if exists (ignoring error if not)
    # Note: This is destructive but requested for the fix.
    sudo kubectl exec -n nekazari \$PG_POD -- psql -U nekazari -d nekazari_db -c "DROP TABLE IF EXISTS vegetation_subscriptions CASCADE;" || true
EOF

# 6. Apply Manifests
echo "[6/6] Applying Kubernetes manifests..."
ssh "${REMOTE_HOST}" << EOF
    set -e
    echo "--> Applying manifests and updating images..."
    
    # Apply Beat Deployment
    sudo kubectl apply -f ${REMOTE_DIR}/k8s/beat-deployment.yaml
    
    # Update API deployment image
    sudo kubectl set image deployment/vegetation-prime-api api=${FULL_IMAGE} -n nekazari
    
    # Update Worker deployment image
    sudo kubectl set image deployment/vegetation-prime-worker worker=${FULL_IMAGE} -n nekazari

    # Restart deployments
    sudo kubectl rollout restart deployment/vegetation-prime-api -n nekazari
    sudo kubectl rollout restart deployment/vegetation-prime-worker -n nekazari
    sudo kubectl rollout restart deployment/vegetation-prime-beat -n nekazari
    
    echo "--> Extracting Frontend..."
    mkdir -p ${REMOTE_DIR}/frontend-build
    tar -xzf ${REMOTE_DIR}/dist.tar.gz -C ${REMOTE_DIR}/frontend-build
    echo "Frontend artifacts ready at ${REMOTE_DIR}/frontend-build"
    echo "Move them to your web server root if needed."
EOF

echo "=== Deployment Complete ==="
echo "Backend: v1.44 running."
echo "Frontend: Built and uploaded."
