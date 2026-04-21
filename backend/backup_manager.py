import os
import uuid
import zipfile
import json
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from backend.config import settings
from backend.db_models import get_db, row_to_dict
from backend.logger import app_logger


async def create_backup(
    selection: str = 'all',
    date_from: Optional[str] = None,
    date_to: Optional[str] = None
) -> dict:
    """Create a backup archive of downloaded files."""
    backup_id = str(uuid.uuid4())
    backup_dir = Path(settings.BACKUP_FOLDER)
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    archive_name = f"median_backup_{timestamp}.zip"
    archive_path = backup_dir / archive_name

    download_dir = Path(settings.UPLOAD_FOLDER)
    files_to_backup = []

    if download_dir.exists():
        for f in download_dir.rglob('*'):
            if f.is_file() and not f.name.startswith('.') and not f.name.startswith('_'):
                files_to_backup.append(f)

    loop = asyncio.get_running_loop()

    def _create_zip():
        with zipfile.ZipFile(
            str(archive_path), 'w',
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=settings.BACKUP_COMPRESSION_LEVEL
        ) as zf:
            for f in files_to_backup:
                arc_name = f.relative_to(download_dir)
                zf.write(str(f), str(arc_name))

        return len(files_to_backup), archive_path.stat().st_size

    file_count, archive_size = await loop.run_in_executor(None, _create_zip)

    # Record backup
    db = get_db()
    try:
        db.execute("""
            INSERT INTO backups (id, filename, path, size, file_count, date_range)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            backup_id, archive_name, str(archive_path),
            archive_size, file_count,
            f"{date_from or 'all'} to {date_to or 'now'}"
        ))
        db.commit()
    finally:
        db.close()

    app_logger.info(f"Backup created: {archive_name} ({file_count} files, {archive_size} bytes)")

    return {
        'id': backup_id,
        'filename': archive_name,
        'path': str(archive_path),
        'size': archive_size,
        'file_count': file_count,
    }


async def restore_backup(backup_path: str) -> dict:
    """Restore files from backup archive."""
    if not os.path.exists(backup_path):
        raise FileNotFoundError(f"Backup file not found: {backup_path}")
    if not zipfile.is_zipfile(backup_path):
        raise ValueError(f"Not a valid ZIP file: {backup_path}")

    download_dir = Path(settings.UPLOAD_FOLDER)
    download_dir.mkdir(parents=True, exist_ok=True)

    loop = asyncio.get_running_loop()
    restored_count = [0]

    def _restore():
        with zipfile.ZipFile(backup_path, 'r') as zf:
            # Security: skip any members with absolute paths or path traversal
            safe_members = [
                m for m in zf.namelist()
                if not os.path.isabs(m) and '..' not in m.split('/')
            ]
            for member in safe_members:
                zf.extract(member, str(download_dir))
                restored_count[0] += 1

    await loop.run_in_executor(None, _restore)
    app_logger.info(f"Restored {restored_count[0]} files from {backup_path}")

    return {'restored_count': restored_count[0], 'path': str(download_dir)}


def get_backup_list() -> List[dict]:
    db = get_db()
    try:
        rows = db.execute(
            "SELECT * FROM backups ORDER BY created_at DESC"
        ).fetchall()
        backups = [row_to_dict(r) for r in rows]

        # Verify files still exist
        for b in backups:
            b['exists'] = os.path.exists(b.get('path', ''))

        return backups
    finally:
        db.close()


def delete_backup(backup_id: str) -> bool:
    db = get_db()
    try:
        row = db.execute("SELECT path FROM backups WHERE id = ?", (backup_id,)).fetchone()
        if row:
            path = row['path']
            if os.path.exists(path):
                os.remove(path)
            db.execute("DELETE FROM backups WHERE id = ?", (backup_id,))
            db.commit()
            return True
        return False
    finally:
        db.close()
