#!/run/current-system/sw/bin/bash
# -----------------------------------------------------------------------------
# Import Django fixture into a clean SQLite database
# -----------------------------------------------------------------------------
# Usage:
#   chmod +x export_db.sh import_db.sh
#   ./import_db.sh
# -----------------------------------------------------------------------------

set -e  # Exit immediately if any command fails

# -----------------------------------------------------------------------------
# Step 1: Remove old SQLite database
# -----------------------------------------------------------------------------


echo "────────────────────────────────────────────────────────────"
echo "  Removing old SQLite database..."
echo "  Database name: dev_db.sqlite3"
echo ""

rm -f dev_db.sqlite3

# -----------------------------------------------------------------------------
# Step 2: Apply migrations
# -----------------------------------------------------------------------------
echo " Applying migrations..."
python manage.py migrate
echo ""

# -----------------------------------------------------------------------------
# Step 3: Run fix script to clean the fixture (failsafe)
# -----------------------------------------------------------------------------
echo " Re-validating fixture with fix_endoreg_db_backup_json.py..."
python manage.py shell < fix_endoreg_db_backup_json.py
echo ""

# -----------------------------------------------------------------------------
# Step 4: Load data into fresh DB
# -----------------------------------------------------------------------------
echo " Loading cleaned data into fresh database..."
python manage.py loaddata endoreg_db_backup_fixed.json
echo ""

# -----------------------------------------------------------------------------
# Step 5: Done!
# -----------------------------------------------------------------------------
echo " Import complete!"
echo " Database has been restored from: endoreg_db_backup_fixed.json"
echo " Please open the app or inspect the DB to verify the data."
echo "────────────────────────────────────────────────────────────"
