### Run it (models only):
python endoreg_db/codemods/rename_datetime_fields.py endoreg_db/models

### Verify what changed
git status
git diff --name-only
git diff

Then check for any leftover legacy names in models (excluding migration history):
grep -RIn --exclude-dir='migrations' -E '\b(date_created|date_modified|last_update|date_updated|createdOn|updatedOn|modified_at|lastModified)\b' endoreg_db/models || true

If the grep above shows hits, they’re usually in things like .values("date_created"), .order_by("-date_created"), dict keys, or admin/serializer field lists. Replace them:
### double-quoted keys
grep -Rl --include="*.py" '"date_created"' endoreg_db/models | xargs sed -i 's/"date_created"/"created_at"/g'
grep -Rl --include="*.py" '"date_modified"' endoreg_db/models | xargs sed -i 's/"date_modified"/"updated_at"/g'
grep -Rl --include="*.py" '"last_update"'  endoreg_db/models | xargs sed -i 's/"last_update"/"updated_at"/g'
grep -Rl --include="*.py" '"date_updated"' endoreg_db/models | xargs sed -i 's/"date_updated"/"updated_at"/g'

### single-quoted keys
grep -Rl --include="*.py" "'date_created'" endoreg_db/models | xargs sed -i "s/'date_created'/'created_at'/g"
grep -Rl --include="*.py" "'date_modified'" endoreg_db/models | xargs sed -i "s/'date_modified'/'updated_at'/g"
grep -Rl --include="*.py" "'last_update'"  endoreg_db/models | xargs sed -i "s/'last_update'/'updated_at'/g"
grep -Rl --include="*.py" "'date_updated'" endoreg_db/models | xargs sed -i "s/'date_updated'/'updated_at'/g"

### order_by("-date_created") style
grep -Rl --include="*.py" '"-date_created"' endoreg_db/models | xargs sed -i 's/"-date_created"/"-created_at"/g'
grep -Rl --include="*.py" "'-date_created'" endoreg_db/models | xargs sed -i "s/'-date_created'/'-created_at'/g"
grep -Rl --include="*.py" '"-date_modified"' endoreg_db/models | xargs sed -i 's/"-date_modified"/"-updated_at"/g'
grep -Rl --include="*.py" "'-date_modified'" endoreg_db/models | xargs sed -i "s/'-date_modified'/'-updated_at'/g"



python manage.py makemigrations endoreg_db

python manage.py migrate

