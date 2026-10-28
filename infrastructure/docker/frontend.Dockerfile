# WarrantyVault AI — production frontend image.
#
# Multi-stage (TSD §51): a Node build stage compiles the Vite bundle, and an
# nginx:alpine runtime serves the static assets AND terminates TLS / proxies
# /api to the backend (see infrastructure/nginx/nginx.conf). The dev
# single-stage image (Vite dev server) remains available via `--target dev`.

FROM node:20-slim AS dev
WORKDIR /app
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host"]

FROM node:20-slim AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
ARG VITE_API_BASE=""
ENV VITE_API_BASE=$VITE_API_BASE
RUN npm run build

FROM nginx:1.27-alpine AS runtime
# Runtime config is mounted (see compose.prod): nginx.conf + TLS certs +
# the SPA build.
COPY infrastructure/nginx/nginx.conf /etc/nginx/nginx.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 443 80
CMD ["nginx", "-g", "daemon off;"]
