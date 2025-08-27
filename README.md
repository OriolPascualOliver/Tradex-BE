# Tradex Backend

## SQLite Security

- Database path defaults to `~/.tradex/users.db`. Override with `TRADEX_DB_PATH`.
- File and WAL permissions are forced to `600`.
- Demo users are not seeded when `TRADEX_ENV=production`.
- Enable optional SQLCipher support with `TRADEX_USE_SQLCIPHER=1` and provide `TRADEX_DB_KEY`.
- Use `scripts/db_backup.py` for backups and restores.
  - `python scripts/db_backup.py backup /path/to/backup.db`
  - `python scripts/db_backup.py restore /path/to/backup.db`

For environments without SQLCipher, ensure disk-level encryption is enabled.
