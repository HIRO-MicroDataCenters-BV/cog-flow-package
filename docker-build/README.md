# Custom Flowise Image with BASE_PATH Support

This directory contains a custom Flowise Docker image that supports running behind a reverse proxy with a path prefix (e.g., `/flowise/`).

## Why This Is Needed

The upstream Flowise image does not yet support the `BASE_PATH` environment variable (PR #5254 was merged but the code is not in releases). This custom image patches Flowise at runtime to support path prefixes.

## Building

```bash
cd docker-build
docker build -t your-registry/flowise-basepath:latest .
docker push your-registry/flowise-basepath:latest
```

## Usage

Set the `BASE_PATH` environment variable to your path prefix (without trailing slash):

```bash
docker run -e BASE_PATH=/flowise -p 3000:3000 your-registry/flowise-basepath:latest
```

Then access Flowise at `http://localhost:3000/flowise/`

## How It Works

The entrypoint script patches these files at container startup:

1. **index.html** - Updates asset paths from `/assets/` to `/flowise/assets/`
2. **JS bundles** - Updates API calls from `/api/v1` to `/flowise/api/v1`
3. **Server index.js** - Mounts routes at `/flowise/` instead of `/`

## Deploying with the Charm

```bash
# Build and push to your registry
docker build -t your-registry/flowise-basepath:latest .
docker push your-registry/flowise-basepath:latest

# Deploy charm with custom image
juju deploy flowise --channel=latest/edge \
    --resource oci-image=your-registry/flowise-basepath:latest \
    --trust
```

## Environment Variables

- `BASE_PATH` - The path prefix (e.g., `/flowise`). No trailing slash. If not set, Flowise runs at root `/`.
