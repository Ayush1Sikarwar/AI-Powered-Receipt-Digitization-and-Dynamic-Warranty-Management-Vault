#!/usr/bin/env bash
# Generate a self-signed TLS certificate for local/staging production
# deployments. For real production, obtain a CA certificate (Let's Encrypt /
# your corporate CA) and place fullchain.pem + privkey.pem in $CERT_DIR.
#
# Usage:  infrastructure/nginx/generate-certs.sh [CERT_DIR] [DAYS] [DOMAIN]
#   CERT_DIR  defaults to infrastructure/nginx/certs
#   DAYS      validity days, defaults to 365
#   DOMAIN    CN, defaults to localhost
set -euo pipefail

CERT_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/certs}"
DAYS="${2:-365}"
DOMAIN="${3:-localhost}"

mkdir -p "$CERT_DIR"
chmod 700 "$CERT_DIR"

openssl req -x509 -newkey rsa:4096 -sha256 -nodes \
  -keyout "$CERT_DIR/privkey.pem" \
  -out "$CERT_DIR/fullchain.pem" \
  -days "$DAYS" \
  -subj "/CN=${DOMAIN}" \
  -addext "subjectAltName=DNS:${DOMAIN},DNS:*.${DOMAIN},IP:127.0.0.1" 2>/dev/null

chmod 600 "$CERT_DIR/privkey.pem"
chmod 644 "$CERT_DIR/fullchain.pem"
echo "Self-signed cert for ${DOMAIN} written to ${CERT_DIR} (valid ${DAYS} days)."
echo "For production: replace with CA-issued certs and keep privkey.pem 0600 root:root."
