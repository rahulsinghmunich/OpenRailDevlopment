#!/usr/bin/env python3
"""
Test script to verify cache busting in the consist editor GUI
"""

import time
import os
from pathlib import Path

def test_cache_busting():
    """Test that the GUI detects file changes"""
    test_file = Path("test_consist.con")

    if not test_file.exists():
        print("Test consist file not found!")
        return

    # Read current content
    with open(test_file, 'r') as f:
        original_content = f.read()

    print(f"Original file content:\n{original_content}")
    print(f"Original modification time: {test_file.stat().st_mtime}")

    # Wait a moment
    time.sleep(1)

    # Modify the file by adding a wagon
    modified_content = original_content.replace(
        '		Wagon (\n			UiD ( 2 )\n			WagonData ( BOXN BOXN )\n		)\n	)\n)',
        '		Wagon (\n			UiD ( 2 )\n			WagonData ( BOXN BOXN )\n		)\n		Wagon (\n			UiD ( 3 )\n			WagonData ( BCN BCN )\n		)\n	)\n)'
    )

    with open(test_file, 'w') as f:
        f.write(modified_content)

    new_mtime = test_file.stat().st_mtime
    print(f"Modified file content:\n{modified_content}")
    print(f"New modification time: {new_mtime}")

    print("\nFile has been modified. Check the GUI to see if it detects the change.")
    print("The GUI should show the new wagon (BCN) in the consist viewer.")

if __name__ == "__main__":
    test_cache_busting()
