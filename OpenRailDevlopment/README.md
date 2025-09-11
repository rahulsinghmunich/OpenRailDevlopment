# MSTS Consist Editor - TSRE5 Style Tools

A comprehensive suite of tools for managing MSTS consist files, similar to TSRE5 functionality but focused specifically on consist editing and asset resolution.

## 🚀 Quick Start

### For Windows Users (Easiest):
1. Double-click `run_consist_editor.bat`
2. Choose your preferred interface (GUI recommended)
3. Follow the interactive setup

### For All Platforms:
```bash
# GUI Mode (Recommended)
python msts_consist_editor_gui.py

# CLI Mode (Interactive)
python msts_consist_cli.py

# Direct Command Line
python consistEditor.py [consists_dir] [trainset_dir] [options]
```

## 📋 Features

### ✨ GUI Tool (`msts_consist_editor_gui.py`)
- **TSRE5-style graphical interface**
- **File/folder browsers** for easy selection
- **Visual consist viewer** with asset status
- **Real-time status checking** (Missing/Existing/Resolved)
- **Live output display** during processing
- **All resolver options** (dry-run, explain, debug)
- **Single file or batch processing**

### 🖥️ CLI Tool (`msts_consist_cli.py`) 
- **Interactive command-line interface**
- **Batch processing capabilities**
- **Detailed analysis and reporting**
- **TSRE5-style workflow menus**
- **Asset validation and status checking**
- **Progress tracking and statistics**

### ⚙️ Core Resolver (`consistEditor.py`)
- **Advanced asset resolution engine**
- **Strict attribute locking system**
- **Comprehensive Indian Railways classification**
- **Parallel processing for speed**
- **Detailed logging and debugging**
- **Dry-run mode for safe testing**

## 🗂️ File Structure

```
msts-consist-editor/
├── consistEditor.py              # Core resolver engine
├── msts_consist_editor_gui.py    # GUI interface  
├── msts_consist_cli.py           # CLI interface
├── run_consist_editor.bat        # Windows launcher
├── README.md                     # This file
└── sample_consists/              # Example files
    └── 0-KR-E-FREIGHT-15.con    # Sample consist
```

## 🎯 Usage Examples

### GUI Mode (Recommended)

```bash
python msts_consist_editor_gui.py
```

1. **Select Consists Directory**: Browse to your consists folder
2. **Select Trainset Directory**: Browse to your trainset folder  
3. **Load & Analyze**: See all consist entries with status
4. **Configure Options**: Set dry-run, explain, debug modes
5. **Run Resolver**: Fix missing assets automatically
6. **Review Results**: Check resolved/changed assets

### CLI Interactive Mode

```bash
python msts_consist_cli.py
```

Follow the interactive menus:
1. **Interactive Setup** - Set directories and options
2. **Analyze Consists** - Check asset status
3. **Run Resolver** - Fix missing assets
4. **Batch Process** - Analyze and resolve in one step

### Single File Processing

```bash
# GUI - Browse for single file
python msts_consist_editor_gui.py

# CLI - Direct file processing  
python msts_consist_cli.py --file "freight_train.con"

# Direct resolver
python consistEditor.py consists/ trainset/ --dry-run
```

### Batch Processing

```bash
# CLI batch mode
python msts_consist_cli.py --batch --consists-dir "consists/" --trainset-dir "trainset/"

# With options
python msts_consist_cli.py --batch --consists-dir "consists/" --trainset-dir "trainset/" --dry-run --explain
```

## 🔧 Command Line Options

### Core Resolver Options:
- `--dry-run` - Preview changes without modifying files
- `--explain` - Show detailed resolution information
- `--debug` - Enable verbose debugging output
- `--config` - Use custom configuration file

### CLI-Specific Options:
- `--file FILE` - Process single consist file
- `--consists-dir DIR` - Consists directory path
- `--trainset-dir DIR` - Trainset directory path  
- `--batch` - Run batch processing automatically

## 📊 Understanding the Output

### Asset Status Indicators:
- **🟢 Exists** - Asset found in trainset
- **🔴 Missing** - Asset not found, needs resolution
- **🔵 Resolved** - Asset found by resolver
- **🟡 Changed** - Asset reference updated

### Resolution Phases:
1. **EXACT_NAME** - Perfect name match found
2. **TOKEN_MATCH** - Similar asset found via token matching
3. **SEMANTIC_MATCH** - Semantic similarity matching
4. **DEFAULT_FALLBACK** - Default asset used
5. **UNRESOLVED** - No suitable asset found

### Statistics:
- **Total Processed** - Number of asset references
- **Resolved** - Successfully matched assets
- **Changed** - Asset references updated
- **Unresolved** - Assets that couldn't be matched
- **Already Matching** - Assets already correct

## 🎨 GUI Interface Features

### Main Window Layout:
```
┌─────────────────────────────────────────────────────────────┐
│ File Selection & Controls    │ Consist Viewer              │
│ ┌─────────────────────────┐  │ ┌─────────────────────────┐ │
│ │ Consists Directory      │  │ │ Index │Type │Folder│Name│ │
│ │ Trainset Directory      │  │ │   1   │Eng │ BRW  │WAG7│ │
│ │ Single File (optional)  │  │ │   2   │Wag │ BGP  │BLCA│ │
│ └─────────────────────────┘  │ └─────────────────────────┘ │
│ ┌─────────────────────────┐  │ Total: 50  Missing: 5      │
│ │ ☑ Dry Run              │  │                            │
│ │ ☐ Explain              │  ├────────────────────────────┤
│ │ ☐ Debug                │  │ Output & Status            │
│ └─────────────────────────┘  │ ┌─────────────────────────┐ │
│ [Load & Analyze] [Run]       │ │ [12:34:56] Resolver...  │ │
└─────────────────────────────────────────────────────────────┘
```

### Status Colors:
- **Green** - Existing/Resolved assets
- **Red** - Missing/Unresolved assets  
- **Blue** - Changed asset references
- **Orange** - Warning conditions

## ⚡ Performance Tips

1. **Use GUI for interactive work** - Better visual feedback
2. **Use CLI for batch processing** - More efficient for large datasets
3. **Enable dry-run first** - Always preview changes
4. **Check debug logs** - For troubleshooting resolution issues
5. **Organize trainsets properly** - Correct folder structure improves matching

## 🔍 Troubleshooting

### Common Issues:

**GUI Won't Start:**
```bash
# Check if tkinter is installed
python -c "import tkinter; print('OK')"

# Install tkinter if missing (Ubuntu/Debian)
sudo apt-get install python3-tk
```

**Resolver Script Not Found:**
- Ensure `consistEditor.py` is in the same directory
- Check file permissions
- Verify Python can execute the script

**Assets Not Resolving:**
- Check trainset directory structure
- Verify asset files exist (.eng/.wag files)
- Enable debug mode to see detailed matching
- Check folder names match consist references

**Encoding Issues:**
- Try different text editors to view consist files
- Check for BOM (Byte Order Mark) in files
- Use UTF-8 encoding when editing manually

### Debug Mode Output:
```bash
python consistEditor.py consists/ trainset/ --debug --explain
```
Provides detailed information about:
- Asset classification
- Matching attempts
- Scoring algorithms
- Resolution decisions

## 🛠️ Advanced Configuration

### Custom Configuration (config.json):
```json
{
  "scoring": {
    "exact_match_bonus": 1000,
    "token_match_bonus": 800,
    "class_match_bonus": 300
  },
  "matching": {
    "enable_fuzzy": true,
    "similarity_threshold": 0.75
  }
}
```

### Environment Variables:
- `CONSIST_RESOLVER_VERBOSE=true` - Enable verbose logging
- `MSTS_TRAINSET_PATH` - Default trainset directory
- `MSTS_CONSISTS_PATH` - Default consists directory

## 📝 Sample Workflow

1. **Initial Setup:**
   ```bash
   # Start with GUI for first-time users
   python msts_consist_editor_gui.py
   ```

2. **Directory Selection:**
   - Consists: `C:\Train Simulator\ROUTES\MyRoute\CONSISTS\`
   - Trainsets: `C:\Train Simulator\TRAINS\`

3. **Analysis Phase:**
   - Load consist files
   - Review missing assets list
   - Check asset status indicators

4. **Resolution Phase:**
   - Enable dry-run mode first
   - Run resolver to see proposed changes
   - Review changes in output log
   - Disable dry-run and apply changes

5. **Verification:**
   - Refresh view to see updated status
   - Test consists in game/simulator
   - Review resolver statistics

## 🤝 Contributing

To contribute to this project:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

### Development Setup:
```bash
git clone <repository>
cd msts-consist-editor
python -m pip install -r requirements.txt  # If requirements.txt exists
```

## 📄 License

This project is provided as-is for educational and personal use. Please respect the terms of use for MSTS/OpenRails and any associated content.

## 🔗 Related Projects

- **TSRE5** - The original route editor that inspired this interface
- **OpenRails** - Open source train simulator
- **MSTS** - Microsoft Train Simulator

## 📞 Support

For support and questions:
1. Check this README first
2. Review debug output with `--debug` flag
3. Check issue tracker (if available)
4. Test with sample consist files

---

**Happy Train Simulation! 🚂**