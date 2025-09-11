# MSTS Consist Editor

A comprehensive tool for editing and resolving MSTS (Microsoft Train Simulator) consist files with OpenRails compatibility.

## 🚀 Quick Start

### Option 1: One-Click Setup (Recommended)
1. **Extract the zip file** to any folder
2. **Double-click `setup.bat`** - Automatic setup (5-10 minutes)
3. **Double-click `run_consist_editor.bat`** - Launch the application

### Option 2: Standalone Executable (No Setup Required)
If available, use the standalone executable:
1. **Extract the zip file**
2. **Double-click `MSTS_Consist_Editor.exe`**
3. **That's it!** No Python or setup required

### Option 3: Manual Setup
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python msts_consist_editor_gui.py
```

## � Deployment Options

### For Distribution:
Choose the best option based on your needs:

#### **Option A: Full Source + Auto-Setup** (Current)
- ✅ **Pros**: Full source code, customizable, auto-setup
- ✅ **Cons**: Requires Python on target computer
- 📁 **Package**: Zip the entire folder (excluding .git, __pycache__)

#### **Option B: Standalone Executable** (Recommended for end-users)
- ✅ **Pros**: No setup required, single file, runs anywhere
- ✅ **Cons**: Larger file size, no source code access
- 📁 **Package**: Just the .exe file + README
- 🛠️ **Build**: Run `build_installer.bat` to create

#### **Option C: Web-Based (Future)**
- ✅ **Pros**: Cross-platform, no installation
- ✅ **Cons**: Requires web server, limited features
- 🛠️ **Tech**: Convert to web app with Flask/Django

## 📋 System Requirements

### For Source Version:
- **Windows 10/11**
- **Python 3.8 or higher**
- **4GB RAM minimum** (8GB recommended)
- **2GB free disk space**

### For Standalone Executable:
- **Windows 7+**
- **No Python required**
- **2GB RAM minimum**
- **500MB free disk space**

## 🛠️ Features

- **GUI Interface**: User-friendly Tkinter-based editor
- **Batch Processing**: Handle multiple consist files simultaneously
- **Asset Resolution**: Automatic detection and replacement of missing assets
- **Progress Indicators**: Real-time feedback for large operations
- **Backup System**: Automatic .bak file creation
- **Error Handling**: Comprehensive error reporting and recovery
- **Cross-Platform**: Works on Windows, Linux, macOS (with Python)

## 📁 Project Structure

```
OpenRailDevlopment/
├── consistEditor.py              # Core resolver engine
├── msts_consist_editor_gui.py    # Main GUI application
├── msts_consist_cli.py          # Command-line interface
├── setup.bat                    # Automated setup script
├── run_consist_editor.bat       # Application launcher
├── build_installer.bat          # Standalone executable builder
├── MSTS_Consist_Editor.spec     # PyInstaller configuration
├── requirements.txt             # Python dependencies
├── engines_store.txt            # Sample engine data
├── wagons_store.txt             # Sample wagon data
├── dist/                        # Built executables (after running build_installer.bat)
│   └── MSTS_Consist_Editor.exe
└── .github/                     # Documentation
```

## 🎯 Usage

### GUI Mode (Recommended):
```bash
run_consist_editor.bat
```
1. Select your **Consists Directory** (folder containing .con files)
2. Select your **Trainset Directory** (folder containing .eng/.wag files)
3. Click **"Load & Analyze Consists"**
4. Review and edit consists in the interface
5. Use **"Run Resolver"** to fix missing assets

### Command Line Mode:
```bash
python msts_consist_cli.py
```

### Direct Resolver:
```bash
python consistEditor.py <consists_dir> <trainset_dir> [options]
```

### Standalone Executable:
```bash
MSTS_Consist_Editor.exe
```

## 🔧 Building Standalone Executable

To create a standalone executable for distribution:

```bash
# Install PyInstaller (one-time)
pip install pyinstaller

# Build the executable
build_installer.bat

# The executable will be created in dist/MSTS_Consist_Editor.exe
```

## 🔧 Troubleshooting

### Common Issues:

**"Python not found"**
- Install Python from https://python.org
- Make sure to check "Add Python to PATH" during installation

**"Virtual environment not found"**
- Run `setup.bat` again
- Check that you have write permissions in the folder

**"Module not found"**
- Run `setup.bat` to reinstall dependencies
- Or manually: `pip install -r requirements.txt`

**Application won't start**
- Check Windows Event Viewer for error details
- Try running as Administrator
- Ensure antivirus isn't blocking the application

### Performance Tips:
- For large consist directories (>100 files), the application shows progress indicators
- Close other applications to free up RAM
- Use SSD storage for better performance

## 📞 Support

If you encounter issues:
1. Check the troubleshooting section above
2. Run `setup.bat` again to ensure proper installation
3. Check the console output for error messages
4. Ensure your consist and trainset directories are accessible

## 📝 Development

### Prerequisites:
- Python 3.8+
- Git

### Setup for Development:
```bash
git clone <repository-url>
cd OpenRailDevlopment
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### Running Tests:
```bash
python -m pytest  # If tests are added
```

## 📝 License

This project is open-source. Feel free to modify and distribute.

---

**Happy Train Simulating! 🚂**
