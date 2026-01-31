FROM nginx:alpine

# Remove default nginx static assets
RUN rm -rf /usr/share/nginx/html/*

# Copy static assets from builder stage (assuming dist is built locally)
COPY dist /usr/share/nginx/html
COPY manifest.json /usr/share/nginx/html/manifest.json

# Copy nginx config
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

# Expose port
EXPOSE 80

# Start Nginx
CMD ["nginx", "-g", "daemon off;"]
