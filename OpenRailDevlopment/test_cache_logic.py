#!/usr/bin/env python3
"""
Simple test to verify cache busting logic works
"""

import sys
import os
from pathlib import Path
import time

# Add current directory to path so we can import the GUI module
sys.path.insert(0, os.getcwd())

# Import the GUI class
from msts_consist_editor_gui import ConsistEditorGUI

def test_cache_logic():
    """Test the cache busting logic without running the full GUI"""

    # Create a mock GUI instance (without actually creating the Tkinter window)
    class MockGUI:
        def __init__(self):
            self._consist_file_mtimes = {}
            self.current_entries = []

        def log_message(self, message, msg_type='log'):
            print(f"[{msg_type}] {message}")

        def parse_consist_file(self, file_path):
            """Mock parse method that returns dummy entries"""
            file_path_obj = Path(file_path).resolve()
            mtime = file_path_obj.stat().st_mtime
            self.log_message(f"Mock parsing: {file_path_obj.name} (mtime: {mtime})")

            # Return some dummy entries based on file content
            entries = []
            with open(file_path, 'r') as f:
                content = f.read()
                if 'WAP4' in content:
                    entries.append({'type': 'Engine', 'name': 'WAP4', 'folder': 'WAP4'})
                if 'BOXN' in content:
                    entries.append({'type': 'Wagon', 'name': 'BOXN', 'folder': 'BOXN'})
                if 'BCN' in content:
                    entries.append({'type': 'Wagon', 'name': 'BCN', 'folder': 'BCN'})
                if 'BTPN' in content:
                    entries.append({'type': 'Wagon', 'name': 'BTPN', 'folder': 'BTPN'})

            return entries

        def analyze_single_consist(self, file_path, force_refresh=False):
            """Copy of the cache busting logic from the GUI"""
            try:
                file_path_obj = Path(file_path).resolve()  # Resolve to absolute path
                file_path_str = str(file_path_obj)  # Use resolved path as key

                # Check if file has been modified since last read
                current_mtime = file_path_obj.stat().st_mtime
                last_mtime = self._consist_file_mtimes.get(file_path_str, 0)

                if force_refresh or current_mtime != last_mtime:
                    self.log_message(f"Analyzing consist file: {file_path_obj.name} (mtime: {current_mtime}, last: {last_mtime})")

                    # Parse the file
                    entries = self.parse_consist_file(str(file_path_obj))

                    # Update modification time cache
                    self._consist_file_mtimes[file_path_str] = current_mtime

                    # Set current entries
                    self.current_entries = entries

                    self.log_message(f"Loaded {len(entries)} entries from {file_path_obj.name}")
                    return True  # File was re-parsed
                else:
                    self.log_message(f"Consist file {file_path_obj.name} unchanged (mtime: {current_mtime}), using cached data", 'status')
                    return False  # Used cached data

            except Exception as e:
                self.log_message(f"Error analyzing consist file: {e}")
                return False

    # Test the cache logic
    gui = MockGUI()
    test_file = "test_consist.con"

    print("=== First load (should parse file) ===")
    result1 = gui.analyze_single_consist(test_file)
    print(f"Result: {'Parsed' if result1 else 'Cached'}")
    print(f"Entries: {len(gui.current_entries)}")

    # Wait a moment
    time.sleep(1)

    print("\n=== Second load (should use cache) ===")
    result2 = gui.analyze_single_consist(test_file)
    print(f"Result: {'Parsed' if result2 else 'Cached'}")
    print(f"Entries: {len(gui.current_entries)}")

    # Modify the file
    print("\n=== Modifying file ===")
    with open(test_file, 'a') as f:
        f.write("\n\t\tWagon (\n\t\t\tUiD ( 5 )\n\t\t\tWagonData ( TEST TEST )\n\t\t)\n")

    print("\n=== Third load (should detect change and parse) ===")
    result3 = gui.analyze_single_consist(test_file)
    print(f"Result: {'Parsed' if result3 else 'Cached'}")
    print(f"Entries: {len(gui.current_entries)}")

    print("\n=== Cache contents ===")
    for path, mtime in gui._consist_file_mtimes.items():
        print(f"  {Path(path).name}: {mtime}")

if __name__ == "__main__":
    test_cache_logic()
