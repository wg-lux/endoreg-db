#!/run/current-system/sw/bin/bash
# first run chmod +x export_db.sh import_db.sh
# then ./export_db.sh and also to run other file ./import_db.sh
set -e  # stop on first error

echo "Django's dumpdata command looks at  installed models and tries to dump all data."
echo "If any model exists in your codebase but its corresponding table doesn’t exist in the database, it fails, in that case, run "
echo "python manage.py makemigrations endoreg_db python manage.py migrate endoreg_db"

echo "Dumping database to endoreg_db_backup.json..."
python manage.py dumpdata --indent 4 --output=endoreg_db_backup.json

echo "Fixing invalid values..."
python manage.py shell < fix_endoreg_db_backup_json.py

echo "Export completed. Cleaned fixture saved to endoreg_db_backup_fixed.json"
echo "Please check backup file and databse before running load data script as it also removethe current database"

