# Building Freqtrade Docker Images

This document describes how to build Docker images for Freqtrade from source. If you just want to use Freqtrade with Docker, please refer to the [Docker Quick Start guide](docs/docker_quickstart.md) instead.

## Prerequisites

- Docker installed on your system (Docker Desktop or Docker Engine)
- Docker Buildx (included in Docker Desktop, may need separate installation on Linux)
- Git (to clone the repository)
- At least 4GB of free disk space for building images

## Building the Standard Image

The main Freqtrade Docker image is defined in the `Dockerfile` at the repository root.

### Basic Build

To build the standard image locally:

```bash
docker build -t freqtrade:custom .
```

This will create a Docker image tagged as `freqtrade:custom` with the following features:
- Python 3.13 base
- All standard Freqtrade dependencies
- Hyperopt support included
- FreqUI web interface

### Build with Cache

For faster rebuilds, you can use Docker's layer caching:

```bash
# Pull the latest official image to use as cache
docker pull freqtradeorg/freqtrade:develop

# Build with cache
docker build --cache-from freqtradeorg/freqtrade:develop -t freqtrade:custom .
```

### Running Your Custom Build

After building, you can run your custom image:

```bash
docker run --rm -it freqtrade:custom --help
```

## Building Specialized Images

Freqtrade provides several specialized Dockerfiles in the `docker/` directory for different use cases.

### FreqAI Image

The FreqAI image includes machine learning dependencies for advanced AI strategies:

```bash
# First build the base image (or use the official one)
docker build -t freqtrade:base .

# Then build the FreqAI image
docker build \
  --build-arg sourceimage=freqtrade \
  --build-arg sourcetag=base \
  -t freqtrade:freqai \
  -f docker/Dockerfile.freqai .
```

This image includes:
- All standard Freqtrade features
- FreqAI machine learning dependencies from `requirements-freqai.txt`
- Support for predictive models

### FreqAI with Reinforcement Learning

For reinforcement learning strategies, build the RL variant:

```bash
# Build FreqAI image first (see above)
# Then build the RL image
docker build \
  --build-arg sourceimage=freqtrade \
  --build-arg sourcetag=freqai \
  -t freqtrade:freqai-rl \
  -f docker/Dockerfile.freqai_rl .
```

This adds PyTorch and reinforcement learning libraries.

### Plotting Image

For generating charts and visualizations:

```bash
# Build the base image first
docker build -t freqtrade:base .

# Build the plotting image
docker build \
  --build-arg sourceimage=freqtrade \
  --build-arg sourcetag=base \
  -t freqtrade:plot \
  -f docker/Dockerfile.plot .
```

This image includes plotting dependencies from `requirements-plot.txt`.

### Custom Dependencies

If you need additional Python packages or system dependencies, use the custom Dockerfile template:

1. Copy the example custom Dockerfile:
   ```bash
   cp docker/Dockerfile.custom Dockerfile.mycustom
   ```

2. Edit `Dockerfile.mycustom` to add your dependencies:
   ```dockerfile
   FROM freqtradeorg/freqtrade:develop
   
   # Switch to root if you need to install system packages
   USER root
   RUN apt-get update && apt-get install -y your-package
   
   # Switch back to ftuser
   USER ftuser
   
   # Install your Python packages
   RUN pip install --user your-python-package
   ```

3. Build your custom image:
   ```bash
   docker build -t freqtrade:mycustom -f Dockerfile.mycustom .
   ```

4. Update your `docker-compose.yml` to use your custom image:
   ```yaml
   services:
     freqtrade:
       image: freqtrade:mycustom
       build:
         context: .
         dockerfile: "./Dockerfile.mycustom"
   ```

5. Build and run with docker compose:
   ```bash
   docker compose build
   docker compose up -d
   ```

## Multi-Architecture Builds

Freqtrade supports multiple architectures: x86_64 (AMD64), ARM64, and ARM v7 (Raspberry Pi).

### Building for ARM64

```bash
docker buildx build \
  --platform linux/arm64 \
  -t freqtrade:arm64 \
  --load \
  .
```

### Building for ARM v7 (Raspberry Pi)

For Raspberry Pi and similar ARM v7 devices, use the ARMHF Dockerfile:

```bash
docker buildx build \
  --platform linux/arm/v7 \
  -f docker/Dockerfile.armhf \
  -t freqtrade:armhf \
  --load \
  .
```

**Note:** The ARMHF build uses Python 3.11 and includes piwheels for faster installation on Raspberry Pi.

### Building Multi-Platform Images

To build a single image that works on multiple platforms:

```bash
# Enable buildx (if not already enabled)
docker buildx create --name multibuilder --use
docker buildx inspect --bootstrap

# Build for multiple platforms
docker buildx build \
  --platform linux/amd64,linux/arm64,linux/arm/v7 \
  -t freqtrade:multiarch \
  --push \
  .
```

**Note:** Multi-platform builds require pushing to a registry; they cannot be loaded locally.

## Build Arguments and Environment Variables

### Available Build Arguments

The Dockerfiles support several build arguments:

- `sourceimage`: Base image name (for specialized images)
- `sourcetag`: Base image tag (for specialized images)

Example:
```bash
docker build \
  --build-arg sourceimage=myregistry/freqtrade \
  --build-arg sourcetag=latest \
  -f docker/Dockerfile.freqai \
  -t freqtrade:custom-freqai \
  .
```

### Runtime Environment Variables

The following environment variables are set in the Docker image:

- `LANG=C.UTF-8` - Locale settings
- `LC_ALL=C.UTF-8` - Locale settings
- `PYTHONDONTWRITEBYTECODE=1` - Prevent Python from writing .pyc files
- `PYTHONFAULTHANDLER=1` - Enable Python fault handler
- `PATH=/home/ftuser/.local/bin:$PATH` - Include user pip packages
- `FT_APP_ENV=docker` - Indicates running in Docker environment

## Using Docker Compose for Building

The `docker-compose.yml` file can be configured to build images locally.

### Basic docker-compose build

1. Uncomment the build section in `docker-compose.yml`:
   ```yaml
   services:
     freqtrade:
       # image: freqtradeorg/freqtrade:stable
       build:
         context: .
         dockerfile: "./Dockerfile"
   ```

2. Build the image:
   ```bash
   docker compose build
   ```

3. Run the container:
   ```bash
   docker compose up -d
   ```

### Building with custom Dockerfile

To use a custom Dockerfile with docker-compose:

```yaml
services:
  freqtrade:
    image: freqtrade:custom
    build:
      context: .
      dockerfile: "./docker/Dockerfile.custom"
```

Then build and run:
```bash
docker compose build --pull
docker compose up -d
```

## Advanced Build Techniques

### Build with No Cache

To force a complete rebuild without using any cached layers:

```bash
docker build --no-cache -t freqtrade:latest .
```

### Build with Progress Output

For detailed build progress:

```bash
docker build --progress=plain -t freqtrade:latest .
```

### Optimize Build Context

The `.dockerignore` file excludes unnecessary files from the build context. To verify what's being sent:

```bash
docker build --progress=plain -t freqtrade:test . 2>&1 | grep "transferring context"
```

### Prune Build Cache

To clean up Docker build cache:

```bash
docker builder prune -af
```

## Testing Your Build

After building an image, you should test it to ensure it works correctly:

### 1. Verify the Image

```bash
# Check if the image was created
docker images | grep freqtrade

# Inspect the image
docker inspect freqtrade:custom
```

### 2. Run Basic Commands

```bash
# Check version
docker run --rm freqtrade:custom --version

# List available commands
docker run --rm freqtrade:custom --help

# List exchanges
docker run --rm freqtrade:custom list-exchanges
```

### 3. Run Backtesting

Test with the included sample data:

```bash
docker run --rm \
  -v $(pwd)/tests/testdata:/freqtrade/tests/testdata \
  freqtrade:custom \
  backtesting \
  --datadir /freqtrade/tests/testdata \
  --strategy SampleStrategy \
  --timerange 20200101-20200201
```

### 4. Validate FreqUI Installation

```bash
# Check if FreqUI is installed
docker run --rm freqtrade:custom --help | grep webserver
```

## Troubleshooting

### Build Fails with Out of Memory

If the build fails due to insufficient memory:

1. Increase Docker memory allocation (Docker Desktop > Settings > Resources)
2. Close other applications to free up RAM
3. Use multi-stage builds (already implemented in the Dockerfiles)

### Build Fails with Disk Space Issues

```bash
# Clean up old images and containers
docker system prune -a

# Remove dangling images
docker image prune

# Check disk usage
docker system df
```

### Dependency Installation Failures

If pip packages fail to install:

1. Check your internet connection
2. Try using a different Python package index:
   ```bash
   docker build --build-arg PIP_INDEX_URL=https://pypi.org/simple -t freqtrade:custom .
   ```
3. Build without cache:
   ```bash
   docker build --no-cache -t freqtrade:custom .
   ```

### ARM Build Issues

For ARM builds, especially on Raspberry Pi:

1. Ensure you have sufficient disk space (at least 8GB free)
2. Use the ARMHF-specific Dockerfile (`docker/Dockerfile.armhf`)
3. Consider building on a more powerful machine and pushing to a registry
4. Use piwheels (already configured in ARMHF Dockerfile)

### Permission Issues

If you encounter permission issues when running the container:

```bash
# The image runs as ftuser (UID 1000)
# Ensure your user_data directory has correct permissions
sudo chown -R 1000:1000 user_data/
```

## CI/CD Integration

The official Freqtrade images are built using GitHub Actions. You can refer to `.github/workflows/docker-build.yml` for the complete CI/CD pipeline configuration.

Key features of the CI/CD build:
- Builds for multiple architectures (AMD64, ARM64, ARMv7)
- Uses layer caching for faster builds
- Runs automated tests after building
- Creates multi-platform manifests
- Pushes to Docker Hub and GitHub Container Registry

## Registry and Tagging

### Official Images

Freqtrade official images are available at:
- Docker Hub: `freqtradeorg/freqtrade`
- GitHub Container Registry: `ghcr.io/freqtrade/freqtrade`

Available tags:
- `stable` - Latest stable release
- `develop` - Development branch (latest features)
- `develop_plot` - Development with plotting support
- `develop_freqai` - Development with FreqAI support
- `develop_freqai_rl` - Development with FreqAI RL support
- Specific versions: `2024.x`, `2025.x`, etc.

### Pushing to Your Own Registry

To push your custom build to a registry:

```bash
# Tag your image
docker tag freqtrade:custom myregistry.com/myuser/freqtrade:custom

# Login to your registry
docker login myregistry.com

# Push the image
docker push myregistry.com/myuser/freqtrade:custom
```

## Performance Optimization

### Reducing Image Size

To reduce the final image size:

1. Use multi-stage builds (already implemented)
2. Clean up package manager caches
3. Remove unnecessary files after installation
4. Use `--no-cache-dir` with pip (already implemented)

### Faster Builds

To speed up builds:

1. Use layer caching with `--cache-from`
2. Order Dockerfile instructions from least to most frequently changing
3. Use BuildKit for parallel builds (enabled by default in recent Docker versions)
4. Implement a CI/CD cache strategy

## Additional Resources

- [Docker Quick Start Guide](docs/docker_quickstart.md) - For using pre-built images
- [Official Documentation](https://www.freqtrade.io/) - Complete Freqtrade documentation
- [Docker Documentation](https://docs.docker.com/) - Official Docker documentation
- [Dockerfile Reference](https://docs.docker.com/engine/reference/builder/) - Dockerfile syntax
- [GitHub Repository](https://github.com/freqtrade/freqtrade) - Source code and issues

## Contributing

If you make improvements to the Docker build process, please consider contributing back:

1. Fork the repository
2. Make your changes
3. Test thoroughly
4. Submit a pull request

See [CONTRIBUTING.md](CONTRIBUTING.md) for more details.
