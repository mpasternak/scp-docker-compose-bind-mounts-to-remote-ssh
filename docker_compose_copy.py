#!/usr/bin/env python3
"""
Docker Compose Copy to Remote Utility

This utility analyzes docker-compose.yml files, extracts bind mounts,
and copies them to remote Docker contexts over SSH if applicable.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Set, Optional, Tuple, NamedTuple


class BindMount(NamedTuple):
    """Represents a bind mount with both absolute path and original relative path."""
    local_path: Path  # Absolute resolved path for checking existence
    relative_path: str  # Original relative path from docker-compose.yml


def parse_yaml_simple(file_path: Path) -> dict:
    """
    Parse YAML file using stdlib only (fallback if PyYAML not available).
    This is a simple parser that handles basic docker-compose.yml structure.
    """
    try:
        import yaml
        with open(file_path, 'r') as f:
            return yaml.safe_load(f)
    except ImportError:
        # Fallback to manual parsing for simple cases
        print("Warning: PyYAML not available, using simple parser. Install PyYAML for better compatibility.")
        print("Run: pip install pyyaml")
        return parse_yaml_manual(file_path)


def parse_yaml_manual(file_path: Path) -> dict:
    """
    Manual YAML parser for docker-compose files (limited functionality).
    Handles basic service definitions and volume binds.
    """
    with open(file_path, 'r') as f:
        content = f.read()

    result = {'services': {}}
    current_service = None
    in_volumes = False

    for line in content.split('\n'):
        # Skip comments and empty lines
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue

        # Detect services section
        if line.startswith('services:'):
            continue

        # Detect service name (2 spaces indentation)
        if line.startswith('  ') and not line.startswith('    ') and ':' in line:
            service_name = line.strip().rstrip(':')
            if service_name != 'volumes':
                current_service = service_name
                result['services'][current_service] = {'volumes': []}
                in_volumes = False

        # Detect volumes section within service (4 spaces)
        if current_service and line.strip() == 'volumes:':
            in_volumes = True

        # Parse volume entries (6+ spaces, starting with -)
        if in_volumes and current_service and stripped.startswith('- '):
            volume_def = stripped[2:].strip()
            result['services'][current_service]['volumes'].append(volume_def)

    return result


def extract_bind_mounts(compose_data: dict, compose_file_dir: Path) -> Set[BindMount]:
    """
    Extract all bind mount source paths from docker-compose data.

    Bind mounts are volumes that map host paths to container paths.
    Format: "host_path:container_path" or "host_path:container_path:options"

    Returns a set of BindMount objects containing both absolute and relative paths.
    """
    bind_mounts = set()

    services = compose_data.get('services', {})

    for service_name, service_config in services.items():
        if not isinstance(service_config, dict):
            continue

        volumes = service_config.get('volumes', [])
        if not isinstance(volumes, list):
            continue

        for volume in volumes:
            # Handle both short syntax (string) and long syntax (dict)
            if isinstance(volume, str):
                # Short syntax: "source:target" or "source:target:mode"
                parts = volume.split(':')
                if len(parts) >= 2:
                    source = parts[0]

                    # Skip named volumes (don't start with . or /)
                    if not (source.startswith('.') or source.startswith('/')):
                        continue

                    # Resolve relative paths for local access
                    if source.startswith('.'):
                        source_path = (compose_file_dir / source).resolve()
                        relative_path = source  # Keep original relative path
                    else:
                        # For absolute paths, use as-is
                        source_path = Path(source).resolve()
                        relative_path = source

                    bind_mounts.add(BindMount(source_path, relative_path))

            elif isinstance(volume, dict):
                # Long syntax
                volume_type = volume.get('type', 'volume')
                if volume_type == 'bind':
                    source = volume.get('source', '')
                    if source:
                        # Resolve relative paths for local access
                        if source.startswith('.'):
                            source_path = (compose_file_dir / source).resolve()
                            relative_path = source  # Keep original relative path
                        else:
                            # For absolute paths, use as-is
                            source_path = Path(source).resolve()
                            relative_path = source

                        bind_mounts.add(BindMount(source_path, relative_path))

    return bind_mounts


def get_current_docker_context() -> Optional[str]:
    """
    Get the current Docker context name.
    """
    try:
        result = subprocess.run(
            ['docker', 'context', 'show'],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def get_docker_context_info(context_name: str) -> Optional[Dict]:
    """
    Get detailed information about a Docker context.
    """
    try:
        result = subprocess.run(
            ['docker', 'context', 'inspect', context_name],
            capture_output=True,
            text=True,
            check=True
        )
        context_info = json.loads(result.stdout)
        return context_info[0] if context_info else None
    except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError, IndexError):
        return None


def is_ssh_context(context_info: Dict) -> Tuple[bool, Optional[str]]:
    """
    Check if the Docker context uses SSH.
    Returns (is_ssh, ssh_host) tuple.
    """
    if not context_info:
        return False, None

    # Check endpoints for SSH
    endpoints = context_info.get('Endpoints', {})
    docker_endpoint = endpoints.get('docker', {})
    host = docker_endpoint.get('Host', '')

    # SSH hosts start with ssh://
    if host.startswith('ssh://'):
        # Extract host from ssh://user@host or ssh://host
        ssh_host = host.replace('ssh://', '')
        return True, ssh_host

    return False, None


def calculate_file_checksum(file_path: Path) -> str:
    """
    Calculate SHA256 checksum of a file.

    Args:
        file_path: Path to the file

    Returns:
        Hexadecimal SHA256 checksum string
    """
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        # Read file in chunks to handle large files efficiently
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def get_remote_checksum(ssh_host: str, remote_path: str) -> Optional[str]:
    """
    Get SHA256 checksum of a remote file via SSH.

    Args:
        ssh_host: SSH host (user@host format)
        remote_path: Path to file on remote host

    Returns:
        Checksum string if file exists and is readable, None otherwise
    """
    try:
        # Use sha256sum on remote (available on most Linux systems)
        result = subprocess.run(
            ['ssh', ssh_host, f'sha256sum "{remote_path}" 2>/dev/null || echo "NOTFOUND"'],
            capture_output=True,
            text=True,
            check=True,
            timeout=30
        )
        output = result.stdout.strip()
        if output == "NOTFOUND" or not output:
            return None
        # sha256sum output format: "checksum  filename"
        checksum = output.split()[0]
        return checksum
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, IndexError):
        return None


def get_directory_file_list_with_checksums(dir_path: Path) -> Dict[str, str]:
    """
    Get a dictionary of all files in a directory with their checksums.

    Args:
        dir_path: Path to the directory

    Returns:
        Dictionary mapping relative file paths to their SHA256 checksums
    """
    if not dir_path.is_dir():
        raise ValueError(f"{dir_path} is not a directory")

    file_checksums = {}
    for file_path in dir_path.rglob('*'):
        if file_path.is_file():
            relative = str(file_path.relative_to(dir_path))
            try:
                checksum = calculate_file_checksum(file_path)
                file_checksums[relative] = checksum
            except Exception:
                # If we can't read a file, mark it
                file_checksums[relative] = "UNREADABLE"

    return file_checksums


def get_remote_directory_file_list(ssh_host: str, remote_path: str) -> Optional[Dict[str, str]]:
    """
    Get a dictionary of all files in a remote directory with their checksums.

    Args:
        ssh_host: SSH host (user@host format)
        remote_path: Path to directory on remote host

    Returns:
        Dictionary mapping relative file paths to checksums, or None if directory doesn't exist
    """
    try:
        # Shell script to get all file checksums in the directory
        script = f'''
if [ ! -d "{remote_path}" ]; then
    exit 1
fi
cd "{remote_path}" || exit 1
find . -type f | sort | while IFS= read -r file; do
    sum=$(sha256sum "$file" 2>/dev/null | awk '{{print $1}}')
    if [ -n "$sum" ]; then
        # Remove leading ./
        cleanfile=${{file#./}}
        echo "$cleanfile:$sum"
    fi
done
'''
        result = subprocess.run(
            ['ssh', ssh_host, script],
            capture_output=True,
            text=True,
            check=True,
            timeout=60
        )

        # Parse the output into a dictionary
        file_checksums = {}
        for line in result.stdout.strip().split('\n'):
            if ':' in line and line:
                filepath, checksum = line.rsplit(':', 1)
                file_checksums[filepath] = checksum

        return file_checksums
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None


def compare_directories(local_path: Path, ssh_host: str, remote_path: str) -> Tuple[bool, List[str], List[str]]:
    """
    Compare local and remote directories file-by-file.

    Args:
        local_path: Local directory path
        ssh_host: SSH host (user@host format)
        remote_path: Remote directory path

    Returns:
        Tuple of (all_identical, changed_files, new_files)
        - all_identical: True if all files are identical
        - changed_files: List of files that exist in both but have different checksums
        - new_files: List of files that only exist locally
    """
    try:
        local_files = get_directory_file_list_with_checksums(local_path)
        remote_files = get_remote_directory_file_list(ssh_host, remote_path)

        if remote_files is None:
            # Remote directory doesn't exist, all files are new
            return False, [], list(local_files.keys())

        changed_files = []
        new_files = []

        for filepath, local_checksum in local_files.items():
            if filepath not in remote_files:
                new_files.append(filepath)
            elif remote_files[filepath] != local_checksum:
                changed_files.append(filepath)

        all_identical = len(changed_files) == 0 and len(new_files) == 0
        return all_identical, changed_files, new_files

    except Exception:
        return False, [], []


def files_are_identical(local_path: Path, ssh_host: str, remote_path: str) -> bool:
    """
    Check if local and remote files/directories are identical by comparing checksums.

    For files: Compares SHA256 checksums directly.
    For directories: Compares all files within recursively.

    Args:
        local_path: Local file or directory path
        ssh_host: SSH host (user@host format)
        remote_path: Remote file or directory path

    Returns:
        True if files/directories have identical checksums, False otherwise
    """
    try:
        if local_path.is_file():
            local_checksum = calculate_file_checksum(local_path)
            remote_checksum = get_remote_checksum(ssh_host, remote_path)

            if remote_checksum is None:
                return False

            return local_checksum == remote_checksum

        elif local_path.is_dir():
            all_identical, _, _ = compare_directories(local_path, ssh_host, remote_path)
            return all_identical

        else:
            return False

    except Exception:
        return False


def copy_files_via_ssh(bind_mounts: Set[BindMount], ssh_host: str, remote_base: str = '.',
                       skip_checksums: bool = False) -> bool:
    """
    Copy local files to remote host via SSH/SCP using relative paths.

    Args:
        bind_mounts: Set of BindMount objects with local and relative paths
        ssh_host: SSH host (user@host format)
        remote_base: Base directory on remote host (default: '.' for current directory)
        skip_checksums: If True, skip checksum verification and always copy

    Returns:
        True if all copies succeeded, False otherwise
    """
    print(f"\n=== Copying files to remote context via SSH (SCP) ===")
    print(f"Remote host: {ssh_host}")
    print(f"Remote base directory: {remote_base}")
    print(f"Checksum verification: {'disabled' if skip_checksums else 'enabled'}")
    print(f"Files to process: {len(bind_mounts)}\n")

    success = True
    skipped_count = 0
    copied_count = 0

    for bind_mount in bind_mounts:
        local_path = bind_mount.local_path
        relative_path = bind_mount.relative_path

        if not local_path.exists():
            print(f"⚠ Warning: Local path does not exist: {local_path}")
            continue

        # Use the original relative path from docker-compose.yml
        # Combine with remote_base if not using current directory
        if remote_base == '.':
            remote_path = relative_path
        else:
            # Strip leading ./ if present for cleaner path joining
            clean_relative = relative_path.lstrip('./')
            remote_path = f"{remote_base}/{clean_relative}"

        # Check if file/directory already exists and is identical
        if not skip_checksums:
            if local_path.is_dir():
                print(f"Checking directory checksums for: {relative_path}...")
                # Get detailed comparison for directories
                all_identical, changed_files, new_files = compare_directories(local_path, ssh_host, remote_path)

                if all_identical:
                    print(f"⊘ Skipped (identical directory): {relative_path}")
                    skipped_count += 1
                    continue
                else:
                    # Show what changed
                    if new_files:
                        print(f"  New files ({len(new_files)}): {', '.join(new_files[:5])}")
                        if len(new_files) > 5:
                            print(f"    ... and {len(new_files) - 5} more")
                    if changed_files:
                        print(f"  Changed files ({len(changed_files)}): {', '.join(changed_files[:5])}")
                        if len(changed_files) > 5:
                            print(f"    ... and {len(changed_files) - 5} more")
            else:
                # For files, use the simple check
                if files_are_identical(local_path, ssh_host, remote_path):
                    print(f"⊘ Skipped (identical file): {relative_path}")
                    skipped_count += 1
                    continue

        # Get the parent directory to create on remote
        remote_dir = str(Path(remote_path).parent)

        # Create remote parent directory if needed
        if remote_dir and remote_dir != '.':
            try:
                subprocess.run(
                    ['ssh', ssh_host, f'mkdir -p "{remote_dir}"'],
                    check=True,
                    capture_output=True
                )
            except subprocess.CalledProcessError as e:
                print(f"✗ Failed to create remote directory {remote_dir}: {e}")
                success = False
                continue

        # Copy file or directory
        try:
            if local_path.is_dir():
                # For directories, we need to copy the directory itself
                # scp -r ./foo user@host:./foo should create ./foo on remote
                print(f"Copying directory: {local_path} -> {ssh_host}:{remote_path}")

                # First ensure parent exists, then copy the directory
                parent = str(Path(remote_path).parent)
                if parent and parent != '.':
                    subprocess.run(
                        ['ssh', ssh_host, f'mkdir -p "{parent}"'],
                        check=True,
                        capture_output=True
                    )

                # Copy directory recursively
                subprocess.run(
                    ['scp', '-r', str(local_path), f'{ssh_host}:{remote_path}'],
                    check=True,
                    capture_output=True
                )
            else:
                # Copy file
                print(f"Copying file: {local_path} -> {ssh_host}:{remote_path}")
                subprocess.run(
                    ['scp', str(local_path), f'{ssh_host}:{remote_path}'],
                    check=True,
                    capture_output=True
                )
            print(f"✓ Copied: {relative_path}")
            copied_count += 1
        except subprocess.CalledProcessError as e:
            print(f"✗ Failed to copy {local_path}: {e}")
            success = False

    print(f"\nSummary: {copied_count} copied, {skipped_count} skipped")
    return success


def sync_files_via_rsync(bind_mounts: Set[BindMount], ssh_host: str, remote_base: str = '.') -> bool:
    """
    Sync local files to remote host using rsync over SSH.

    rsync is more efficient than scp as it:
    - Only transfers differences (delta transfer)
    - Preserves permissions and timestamps
    - Has built-in checksum verification
    - Can resume interrupted transfers

    Args:
        bind_mounts: Set of BindMount objects with local and relative paths
        ssh_host: SSH host (user@host format)
        remote_base: Base directory on remote host (default: '.' for current directory)

    Returns:
        True if all syncs succeeded, False otherwise
    """
    print(f"\n=== Syncing files to remote context via rsync ===")
    print(f"Remote host: {ssh_host}")
    print(f"Remote base directory: {remote_base}")
    print(f"Files to sync: {len(bind_mounts)}\n")

    success = True
    for bind_mount in bind_mounts:
        local_path = bind_mount.local_path
        relative_path = bind_mount.relative_path

        if not local_path.exists():
            print(f"⚠ Warning: Local path does not exist: {local_path}")
            continue

        # Use the original relative path from docker-compose.yml
        # Combine with remote_base if not using current directory
        if remote_base == '.':
            remote_path = relative_path
        else:
            # Strip leading ./ if present for cleaner path joining
            clean_relative = relative_path.lstrip('./')
            remote_path = f"{remote_base}/{clean_relative}"

        # rsync options:
        # -a: archive mode (preserve permissions, timestamps, etc.)
        # -v: verbose
        # -z: compress during transfer
        # -c: skip based on checksum, not mod-time & size (more accurate)
        # --relative: preserve relative path structure
        # -h: human-readable output
        try:
            if local_path.is_dir():
                # For directories, ensure trailing slash for rsync
                local_str = str(local_path) + '/'
                # Create parent directory on remote
                parent = str(Path(remote_path).parent)
                if parent and parent != '.':
                    subprocess.run(
                        ['ssh', ssh_host, f'mkdir -p "{parent}"'],
                        check=True,
                        capture_output=True
                    )

                print(f"Syncing directory: {local_path} -> {ssh_host}:{remote_path}")
                result = subprocess.run(
                    ['rsync', '-avz', '--checksum', local_str, f'{ssh_host}:{remote_path}/'],
                    capture_output=True,
                    text=True,
                    check=True
                )
            else:
                # For files, create parent directory first
                remote_dir = str(Path(remote_path).parent)
                if remote_dir and remote_dir != '.':
                    subprocess.run(
                        ['ssh', ssh_host, f'mkdir -p "{remote_dir}"'],
                        check=True,
                        capture_output=True
                    )

                print(f"Syncing file: {local_path} -> {ssh_host}:{remote_path}")
                result = subprocess.run(
                    ['rsync', '-avz', '--checksum', str(local_path), f'{ssh_host}:{remote_path}'],
                    capture_output=True,
                    text=True,
                    check=True
                )

            # Check if rsync actually transferred anything
            if 'total size' in result.stdout:
                print(f"✓ Synced: {relative_path}")
            else:
                print(f"✓ Up-to-date: {relative_path}")

        except subprocess.CalledProcessError as e:
            print(f"✗ Failed to sync {local_path}: {e}")
            if e.stderr:
                print(f"  Error: {e.stderr}")
            success = False
        except FileNotFoundError:
            print(f"✗ rsync not found. Please install rsync to use this feature.")
            print(f"  On macOS: brew install rsync")
            print(f"  On Linux: sudo apt-get install rsync (or equivalent)")
            return False

    return success


def main():
    parser = argparse.ArgumentParser(
        description='Analyze docker-compose.yml and copy bind mounts to remote Docker contexts over SSH'
    )
    parser.add_argument(
        'compose_file',
        nargs='?',
        default='docker-compose.yml',
        help='Path to docker-compose.yml file (default: docker-compose.yml)'
    )
    parser.add_argument(
        '--remote-base',
        default='.',
        help='Base directory on remote host for copied files (default: . - preserves relative paths from docker-compose.yml)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be copied without actually copying'
    )
    parser.add_argument(
        '--use-rsync',
        action='store_true',
        help='Use rsync instead of scp for file transfer (more efficient, with built-in checksums and delta transfer)'
    )
    parser.add_argument(
        '--skip-checksums',
        action='store_true',
        help='Skip checksum verification when using scp (always copy files). Not applicable when using rsync.'
    )

    args = parser.parse_args()

    # Check if compose file exists
    compose_file = Path(args.compose_file)
    if not compose_file.exists():
        print(f"Error: File not found: {compose_file}")
        sys.exit(1)

    print(f"=== Docker Compose Copy to Remote ===")
    print(f"Compose file: {compose_file.resolve()}\n")

    # Parse docker-compose file
    try:
        compose_data = parse_yaml_simple(compose_file)
    except Exception as e:
        print(f"Error parsing compose file: {e}")
        sys.exit(1)

    # Extract bind mounts
    compose_dir = compose_file.parent.resolve()
    bind_mounts = extract_bind_mounts(compose_data, compose_dir)

    if not bind_mounts:
        print("No bind mounts found in docker-compose file.")
        sys.exit(0)

    print(f"Found {len(bind_mounts)} bind mount(s):")
    for mount in sorted(bind_mounts, key=lambda m: m.relative_path):
        exists = "✓" if mount.local_path.exists() else "✗"
        print(f"  {exists} {mount.relative_path} -> {mount.local_path}")
    print()

    # Check current Docker context
    context_name = get_current_docker_context()
    if not context_name:
        print("Could not determine current Docker context. Using local context.")
        print("No files will be copied.")
        sys.exit(0)

    print(f"Current Docker context: {context_name}")

    # Get context information
    context_info = get_docker_context_info(context_name)
    if not context_info:
        print("Could not retrieve context information.")
        print("No files will be copied.")
        sys.exit(0)

    # Check if SSH context
    is_ssh, ssh_host = is_ssh_context(context_info)

    if not is_ssh:
        print("Current Docker context does not use SSH.")
        print("No files will be copied.")
        sys.exit(0)

    print(f"SSH context detected: {ssh_host}\n")

    # Copy files
    if args.dry_run:
        print("=== DRY RUN MODE ===")
        method = "rsync" if args.use_rsync else "scp"
        print(f"Would copy {len(bind_mounts)} file(s)/directory(ies) to {ssh_host}:{args.remote_base} using {method}")
        for mount in sorted(bind_mounts, key=lambda m: m.relative_path):
            if args.remote_base == '.':
                remote_path = mount.relative_path
            else:
                clean_relative = mount.relative_path.lstrip('./')
                remote_path = f"{args.remote_base}/{clean_relative}"
            print(f"  {mount.relative_path} -> {ssh_host}:{remote_path}")
    else:
        if args.use_rsync:
            success = sync_files_via_rsync(bind_mounts, ssh_host, args.remote_base)
        else:
            success = copy_files_via_ssh(bind_mounts, ssh_host, args.remote_base, args.skip_checksums)

        if success:
            print("\n✓ All files transferred successfully!")
            if args.remote_base != '.':
                print(f"\nNote: Files were transferred to {args.remote_base}/ on the remote host.")
                print(f"You may need to update your docker-compose.yml to use remote paths.")
            else:
                print(f"\nFiles were transferred using the same relative paths as in docker-compose.yml.")
        else:
            print("\n✗ Some files failed to transfer. Check errors above.")
            sys.exit(1)


if __name__ == '__main__':
    main()
