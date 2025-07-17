#!/usr/bin/env python3
"""
Direct fix for video file path issues without complex Django setup.
"""

import sqlite3
from pathlib import Path
import os
import argparse

def fix_video_paths_direct(db_path, storage_dir):
    """Fix video file paths directly in SQLite database."""
    
    db_path = Path(db_path)
    storage_dir = Path(storage_dir)
    
    print("🔧 DIRECT VIDEO PATH FIX")
    print("=" * 40)
    
    if not db_path.exists():
        print(f"❌ Database not found at {db_path}")
        return
    
    # Find actual video files
    print("1. Scanning for video files...")
    actual_files = {}
    for pattern in ['**/*.mp4', '**/*.avi', '**/*.mov', '**/*.mkv']:
        for file_path in storage_dir.glob(pattern):
            if file_path.is_file() and file_path.stat().st_size > 0:
                filename = file_path.name
                if '_' in filename:
                    uuid_part = filename.split('_')[0]
                else:
                    uuid_part = filename.split('.')[0]
                
                relative_path = file_path.relative_to(storage_dir)
                actual_files[uuid_part] = {
                    'absolute_path': str(file_path),
                    'relative_path': str(relative_path),
                    'size_mb': file_path.stat().st_size / (1024*1024)
                }
    
    print(f"Found {len(actual_files)} video files")
    
    # Connect to database and check video records
    print("\n2. Checking database records...")
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Find video file table (might be different names)
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%video%'")
        tables = cursor.fetchall()
        print(f"Found video tables: {[t[0] for t in tables]}")
        
        # Check for VideoFile records
        video_table = None
        for table in ['endoreg_db_videofile', 'videofile', 'video_file']:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                if count > 0:
                    video_table = table
                    print(f"Using table: {table} ({count} records)")
                    break
            except sqlite3.OperationalError:
                continue
        
        if not video_table:
            print("❌ No video table found in database")
            return
        
        # Get video record 5
        cursor.execute(f"SELECT id, uuid, raw_file FROM {video_table} WHERE id = 5")
        record = cursor.fetchone()
        
        if not record:
            print("❌ Video ID 5 not found in database")
            return
        
        video_id, uuid_str, current_raw_file = record
        print(f"\n3. Video ID 5 details:")
        print(f"   UUID: {uuid_str}")
        print(f"   Current raw_file: {current_raw_file}")
        
        # Check if current file exists
        if current_raw_file:
            current_full_path = storage_dir / current_raw_file
            file_exists = current_full_path.exists()
            print(f"   File exists: {file_exists}")
            if not file_exists:
                print(f"   Full path checked: {current_full_path}")
        
        # Find matching file
        if uuid_str in actual_files:
            file_info = actual_files[uuid_str]
            print(f"\n4. Found matching file:")
            print(f"   Path: {file_info['absolute_path']}")
            print(f"   Size: {file_info['size_mb']:.1f} MB")
            print(f"   Relative path: {file_info['relative_path']}")
            
            if current_raw_file != file_info['relative_path']:
                print(f"\n5. 🔧 FIXING PATH:")
                print(f"   FROM: {current_raw_file}")
                print(f"   TO:   {file_info['relative_path']}")
                
                # Update the database with transaction safety
                try:
                    cursor.execute(f"UPDATE {video_table} SET raw_file = ? WHERE id = ?", 
                                   (file_info['relative_path'], video_id))
                    conn.commit()
                    print("✅ Database updated successfully!")
                except Exception as e:
                    conn.rollback()
                    print(f"❌ Failed to update database: {e}")
                
                print("\n6. 🎯 NEXT STEPS:")
                print("   1. Restart your Django development server")
                print("   2. Test video streaming in the frontend")
                print("   3. Check browser developer tools for any remaining errors")
            else:
                print("\n✅ Path is already correct in database")
        else:
            print(f"\n❌ No matching file found for UUID {uuid_str}")
            print("Available UUIDs in storage:")
            for uuid_key in list(actual_files.keys())[:5]:
                print(f"   - {uuid_key}")
        
        conn.close()
        
    except Exception as e:
        print(f"❌ Database error: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Direct fix for video file path issues in SQLite DB.")
    parser.add_argument('--db-path', type=str, default=os.environ.get('ENDOREG_DB_PATH', './db.sqlite3'),
                        help='Path to the SQLite database file (default: ./db.sqlite3 or $ENDOREG_DB_PATH)')
    parser.add_argument('--storage-dir', type=str, default=os.environ.get('ENDOREG_STORAGE_DIR', './storage'),
                        help='Path to the storage directory (default: ./storage or $ENDOREG_STORAGE_DIR)')
    args = parser.parse_args()
    fix_video_paths_direct(args.db_path, args.storage_dir)