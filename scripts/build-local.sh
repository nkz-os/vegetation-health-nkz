#!/bin/bash
# Build Docker images locally for testing (without pushing)
# Useful for development and debugging

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

VERSION="${1:-local}"

echo -e "${GREEN}Building Docker images locally for testing${NC}"
echo -e "Tag: ${VERSION}"
echo ""

# Build Backend
echo -e "${GREEN}Building backend image...${NC}"
cd backend
docker build \
    -t "vegetation-prime-backend:${VERSION}" \
    -f Dockerfile \
    .

echo -e "${GREEN}Backend image built: vegetation-prime-backend:${VERSION}${NC}"
echo ""

# Build Frontend
echo -e "${GREEN}Building frontend image...${NC}"
cd ../frontend
docker build \
    -t "vegetation-prime-frontend:${VERSION}" \
    -f Dockerfile \
    --build-arg NODE_ENV=development \
    ..

echo -e "${GREEN}Frontend image built: vegetation-prime-frontend:${VERSION}${NC}"
echo ""

echo -e "${GREEN}All images built successfully!${NC}"
echo ""
echo "You can now test them locally:"
echo "  docker run -p 8000:8000 vegetation-prime-backend:${VERSION}"
echo "  docker run -p 80:80 vegetation-prime-frontend:${VERSION}"

cd ..



