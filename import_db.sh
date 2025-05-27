#!/run/current-system/sw/bin/bash
set -e  # stop on first error

echo " Removing old SQLite DB..."
rm dev_db.sqlite3

echo " Running migrations..."
python manage.py migrate

echo " Fixing fixture (again just in case)..."
python manage.py shell < fix_endoreg_db_backup_json.py

echo " Loading cleaned data into fresh DB..."
python manage.py loaddata endoreg_db_backup_fixed.json

echo " Import complete. Database restored successfully!"
echo " Please check!"

