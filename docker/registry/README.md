# Private Docker Registry

Self-hosted registry for pre-built brainbox profile images.

## Setup

### 1. Generate TLS certificate

```bash
mkdir -p certs
openssl req -newkey rsa:4096 -nodes -sha256 -keyout certs/domain.key \
  -x509 -days 3650 -out certs/domain.crt \
  -subj "/CN=<registry-hostname>" \
  -addext "subjectAltName=DNS:<registry-hostname>,IP:<registry-ip>"
```

### 2. Create registry credentials

```bash
mkdir -p auth
docker run --rm --entrypoint htpasswd httpd:2 -Bbn <username> <password> > auth/htpasswd
```

### 3. Trust the certificate (on every machine that will push/pull)

```bash
# macOS
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain certs/domain.crt

# Linux (Docker daemon)
sudo mkdir -p /etc/docker/certs.d/<registry-hostname>:5000
sudo cp certs/domain.crt /etc/docker/certs.d/<registry-hostname>:5000/ca.crt
sudo systemctl restart docker
```

### 4. Start

```bash
docker compose up -d
```

### 5. Configure brainbox

Add to `~/.config/phantom-ink/brainbox/brainbox.env`:

```bash
CL_REGISTRY_URL=<registry-hostname>:5000
CL_REGISTRY_USERNAME=<username>
CL_REGISTRY_PASSWORD=<password>
```

## Notes

- Port 5000 is bound to 127.0.0.1 by default. Use a reverse proxy (NPM, nginx)
  to expose it on the network with the same TLS cert.
- Profile images are pushed by the Wails app and pulled automatically by
  brainbox when a session is created for an enrolled profile.
- Image tag format: `<registry-url>/brainbox-profile:<profile-name>`
