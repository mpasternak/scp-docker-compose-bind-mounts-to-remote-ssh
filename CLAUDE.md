# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a single-file Python utility (`docker_compose_copy.py`) that analyzes docker-compose.yml files, extracts bind mounts, and copies them to remote Docker hosts over SSH. The key design principle is **preserving relative paths** from the docker-compose file, so the same compose file works both locally and on remote SSH contexts without modification.

## Running the Utility

```bash
# Basic usage (analyzes docker-compose.yml in current directory)
./docker_compose_copy.py

# Analyze specific compose file
./docker_compose_copy.py path/to/docker-compose.yml

# Dry run to see what would be copied
./docker_compose_copy.py --dry-run

# Copy to a different base directory on remote
./docker_compose_copy.py --remote-base /opt/volumes
```

## Architecture

### Core Data Structure

The `BindMount` NamedTuple is central to the design:
- `local_path: Path` - Absolute resolved path for local file operations (checking existence, reading)
- `relative_path: str` - Original relative path from docker-compose.yml (e.g., `./config/nginx.conf`)

This dual-path approach allows:
1. Checking if local files exist using absolute paths
2. Copying to remote using the exact relative paths from docker-compose.yml

### Parsing Strategy

The utility uses a two-tier parsing approach:
1. **Primary**: PyYAML (`parse_yaml_simple`) - Handles complex YAML structures
2. **Fallback**: Manual parser (`parse_yaml_manual`) - Simple regex-based parser when PyYAML unavailable

The fallback parser is intentionally limited to basic docker-compose structures (services with volume binds). It expects standard 2-space YAML indentation.

### Path Preservation Logic

**Critical behavior** (`copy_files_via_ssh` function):
- Relative paths like `./foo/bar/baz` are copied to `./foo/bar/baz` on the remote (default)
- Directory structure is automatically created on remote using `mkdir -p`
- Absolute paths (e.g., `/var/log/nginx`) are copied to the same absolute path on remote
- Named volumes (e.g., `db_data:/var/lib/...`) are ignored - only bind mounts are processed

When `--remote-base` is specified (non-default), the relative path is combined:
- `./foo` with `--remote-base /opt` → `/opt/foo` on remote

### Docker Context Detection

The utility follows this workflow:
1. Get current Docker context using `docker context show`
2. Inspect context details using `docker context inspect`
3. Check if context uses SSH by examining the `Host` field in endpoints
4. Extract SSH host from `ssh://user@host` format
5. Only copy files if SSH context is detected

## Testing

Currently no automated tests. To test manually:

```bash
# Test parsing without SSH context
./docker_compose_copy.py --dry-run

# Test with PyYAML
pip install pyyaml
./docker_compose_copy.py --dry-run

# Test fallback parser (requires uninstalling PyYAML temporarily)
pip uninstall pyyaml
./docker_compose_copy.py --dry-run
```

The included `docker-compose.yml` file serves as a test fixture with various bind mount formats.

## Key Implementation Details

### Volume Format Support

The `extract_bind_mounts` function handles both docker-compose volume syntaxes:

**Short syntax** (string):
```yaml
volumes:
  - ./local/path:/container/path
  - ./local/path:/container/path:ro
```

**Long syntax** (dict):
```yaml
volumes:
  - type: bind
    source: ./local/path
    target: /container/path
```

### SCP Behavior

Directory copying requires careful handling:
- `scp -r ./foo user@host:./foo` copies the directory itself to create `./foo` on remote
- Parent directories are created first using `ssh user@host 'mkdir -p ./parent'`
- Files in subdirectories (e.g., `./config/nginx.conf`) trigger creation of `./config/` first

## Dependencies

- **Python 3.6+** (uses f-strings, typing, pathlib)
- **Required**: `docker` CLI tool for context inspection
- **Required**: SSH access to remote hosts (uses `ssh` and `scp` commands)
- **Optional**: `pyyaml` library for robust YAML parsing (gracefully degrades without it)

## Common Modifications

When modifying this utility:

1. **Changing path handling**: The path logic is split between `extract_bind_mounts` (creates BindMount tuples) and `copy_files_via_ssh` (uses the relative_path field). Update both if changing path behavior.

2. **Adding volume format support**: Modify `extract_bind_mounts` to handle new docker-compose volume syntax. Ensure both absolute and relative paths are captured correctly.

3. **Improving the fallback parser**: The `parse_yaml_manual` function uses hardcoded indentation levels (2 spaces for services, 4 for properties, 6 for list items). This matches standard docker-compose formatting but may need adjustment for non-standard files.

4. **Error handling**: SSH/SCP operations use `subprocess.run()` with `check=True` and `capture_output=True`. Errors are caught and reported but don't halt the entire operation - other files continue copying.
