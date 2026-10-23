# Database backup and recovery

## Use the deployment's backup procedure

Use the backup and recovery procedure defined for the active deployment
environment. Django JSON fixtures do not replace database backups or protected
media recovery. A recoverable system needs coordinated procedures for the
database, protected media, encryption keys, and deployment configuration.

## Legacy fixture scripts

The repository contains `export_db.sh` and `import_db.sh`. They are legacy
scripts and depend on `fix_endoreg_db_backup_json.py`, which is not present in
this checkout. Do not rely on these scripts as a production backup or restore
workflow until that dependency is restored and the procedure is verified.

For storage boundary requirements, see [configuration and deployment
settings](../setup/CONFIGURATION_GUIDE.md) and [data privacy](../setup/HOW_WE_KEEP_DATA_PRIVATE.md).
