# Docker Compose Copy to Remote

A Python utility that analyzes `docker-compose.yml` files, extracts bind mounts, and automatically copies them to remote Docker contexts over SSH.

So, no more problems with the bind mounts when working with local files over SSH, or in Docker Desktop on Mac, as long as you don't mind having them in your SSH user's home directory,
but you would have to copy them to that remote server anyways, so... also, I hope you don't mind having the directories grow, as there is no delete implemented... 

## Features

- Parses `docker-compose.yml` files using PyYAML or a fallback parser
- Extracts all bind mount volumes from services
- Detects current Docker context and checks if it uses SSH
- **Smart file transfer** with checksum verification to skip unchanged files
- **rsync support** for efficient delta transfers and built-in checksums
- Automatically copies local files/directories to remote host via SCP or rsync
- Supports both relative and absolute paths
- Handles both short and long volume syntax
- Dry-run mode for testing without copying

## Requirements

- Python 3.7+
- `docker` CLI tool
- SSH access to remote Docker host (if using SSH context)
- `pyyaml` for YAML parsing (automatically installed)
- Optional: `rsync` for more efficient file transfers

## Installation

### Via pipx (recommended)

```bash
pipx install /path/to/docker-compose-copy-to-remote
```

Or install directly from the directory:

```bash
cd docker-compose-copy-to-remote
pipx install .
```

### Via pip

```bash
pip install /path/to/docker-compose-copy-to-remote
```

### From source (development)

```bash
git clone <repository-url>
cd docker-compose-copy-to-remote
pip install -e .
```

After installation, the `docker-compose-copy` command will be available globally.

## Usage

### Basic usage

```bash
# Analyze default docker-compose.yml and copy files
docker-compose-copy

# Analyze specific file
docker-compose-copy my-compose.yml

# Dry run (show what would be copied)
docker-compose-copy --dry-run

# Use rsync for efficient transfers (recommended)
docker-compose-copy --use-rsync

# Specify custom remote base directory (e.g., /opt/volumes)
# This will copy ./foo to /opt/volumes/foo on the remote
docker-compose-copy --remote-base /opt/volumes

# Skip checksum verification (always copy with scp)
docker-compose-copy --skip-checksums
```

### Options

- `compose_file` - Path to docker-compose.yml file (default: `docker-compose.yml`)
- `--remote-base DIR` - Base directory on remote host (default: `.` - preserves relative paths)
- `--dry-run` - Show what would be copied without actually copying
- `--use-rsync` - Use rsync instead of scp (more efficient, with built-in checksums)
- `--skip-checksums` - Skip checksum verification when using scp (not applicable with rsync)
- `--help` - Show help message

### Transfer Methods

#### SCP with checksums (default)
By default, the tool uses `scp` for file transfer but calculates SHA256 checksums to skip files that haven't changed:

```bash
docker-compose-copy
```

This will:
1. Calculate checksum of local file or directory
2. Get checksum of remote file or directory (if it exists)
   - For files: Direct SHA256 checksum
   - For directories: Recursive checksum of all files
3. Skip copying if checksums match
4. Copy only changed files/directories

#### rsync (recommended for large files/directories)
For more efficient transfers, especially with large files or directories, use rsync:

```bash
docker-compose-copy --use-rsync
```

rsync advantages:
- **Delta transfer**: Only sends file differences, not entire files
- **Built-in checksums**: Automatically verifies file integrity
- **Resumable**: Can resume interrupted transfers
- **Preserves permissions**: Maintains file permissions and timestamps

#### Force copy everything (skip checksums)
To always copy all files without checksum verification:

```bash
docker-compose-copy --skip-checksums
```

## How it works

1. **Parse docker-compose.yml**: Reads and parses the compose file
2. **Extract bind mounts**: Identifies all volume binds that map local paths
3. **Check Docker context**: Determines if current Docker context uses SSH
4. **Smart transfer**:
   - With checksums (default): Compares file checksums and skips identical files
   - With rsync: Uses delta transfer for maximum efficiency
5. **Copy files**: If SSH context detected, copies/syncs all bind mount sources to remote host using the same relative paths from docker-compose.yml

## Docker Context Setup

To use with a remote Docker host over SSH:

```bash
# Create SSH context
docker context create remote-host --docker "host=ssh://user@remote-host"

# Use the context
docker context use remote-host

# Run the utility
docker-compose-copy

# Or use rsync for better performance
docker-compose-copy --use-rsync
```

## Example

Given this `docker-compose.yml`:

```yaml
services:
  web:
    image: nginx
    volumes:
      - ./config/nginx.conf:/etc/nginx/nginx.conf
      - ./html:/usr/share/nginx/html
```

When running with an SSH Docker context:

```bash
$ docker-compose-copy

=== Docker Compose Copy to Remote ===
Compose file: /path/to/docker-compose.yml

Found 2 bind mount(s):
  ✓ ./config/nginx.conf -> /path/to/config/nginx.conf
  ✓ ./html -> /path/to/html

Current Docker context: remote-host
SSH context detected: user@remote-host

=== Copying files to remote context via SSH (SCP) ===
Remote host: user@remote-host
Remote base directory: .
Checksum verification: enabled
Files to process: 2

Copying file: /path/to/config/nginx.conf -> user@remote-host:./config/nginx.conf
✓ Copied: ./config/nginx.conf
Copying directory: /path/to/html -> user@remote-host:./html
✓ Copied: ./html

Summary: 2 copied, 0 skipped

✓ All files transferred successfully!

Files were transferred using the same relative paths as in docker-compose.yml.
```

With rsync:

```bash
$ docker-compose-copy --use-rsync

=== Syncing files to remote context via rsync ===
Remote host: user@remote-host
Remote base directory: .
Files to sync: 2

Syncing file: /path/to/config/nginx.conf -> user@remote-host:./config/nginx.conf
✓ Synced: ./config/nginx.conf
Syncing directory: /path/to/html -> user@remote-host:./html
✓ Up-to-date: ./html

✓ All files transferred successfully!
```

## Supported Volume Formats

### Short syntax
```yaml
volumes:
  - ./local/path:/container/path
  - /absolute/path:/container/path:ro
```

### Long syntax
```yaml
volumes:
  - type: bind
    source: ./local/path
    target: /container/path
```

## Notes

- Named volumes (e.g., `db_data:/var/lib/postgresql/data`) are ignored - only bind mounts are copied
- **The utility preserves the relative paths from docker-compose.yml** (e.g., `./foo` is copied to `./foo` on the remote)
- For files with subdirectories (e.g., `./foo/bar/baz`), the directory structure is automatically created on the remote
- Absolute paths (e.g., `/var/log/nginx`) are copied to the same absolute path on the remote
- Use `--remote-base` to copy all files under a different base directory on the remote
- Local Docker contexts (non-SSH) are detected and no copying occurs
- **Checksum verification** (default with scp): Uses SHA256 to skip unchanged files
- **rsync** is recommended for large files/directories as it only transfers differences

## Performance Tips

1. **Use rsync for large projects**: `docker-compose-copy --use-rsync`
2. **Use checksums for selective updates**: Default behavior skips unchanged files
3. **For initial setup**: Consider using `--skip-checksums` to avoid checksum overhead
4. **For repeated syncs**: rsync is most efficient as it only transfers file deltas

## License

MIT
