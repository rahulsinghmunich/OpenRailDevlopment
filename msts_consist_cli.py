#!/usr/bin/env python3
"""
MSTS Consist Editor CLI - Enhanced Command Line Interface
A command-line wrapper for the consist editor with advanced features.

This provides an interactive CLI that mimics TSRE5 functionality:
- Interactive file/folder selection
- Batch processing options
- Status checking and validation
- Multiple run modes (dry-run, explain, debug)
- Detailed reporting and statistics
"""

import argparse
import sys
import os
from pathlib import Path
import subprocess
import time
import json
import re
from typing import List, Dict, Optional, Tuple, Any
import shutil

class ConsistEditorCLI:
    def __init__(self):
        self.resolver_script = None
        self.consists_dir = None
        self.trainset_dir = None
        self.config = {
            'dry_run': True,
            'explain': False,
            'debug': False,
            'verbose': True
        }
        
        # Find resolver script
        self.find_resolver_script()
        
    def find_resolver_script(self):
        """Find the consistEditor.py script"""
        
        # Check current directory
        current_dir = Path.cwd()
        script_path = current_dir / "consistEditor.py"
        
        if script_path.exists():
            self.resolver_script = str(script_path)
            return
        
        # Check script directory
        if __file__:
            script_dir = Path(__file__).parent
            script_path = script_dir / "consistEditor.py"
            
            if script_path.exists():
                self.resolver_script = str(script_path)
                return
        
        print("WARNING: consistEditor.py not found in current directory")
        print("Please ensure the resolver script is available")
    
    def find_python_executable(self):
        """Find the best Python executable to use"""
        
        # First, check if we're already in a virtual environment
        if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
            return sys.executable
        
        # Look for virtual environment in current directory
        current_dir = Path.cwd()
        for v in ['venv', '.venv', 'env', '.env', 'virtualenv']:
            vp = current_dir / v
            if vp.is_dir():
                py = vp / "Scripts" / "python.exe"
                if not py.exists():
                    py = vp / "bin" / "python"
                if py.exists():
                    return str(py)
        
        # Try to find Python in PATH
        python_in_path = shutil.which('python')
        if python_in_path:
            return python_in_path
        
        # Last resort: use current sys.executable
        return sys.executable
    
    def print_banner(self):
        """Print application banner"""
        
        banner = """
╔══════════════════════════════════════════════════════════════════════════════╗
║                    MSTS Consist Editor CLI - TSRE5 Style                     ║
║                     Enhanced Command Line Interface                          ║
╚══════════════════════════════════════════════════════════════════════════════╝

Features:
• Interactive file/folder selection (like TSRE5)
• Batch processing of consist files
• Asset status checking and validation
• Multiple run modes (dry-run, explain, debug)
• Detailed reporting and statistics
• Single file or whole directory processing

"""
        print(banner)
    
    def interactive_setup(self):
        """Interactive setup process"""
        
        print("=== INTERACTIVE SETUP ===")
        print()
        
        # Get consists directory
        while not self.consists_dir:
            print("1. Consists Directory:")
            print("   Enter the path to your consists directory (containing .con files)")
            
            path = input("   Consists Path: ").strip()
            if not path:
                print("   ERROR: Path cannot be empty")
                continue
            
            path_obj = Path(path)
            if not path_obj.exists():
                print(f"   ERROR: Directory not found: {path}")
                continue
            
            if not path_obj.is_dir():
                print(f"   ERROR: Path is not a directory: {path}")
                continue
            
            # Check for .con files
            con_files = list(path_obj.glob("*.con"))
            if not con_files:
                print(f"   WARNING: No .con files found in {path}")
                confirm = input("   Continue anyway? [y/N]: ").strip().lower()
                if confirm != 'y':
                    continue
            else:
                print(f"   Found {len(con_files)} consist files")
            
            self.consists_dir = str(path_obj)
            print(f"   ✓ Consists directory set: {self.consists_dir}")
            break
        
        print()
        
        # Get trainset directory
        while not self.trainset_dir:
            print("2. Trainset Directory:")
            print("   Enter the path to your trainset directory (containing asset folders)")
            
            path = input("   Trainset Path: ").strip()
            if not path:
                print("   ERROR: Path cannot be empty")
                continue
            
            path_obj = Path(path)
            if not path_obj.exists():
                print(f"   ERROR: Directory not found: {path}")
                continue
            
            if not path_obj.is_dir():
                print(f"   ERROR: Path is not a directory: {path}")
                continue
            
            # Check for asset folders
            asset_folders = [d for d in path_obj.iterdir() if d.is_dir()]
            print(f"   Found {len(asset_folders)} asset folders")
            
            self.trainset_dir = str(path_obj)
            print(f"   ✓ Trainset directory set: {self.trainset_dir}")
            break
        
        print()
        
        # Get run options
        print("3. Run Options:")
        
        dry_run = input("   Dry run (preview only)? [Y/n]: ").strip().lower()
        self.config['dry_run'] = dry_run != 'n'
        
        explain = input("   Show detailed explanations? [y/N]: ").strip().lower()
        self.config['explain'] = explain == 'y'
        
        debug = input("   Enable debug mode? [y/N]: ").strip().lower()
        self.config['debug'] = debug == 'y'
        
        print()
        print("=== SETUP COMPLETE ===")
        print(f"Consists Dir: {self.consists_dir}")
        print(f"Trainset Dir: {self.trainset_dir}")
        print(f"Dry Run: {self.config['dry_run']}")
        print(f"Explain: {self.config['explain']}")
        print(f"Debug: {self.config['debug']}")
        print()
    
    def analyze_consists(self) -> Dict[str, Any]:
        """Analyze consist files and show status"""
        
        print("=== ANALYZING CONSISTS ===")
        
        consists_path = Path(self.consists_dir)
        trainset_path = Path(self.trainset_dir)
        
        # Find all consist files
        consist_files = list(consists_path.glob("*.con"))
        print(f"Found {len(consist_files)} consist files")
        
        total_entries = 0
        missing_assets = []
        existing_assets = []
        broken_consists = []
        
        for consist_file in consist_files:
            print(f"Analyzing: {consist_file.name}")
            
            try:
                entries = self.parse_consist_file(consist_file)
                total_entries += len(entries)
                
                for entry in entries:
                    asset_path = trainset_path / entry['folder'] / f"{entry['name']}.{entry['extension']}"
                    
                    if asset_path.exists():
                        existing_assets.append({
                            'consist': consist_file.name,
                            'entry': entry,
                            'path': str(asset_path)
                        })
                    else:
                        missing_assets.append({
                            'consist': consist_file.name,
                            'entry': entry,
                            'expected_path': str(asset_path)
                        })
                
            except Exception as e:
                print(f"ERROR parsing {consist_file.name}: {str(e)}")
                broken_consists.append({
                    'file': consist_file.name,
                    'error': str(e)
                })
        
        # Summary
        print()
        print("=== ANALYSIS RESULTS ===")
        print(f"Total Consist Files: {len(consist_files)}")
        print(f"Total Asset Entries: {total_entries}")
        print(f"Existing Assets: {len(existing_assets)}")
        print(f"Missing Assets: {len(missing_assets)}")
        print(f"Broken Consists: {len(broken_consists)}")
        
        if missing_assets:
            print()
            print("MISSING ASSETS:")
            for item in missing_assets[:10]:  # Show first 10
                print(f"  • {item['consist']}: {item['entry']['type']} {item['entry']['folder']}/{item['entry']['name']}")
            if len(missing_assets) > 10:
                print(f"  ... and {len(missing_assets) - 10} more")
        
        if broken_consists:
            print()
            print("BROKEN CONSISTS:")
            for item in broken_consists:
                print(f"  • {item['file']}: {item['error']}")
        
        return {
            'total_files': len(consist_files),
            'total_entries': total_entries,
            'existing_assets': len(existing_assets),
            'missing_assets': len(missing_assets),
            'broken_consists': len(broken_consists),
            'missing_list': missing_assets,
            'broken_list': broken_consists
        }
    
    def parse_consist_file(self, file_path: Path) -> List[Dict[str, str]]:
        """Parse a consist file and extract asset entries"""
        
        entries = []
        
        # Try different encodings
        encodings = ['utf-8', 'utf-16', 'cp1252', 'latin-1']
        content = None
        
        for encoding in encodings:
            try:
                content = file_path.read_text(encoding=encoding, errors='ignore')
                break
            except UnicodeDecodeError:
                continue
        
        if content is None:
            raise ValueError("Could not decode file with any known encoding")
        
        # Parse engine entries
        engine_patterns = [
            r'Engine\s*\([^)]*EngineData\s*\(\s*([^\s"]+)\s+"([^"]+)"\s*\)',
            r'EngineData\s*\(\s*([^\s)]+)\s+([^\s)]+)\s*\)'
        ]
        
        for pattern in engine_patterns:
            for match in re.finditer(pattern, content):
                name, folder = match.group(1), match.group(2)
                if not any(e['name'] == name and e['folder'] == folder for e in entries):
                    entries.append({
                        'type': 'Engine',
                        'name': name,
                        'folder': folder,
                        'extension': 'eng'
                    })
        
        # Parse wagon entries
        wagon_patterns = [
            r'Wagon\s*\([^)]*WagonData\s*\(\s*([^\s"]+)\s+"([^"]+)"\s*\)',
            r'WagonData\s*\(\s*([^\s)]+)\s+([^\s)]+)\s*\)'
        ]
        
        for pattern in wagon_patterns:
            for match in re.finditer(pattern, content):
                name, folder = match.group(1), match.group(2)
                if not any(e['name'] == name and e['folder'] == folder for e in entries):
                    entries.append({
                        'type': 'Wagon',
                        'name': name,
                        'folder': folder,
                        'extension': 'wag'
                    })
        
        return entries
    
    def run_resolver(self) -> bool:
        """Run the consist resolver"""
        
        if not self.resolver_script:
            print("ERROR: Resolver script not found!")
            return False
        
        print("=== RUNNING RESOLVER ===")
        
        # Build command - use more robust Python detection
        python_exe = self.find_python_executable()
        cmd = [python_exe, self.resolver_script, self.consists_dir, self.trainset_dir]
        
        if self.config['dry_run']:
            cmd.append('--dry-run')
        if self.config['explain']:
            cmd.append('--explain')
        if self.config['debug']:
            cmd.append('--debug')
        
        print(f"Command: {' '.join(cmd)}")
        print()
        
        try:
            # Run the resolver
            start_time = time.time()
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            end_time = time.time()
            duration = end_time - start_time
            
            print(f"Execution time: {duration:.2f} seconds")
            print(f"Return code: {result.returncode}")
            print()
            
            # Show output
            if result.stdout:
                print("=== RESOLVER OUTPUT ===")
                print(result.stdout)
            
            if result.stderr:
                print("=== ERRORS/WARNINGS ===")
                print(result.stderr)
            
            return result.returncode == 0
            
        except subprocess.TimeoutExpired:
            print("ERROR: Resolver timed out after 5 minutes")
            return False
        except Exception as e:
            print(f"ERROR running resolver: {str(e)}")
            return False
    
    def single_file_mode(self, consist_file: str):
        """Process a single consist file"""
        
        file_path = Path(consist_file)
        if not file_path.exists():
            print(f"ERROR: Consist file not found: {consist_file}")
            return False
        
        # Set consists directory from file path
        self.consists_dir = str(file_path.parent)
        
        print(f"=== SINGLE FILE MODE ===")
        print(f"File: {consist_file}")
        print(f"Consists Dir: {self.consists_dir}")
        
        # Get trainset directory if not set
        if not self.trainset_dir:
            while True:
                path = input("Enter trainset directory path: ").strip()
                if not path:
                    print("ERROR: Path cannot be empty")
                    continue
                
                path_obj = Path(path)
                if not path_obj.exists() or not path_obj.is_dir():
                    print(f"ERROR: Invalid directory: {path}")
                    continue
                
                self.trainset_dir = str(path_obj)
                break
        
        print()
        
        # Analyze single file
        try:
            entries = self.parse_consist_file(file_path)
            print(f"Found {len(entries)} asset entries")
            
            trainset_path = Path(self.trainset_dir)
            missing = 0
            existing = 0
            
            for entry in entries:
                asset_path = trainset_path / entry['folder'] / f"{entry['name']}.{entry['extension']}"
                if asset_path.exists():
                    existing += 1
                else:
                    missing += 1
                    print(f"MISSING: {entry['type']} {entry['folder']}/{entry['name']}")
            
            print(f"Existing: {existing}, Missing: {missing}")
            
        except Exception as e:
            print(f"ERROR analyzing file: {str(e)}")
            return False
        
        # Ask to run resolver
        if missing > 0:
            run_resolver = input(f"\nRun resolver to fix {missing} missing assets? [Y/n]: ").strip().lower()
            if run_resolver != 'n':
                return self.run_resolver()
        else:
            print("All assets found - no resolver needed!")
            return True
        
        return True
    
    def batch_mode(self):
        """Batch processing mode"""
        
        print("=== BATCH MODE ===")
        
        # Analyze all consists
        analysis = self.analyze_consists()
        
        if analysis['missing_assets'] == 0:
            print("All assets found - no resolver needed!")
            return True
        
        # Ask to run resolver
        run_resolver = input(f"\nRun resolver to fix {analysis['missing_assets']} missing assets? [Y/n]: ").strip().lower()
        if run_resolver != 'n':
            return self.run_resolver()
        
        return True
    
    def main_menu(self):
        """Display main menu"""
        
        while True:
            print("\n" + "="*70)
            print("MAIN MENU")
            print("="*70)
            print("1. Interactive Setup")
            print("2. Analyze Consists")
            print("3. Run Resolver")
            print("4. Batch Process")
            print("5. Settings")
            print("6. Help")
            print("7. Exit")
            print()
            
            choice = input("Select option [1-7]: ").strip()
            
            if choice == '1':
                self.interactive_setup()
            elif choice == '2':
                if not self.consists_dir or not self.trainset_dir:
                    print("Please complete interactive setup first")
                    continue
                self.analyze_consists()
            elif choice == '3':
                if not self.consists_dir or not self.trainset_dir:
                    print("Please complete interactive setup first")
                    continue
                self.run_resolver()
            elif choice == '4':
                if not self.consists_dir or not self.trainset_dir:
                    print("Please complete interactive setup first")
                    continue
                self.batch_mode()
            elif choice == '5':
                self.settings_menu()
            elif choice == '6':
                self.show_help()
            elif choice == '7':
                print("Goodbye!")
                break
            else:
                print("Invalid choice. Please try again.")
    
    def settings_menu(self):
        """Settings menu"""
        
        print("\n=== SETTINGS ===")
        print(f"1. Dry Run: {self.config['dry_run']}")
        print(f"2. Explain: {self.config['explain']}")
        print(f"3. Debug: {self.config['debug']}")
        print(f"4. Verbose: {self.config['verbose']}")
        print("5. Reset Paths")
        print("6. Back to Main Menu")
        print()
        
        choice = input("Select option [1-6]: ").strip()
        
        if choice == '1':
            self.config['dry_run'] = not self.config['dry_run']
            print(f"Dry run set to: {self.config['dry_run']}")
        elif choice == '2':
            self.config['explain'] = not self.config['explain']
            print(f"Explain set to: {self.config['explain']}")
        elif choice == '3':
            self.config['debug'] = not self.config['debug']
            print(f"Debug set to: {self.config['debug']}")
        elif choice == '4':
            self.config['verbose'] = not self.config['verbose']
            print(f"Verbose set to: {self.config['verbose']}")
        elif choice == '5':
            self.consists_dir = None
            self.trainset_dir = None
            print("Paths reset. Run interactive setup again.")
        elif choice == '6':
            return
    
    def show_help(self):
        """Show help information"""
        
        help_text = """
=== HELP ===

MSTS Consist Editor CLI - This tool helps you manage MSTS consist files and resolve
missing assets using the advanced consist resolver.

WORKFLOW:
1. Interactive Setup - Set your consists and trainset directories
2. Analyze Consists - Check which assets are missing
3. Run Resolver - Fix missing assets automatically
4. Batch Process - Analyze and resolve in one step

MODES:
• Dry Run - Preview changes without modifying files
• Explain - Show detailed resolution information
• Debug - Enable verbose debugging output

SINGLE FILE MODE:
Use --file <filename> to process a single consist file

COMMAND LINE USAGE:
  python msts_consist_cli.py                    # Interactive mode
  python msts_consist_cli.py --file freight.con # Single file mode
  python msts_consist_cli.py --help            # Show help

REQUIREMENTS:
• consistEditor.py must be in the same directory
• Python 3.6 or later
• Valid MSTS/OR installation with trainset assets

For more information, visit the project documentation.
"""
        print(help_text)

def main():
    """Main entry point"""
    
    parser = argparse.ArgumentParser(
        description="MSTS Consist Editor CLI - TSRE5 Style Interface",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('--file', '-f', type=str, 
                       help='Process a single consist file')
    parser.add_argument('--consists-dir', '-c', type=str,
                       help='Consists directory path')
    parser.add_argument('--trainset-dir', '-t', type=str,
                       help='Trainset directory path')
    parser.add_argument('--dry-run', action='store_true',
                       help='Preview changes only')
    parser.add_argument('--explain', action='store_true',
                       help='Show detailed explanations')
    parser.add_argument('--debug', action='store_true',
                       help='Enable debug mode')
    parser.add_argument('--batch', action='store_true',
                       help='Run batch processing automatically')
    
    args = parser.parse_args()
    
    # Create CLI instance
    cli = ConsistEditorCLI()
    
    # Set configuration from arguments
    if args.dry_run:
        cli.config['dry_run'] = True
    if args.explain:
        cli.config['explain'] = True
    if args.debug:
        cli.config['debug'] = True
    
    # Set paths from arguments
    if args.consists_dir:
        cli.consists_dir = args.consists_dir
    if args.trainset_dir:
        cli.trainset_dir = args.trainset_dir
    
    # Print banner
    cli.print_banner()
    
    try:
        if args.file:
            # Single file mode
            success = cli.single_file_mode(args.file)
            sys.exit(0 if success else 1)
        elif args.batch:
            # Batch mode
            if not cli.consists_dir or not cli.trainset_dir:
                print("ERROR: --batch requires --consists-dir and --trainset-dir")
                sys.exit(1)
            success = cli.batch_mode()
            sys.exit(0 if success else 1)
        else:
            # Interactive mode
            cli.main_menu()
    
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        sys.exit(130)
    except Exception as e:
        print(f"Fatal error: {e}")
        if cli.config.get('debug', False):
            import traceback
            traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()