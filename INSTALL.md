# Installation Guide

## Quick Install with pipx (Recommended)

```bash
# Install from the current directory
pipx install .

# Or specify the full path
pipx install /path/to/docker-compose-copy-to-remote
```

After installation, you'll have the `docker-compose-copy` command available globally.

## Install with pip

```bash
pip install .
```

## Development Install

```bash
# Install in editable mode for development
pip install -e .
```

## Verify Installation

```bash
docker-compose-copy --help
```

## Uninstall

With pipx:
```bash
pipx uninstall docker-compose-copy
```

With pip:
```bash
pip uninstall docker-compose-copy
```

## Requirements

- Python 3.7+
- PyYAML (automatically installed as dependency)
- Optional: rsync for efficient file transfers
