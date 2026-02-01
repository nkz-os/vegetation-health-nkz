# Multi-stage build for Vegetation Prime Frontend
# Stage 1: Build with Vite
FROM node:20-alpine AS builder

WORKDIR /app

# Copy package files
COPY package.json package-lock.json* ./

# Temporarily update package.json to use npm SDK instead of local path
# Remove package-lock.json to force regeneration with npm SDK
RUN sed -i 's|"file:../nekazari-public/packages/sdk"|"^1.0.3"|g' package.json && \
    sed -i 's|"@nekazari/sdk": "@nekazari/sdk@|"@nekazari/sdk": "|g' package.json && \
    rm -f package-lock.json && \
    echo "=== Updated package.json ===" && \
    grep "@nekazari/sdk" package.json && \
    echo "=== Installing dependencies ===" && \
    npm install --registry=https://registry.npmjs.org/ && \
    echo "=== Verifying SDK installation ===" && \
    npm list @nekazari/sdk 2>&1 && \
    ls -la node_modules/@nekazari/sdk/ 2>&1 && \
    test -d node_modules/@nekazari/sdk && echo "✓ SDK directory exists" || (echo "ERROR: SDK directory not found" && exit 1) && \
    test -f node_modules/@nekazari/sdk/dist/index.js && echo "✓ SDK dist/index.js exists" || (echo "ERROR: SDK dist/index.js not found" && exit 1)

# Copy all necessary files for build
COPY src/ ./src/
COPY index.html ./
COPY vite.config.ts ./
COPY tsconfig.json ./
COPY tsconfig.node.json ./
COPY tailwind.config.js ./
COPY postcss.config.js ./
COPY manifest.json ./

# Build application
RUN npm run build

# Stage 2: Serve with Nginx
FROM nginx:alpine

# Copy built assets
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy manifest.json for module registration
COPY --from=builder /app/manifest.json /usr/share/nginx/html/manifest.json

# Copy custom nginx config (with CORS and module path support)
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

# Expose port
EXPOSE 80

# Start nginx
CMD ["nginx", "-g", "daemon off;"]
