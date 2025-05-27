#!/run/current-system/sw/bin/bash
# -----------------------------------------------------------------------------
# Export Django database to a JSON fixture, excluding system tables
# -----------------------------------------------------------------------------
# Usage:
#   chmod +x export_db.sh import_db.sh
#   ./export_db.sh
# -----------------------------------------------------------------------------

set -e  # Exit immediately if any command fails

# -----------------------------------------------------------------------------
# Step 1: Info for Developers
# -----------------------------------------------------------------------------
echo "────────────────────────────────────────────────────────────"
echo "  Django Fixture Export Script"
echo "────────────────────────────────────────────────────────────"
echo " If you see errors about missing tables, run the following:"
echo "   python manage.py makemigrations endoreg_db"
echo "   python manage.py migrate endoreg_db"
echo ""

# -----------------------------------------------------------------------------
# Step 2: Dumping Data
# -----------------------------------------------------------------------------
echo " Dumping database to 'endoreg_db_backup.json'"
echo "   ➤ Excluding: contenttypes.contenttype, auth.permission"
echo ""

python manage.py dumpdata \
  --exclude contenttypes \
  --exclude auth.permission \
  --indent 4 \
  --output=endoreg_db_backup.json

# -----------------------------------------------------------------------------
# Step 3: Post-process the JSON to fix or clean values
# -----------------------------------------------------------------------------
echo "  Running fix script: fix_endoreg_db_backup_json.py"
echo ""

python manage.py shell < fix_endoreg_db_backup_json.py

# -----------------------------------------------------------------------------
# Step 4: Done
# -----------------------------------------------------------------------------
echo ""
echo " Export completed successfully!"
echo " Cleaned fixture saved to: endoreg_db_backup_fixed.json"
echo " IMPORTANT: Review the backup file and database before using import."
echo "────────────────────────────────────────────────────────────"
