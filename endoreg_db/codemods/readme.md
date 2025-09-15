### install
```
uv add bowler fissix
```

### Dry-run (preview only)
```
python endoreg_db/codemods/rename_datetime_fields.py
```

### Run it (models only):
```
python endoreg_db/codemods/rename_datetime_fields.py --yes
```

### Verify what changed
```
git status
git diff --name-only
git diff
```

### Then check for any leftover legacy names in models (excluding migration history):
```
grep -RIn --exclude-dir='migrations' \
  -E '\b(date_created|date_modified|last_update|date_updated|createdOn|updatedOn|modified_at|lastModified)\b' \
  endoreg_db/models || true

grep -RIn --exclude-dir='migrations' -E '\b(date_created|date_modified|last_update|date_updated|createdOn|updatedOn|modified_at|lastModified)\b' endoreg_db/models || true
```

### Fix F(..) expressions (if any)
```
# date_created -> created_at
grep -Rl --exclude-dir='migrations' "F('date_created')" endoreg_db/models | xargs -r sed -i "s/F('date_created')/F('created_at')/g"
grep -Rl --exclude-dir='migrations' 'F("date_created")' endoreg_db/models | xargs -r sed -i 's/F("date_created")/F("created_at")/g'

# date_modified -> updated_at
grep -Rl --exclude-dir='migrations' "F('date_modified')" endoreg_db/models | xargs -r sed -i "s/F('date_modified')/F('updated_at')/g"
grep -Rl --exclude-dir='migrations' 'F("date_modified")' endoreg_db/models | xargs -r sed -i 's/F("date_modified")/F("updated_at")/g'
```


### Example: Fix f-strings / attribute refs (seen in raw_pdf.py, video.py)
```
# self.date_created -> self.created_at
grep -Rl --exclude-dir='migrations' 'self\.date_created' endoreg_db/models | xargs -r sed -i 's/self\.date_created/self.created_at/g'
# self.date_modified -> self.updated_at
grep -Rl --exclude-dir='migrations' 'self\.date_modified' endoreg_db/models | xargs -r sed -i 's/self\.date_modified/self.updated_at/g'
```

### Fix string literals in .values(), .order_by(), dict keys
```
# double-quoted keys
grep -Rl --exclude-dir='migrations' --include="*.py" '"date_created"' endoreg_db/models | xargs -r sed -i 's/"date_created"/"created_at"/g'
grep -Rl --exclude-dir='migrations' --include="*.py" '"date_modified"' endoreg_db/models | xargs -r sed -i 's/"date_modified"/"updated_at"/g'
grep -Rl --exclude-dir='migrations' --include="*.py" '"last_update"'  endoreg_db/models | xargs -r sed -i 's/"last_update"/"updated_at"/g'
grep -Rl --exclude-dir='migrations' --include="*.py" '"date_updated"' endoreg_db/models | xargs -r sed -i 's/"date_updated"/"updated_at"/g'

# single-quoted keys
grep -Rl --exclude-dir='migrations' --include="*.py" "'date_created'" endoreg_db/models | xargs -r sed -i "s/'date_created'/'created_at'/g"
grep -Rl --exclude-dir='migrations' --include="*.py" "'date_modified'" endoreg_db/models | xargs -r sed -i "s/'date_modified'/'updated_at'/g"
grep -Rl --exclude-dir='migrations' --include="*.py" "'last_update'"  endoreg_db/models | xargs -r sed -i "s/'last_update'/'updated_at'/g"
grep -Rl --exclude-dir='migrations' --include="*.py" "'date_updated'" endoreg_db/models | xargs -r sed -i "s/'date_updated'/'updated_at'/g"

# order_by("-...") style
grep -Rl --exclude-dir='migrations' --include="*.py" '"-date_created"' endoreg_db/models | xargs -r sed -i 's/"-date_created"/"-created_at"/g'
grep -Rl --exclude-dir='migrations' --include="*.py" "'-date_created'" endoreg_db/models | xargs -r sed -i "s/'-date_created'/'-created_at'/g"
grep -Rl --exclude-dir='migrations' --include="*.py" '"-date_modified"' endoreg_db/models | xargs -r sed -i 's/"-date_modified"/"-updated_at"/g'
grep -Rl --exclude-dir='migrations' --include="*.py" "'-date_modified'" endoreg_db/models | xargs -r sed -i "s/'-date_modified'/'-updated_at'/g"
```


### Re-scan to confirm
```
grep -RIn --exclude-dir='migrations' \
  -E '\b(date_created|date_modified|last_update|date_updated|createdOn|updatedOn|modified_at|lastModified)\b' \
  endoreg_db/models || true
```


### Migrations
```
python manage.py makemigrations endoreg_db
python manage.py migrate
```


