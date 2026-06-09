#!/usr/bin/env python3
"""
ReMarkable integration using rmapi
"""

import json
import os
import re
import subprocess
import time
from pathlib import Path

from config.settings import DEFAULT_RMAPI_PATH, REMARKABLE_FOLDER, RMAPI_TIMEOUT

# Strip the generated filename prefix (dispatch_NNN_, dispatch_website_NNN_,
# dispatch_email_NNN_) so a stored document name reduces to its article title.
_FILENAME_PREFIX_RE = re.compile(r'^dispatch_(?:website_|email_)?\d+_', re.IGNORECASE)


def _normalize_title(text):
    """Lowercase and strip all non-alphanumerics (same scheme as prune_news.py)."""
    return re.sub(r'[^a-z0-9]', '', (text or '').lower())


def _normalize_document_name(name):
    """Reduce a reMarkable document name to a normalized article title.

    rmapi stores the uploaded filename (sans .pdf) as the visible name, so names
    carry our dispatch_NNN_ prefix. Strip the extension and prefix, then normalize.
    """
    stem = Path(name).stem  # drop .pdf if present
    stem = _FILENAME_PREFIX_RE.sub('', stem)
    return _normalize_title(stem)


class ReMarkableManager:
    """Manages ReMarkable device integration via rmapi"""

    def __init__(self, rmapi_path=None):
        self.rmapi_path = os.path.expanduser(rmapi_path or DEFAULT_RMAPI_PATH)
        self.available = False
        self._inventory = None  # set of normalized titles; None = not yet fetched
        self.check_availability()

    def refresh_inventory(self, folder_name=None):
        """Fetch the live document inventory for a folder, normalized for matching.

        Fails open: on any error the inventory is an empty set so document_exists
        never blocks an upload because the check itself broke.
        """
        folder_name = folder_name or REMARKABLE_FOLDER
        self._inventory = set()
        try:
            result = subprocess.run(
                [self.rmapi_path, '-json', 'ls', f'/{folder_name}'],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                print(f"⚠️ Could not list /{folder_name} for dedup: {result.stderr.strip()}")
                return self._inventory

            entries = json.loads(result.stdout)
            for entry in entries:
                if entry.get('type') == 'DocumentType':
                    self._inventory.add(_normalize_document_name(entry.get('name', '')))
            print(f"📋 reMarkable inventory: {len(self._inventory)} documents in /{folder_name}")
        except Exception as e:
            print(f"⚠️ Inventory fetch failed (proceeding without dedup): {e}")
            self._inventory = set()
        return self._inventory

    def document_exists(self, title, folder_name=None):
        """Return True if a document matching `title` is already on the device.

        Matches on normalized title, ignoring the dispatch_NNN_ filename prefix and
        the .pdf extension. Lazily fetches the inventory on first use (cached).
        """
        if self._inventory is None:
            self.refresh_inventory(folder_name)
        normalized = _normalize_title(title)
        if not normalized:
            return False
        return normalized in self._inventory
    
    def check_availability(self):
        """Check if rmapi is available and accessible"""
        try:
            if not os.path.exists(self.rmapi_path):
                print(f"❌ rmapi not found at: {self.rmapi_path}")
                print(f"💡 Please ensure rmapi is installed and the path is correct")
                return False

            # Test rmapi with a simple command
            result = subprocess.run([self.rmapi_path, 'ls'],
                                    capture_output=True, text=True, timeout=30)

            if result.returncode == 0:
                print(f"✅ rmapi is available at: {self.rmapi_path}")
                self.available = True
                return True
            else:
                print(f"❌ rmapi test failed: {result.stderr}")
                print(f"💡 Please ensure rmapi is properly configured and authenticated")
                return False

        except subprocess.TimeoutExpired:
            print("❌ rmapi command timed out")
            return False
        except Exception as e:
            print(f"❌ Error checking rmapi: {e}")
            return False

    def is_available(self):
        """Check if ReMarkable integration is available"""
        return self.available

    def create_folder(self, folder_name):
        """Create a folder on ReMarkable device. Returns True if folder exists/was created."""
        try:
            print(f"📁 Checking/creating folder: {folder_name}")
            mkdir_result = subprocess.run([self.rmapi_path, 'mkdir', folder_name],
                                          capture_output=True, text=True, timeout=30)

            if mkdir_result.returncode == 0:
                # Newly created folder — wait for it to sync to the reMarkable cloud
                # before uploading, otherwise the subsequent put will fail with "not found"
                print(f"⏳ New folder created, waiting 10s for reMarkable cloud sync...")
                time.sleep(10)
                return True

            # Non-zero return is fine if the folder already exists
            error_output = (mkdir_result.stderr + mkdir_result.stdout).lower()
            if "already exists" not in error_output:
                print(f"⚠️ mkdir result: {mkdir_result.stderr}")
                return False

            return True
        except Exception as e:
            print(f"❌ Error creating folder: {e}")
            return False

    def upload_pdf(self, pdf_path, folder_name=None):
        """Upload PDF to ReMarkable using rmapi"""
        if not self.available:
            print("❌ ReMarkable integration not available")
            return False
            
        folder_name = folder_name or REMARKABLE_FOLDER
        
        try:
            pdf_path = Path(pdf_path)
            if not pdf_path.exists():
                print(f"❌ PDF file not found: {pdf_path}")
                return False

            print(f"📤 Uploading {pdf_path.name} to ReMarkable folder: {folder_name}")

            # First, ensure the folder exists
            if not self.create_folder(folder_name):
                print(f"❌ Failed to create/verify folder: {folder_name}")
                return False

            # Upload the file to the specified folder (retry up to 3 times for transient failures)
            upload_cmd = [self.rmapi_path, 'put', str(pdf_path), folder_name]
            print(f"🔧 Running: {' '.join(upload_cmd)}")

            for attempt in range(1, 4):
                result = subprocess.run(upload_cmd, capture_output=True, text=True, timeout=RMAPI_TIMEOUT)
                if result.returncode == 0:
                    print(f"✅ Successfully uploaded {pdf_path.name} to ReMarkable/{folder_name}")
                    return True
                if attempt < 3:
                    print(f"⚠️ Upload attempt {attempt} failed: {result.stderr.strip()} — retrying in 5s...")
                    time.sleep(5)

            print(f"❌ Upload failed after 3 attempts: {result.stderr}")
            print(f"📤 stdout: {result.stdout}")
            return False

        except subprocess.TimeoutExpired:
            print("❌ Upload command timed out")
            return False
        except Exception as e:
            print(f"❌ Error uploading to ReMarkable: {e}")
            return False

    def list_files(self, folder_name=None):
        """List files in a ReMarkable folder"""
        if not self.available:
            print("❌ ReMarkable integration not available")
            return []
            
        try:
            cmd = [self.rmapi_path, 'ls']
            if folder_name:
                cmd.append(folder_name)
                
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                files = result.stdout.strip().split('\n') if result.stdout.strip() else []
                return [f.strip() for f in files if f.strip()]
            else:
                print(f"❌ Failed to list files: {result.stderr}")
                return []
                
        except Exception as e:
            print(f"❌ Error listing files: {e}")
            return []

    def delete_file(self, file_path):
        """Delete a file from ReMarkable device"""
        if not self.available:
            print("❌ ReMarkable integration not available")
            return False
            
        try:
            result = subprocess.run([self.rmapi_path, 'rm', file_path],
                                  capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                print(f"✅ Successfully deleted: {file_path}")
                return True
            else:
                print(f"❌ Failed to delete {file_path}: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"❌ Error deleting file: {e}")
            return False

    def get_device_info(self):
        """Get ReMarkable device information"""
        if not self.available:
            return None
            
        try:
            # Try to get some basic info (this might not work with all rmapi versions)
            result = subprocess.run([self.rmapi_path, 'ls', '/'],
                                  capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                return {
                    'status': 'connected',
                    'rmapi_path': self.rmapi_path,
                    'root_accessible': True
                }
            else:
                return {
                    'status': 'error',
                    'rmapi_path': self.rmapi_path,
                    'root_accessible': False,
                    'error': result.stderr
                }
                
        except Exception as e:
            return {
                'status': 'error',
                'rmapi_path': self.rmapi_path,
                'error': str(e)
            }

    def bulk_upload(self, pdf_files, folder_name=None):
        """Upload multiple PDFs to ReMarkable"""
        if not self.available:
            print("❌ ReMarkable integration not available")
            return []
            
        folder_name = folder_name or REMARKABLE_FOLDER
        successful_uploads = []
        
        print(f"📤 Starting bulk upload of {len(pdf_files)} files to {folder_name}")
        
        # Ensure folder exists
        if not self.create_folder(folder_name):
            print(f"❌ Failed to create/verify folder: {folder_name}")
            return successful_uploads
        
        for i, pdf_path in enumerate(pdf_files, 1):
            print(f"\n📄 Uploading {i}/{len(pdf_files)}: {Path(pdf_path).name}")
            
            if self.upload_pdf(pdf_path, folder_name):
                successful_uploads.append(pdf_path)
            else:
                print(f"❌ Failed to upload: {pdf_path}")
        
        print(f"\n🎉 Bulk upload complete: {len(successful_uploads)}/{len(pdf_files)} successful")
        return successful_uploads

    def print_status(self):
        """Print ReMarkable integration status"""
        print("\n📱 REMARKABLE STATUS")
        print("=" * 40)
        print(f"🔧 rmapi path: {self.rmapi_path}")
        print(f"📶 Available: {'✅ Yes' if self.available else '❌ No'}")
        
        if self.available:
            device_info = self.get_device_info()
            if device_info:
                print(f"🔗 Status: {device_info['status']}")
                if device_info.get('error'):
                    print(f"⚠️ Error: {device_info['error']}")
        
        print("=" * 40)
