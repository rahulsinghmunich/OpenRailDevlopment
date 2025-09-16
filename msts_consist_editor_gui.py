#!/usr/bin/env python3
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext, simpledialog
import threading, subprocess, sys, os, shutil, queue, time, json, re
from pathlib import Path
from typing import List, Optional, Dict, Any

class ConsistEditorGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("MSTS Consist Editor - TSRE5 Style Tool")
        self.root.geometry("1200x800")

        style = ttk.Style()
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        elif 'alt' in style.theme_names():
            style.theme_use('alt')

        self.colors = {
            'resolved': '#4CAF50',
            'changed': '#2196F3',
            'unresolved': '#F44336',
            'missing': '#FF9800',
            'existing': '#4CAF50',
            'background': '#f0f0f0'
        }

        self.consists_path = tk.StringVar()
        self.trainset_path = tk.StringVar()
        # recent paths persistence (stores last two entries for consists and trainset)
        self._recent_paths_file = Path.home() / '.msts_consist_editor_recent_paths.json'
        self._recent_paths = {'consists': [], 'trainsets': []}
        self.selected_consist = tk.StringVar()
        self.current_entries = []
        self._unsaved_changes = False

        self.store_items = []
        self.filtered_store_items = []
        self.store_search_var = tk.StringVar()
        self._store_cache = None
        self._store_cache_trainset = None
        self._previous_trainset_path = None  # Track previous trainset path

        self.resolver_script_path = None
        self.current_consist_file = None
        self.venv_python_path = None

        self.resolver_progress_var = tk.DoubleVar(value=0.0)
        self.resolver_progress_visible = False
        self.store_progress_var = tk.DoubleVar(value=0.0)
        self._store_progress_visible = False
        self.message_queue = queue.Queue()
        self._consist_errors: Dict[str,str] = {}
        self._tooltip_window = None

        script_dir = Path(__file__).parent if __file__ else Path.cwd()
        potential_script = script_dir / "consistEditor.py"
        if potential_script.exists():
            self.resolver_script_path = str(potential_script)

        # Cache for last consist scan results so filter can be re-applied without re-scanning
        self._last_consist_scan_results = []  # list of tuples (path_str, display_name, missing_count, err)

        self._detect_virtual_environment()

    def _dedupe_consist_scan_results(self, results):
        """Return a de-duplicated list of scan results keeping the last seen entry for each path.

        results: iterable of (path_str, display_name, missing_count, err)
        """
        try:
            seen = {}
            for path_str, display_name, missing_count, err in results:
                try:
                    key = self._normalize_path(path_str)
                except Exception:
                    key = str(path_str)
                seen[key] = (key, display_name, missing_count, err)
            # keep sorted by path for stable ordering
            return [seen[k] for k in sorted(seen.keys())]
        except Exception:
            try:
                return list(results)
            except Exception:
                return []

    def _detect_virtual_environment(self):
        try:
            if hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix):
                self.venv_python_path = sys.executable
                self.log_message("Virtual environment detected and will be used for resolver")
            else:
                script_dir = Path(__file__).parent if __file__ else Path.cwd()
                self.log_message(f"Looking for virtual environment in: {script_dir}")

                # Look for venv in multiple possible locations
                venv_found = False
                for v in ['venv', '.venv', 'env', '.env', 'virtualenv']:
                    vp = script_dir / v
                    self.log_message(f"Checking {v} directory: {vp}")
                    if vp.is_dir():
                        py = vp / "Scripts" / "python.exe"
                        if not py.exists():
                            py = vp / "bin" / "python"
                        if py.exists():
                            self.venv_python_path = str(py)
                            self.log_message(f"Found virtual environment at: {vp}")
                            venv_found = True
                            break
                        else:
                            self.log_message(f"Python executable not found at: {py}")
                    else:
                        self.log_message(f"Directory {v} not found at: {vp}")

                if not venv_found:
                    # Try to find Python in PATH
                    import shutil
                    python_in_path = shutil.which('python')
                    if python_in_path:
                        self.venv_python_path = python_in_path
                        self.log_message(f"Using Python from PATH: {python_in_path}")
                    else:
                        # Last resort: use current sys.executable
                        self.venv_python_path = sys.executable
                        self.log_message(f"No virtual environment found, using current Python: {sys.executable}")
        except Exception as e:
            # Last resort: use current sys.executable
            self.venv_python_path = sys.executable
            self.log_message(f"Error detecting virtual environment: {e}, using current Python: {sys.executable}")
        self.setup_gui()
        self.process_messages()

    def _normalize_path(self, p):
        """Return a stable, absolute, normalized path string for use as cache/tree iids.

        Falls back to os.path.normcase/abspath if Path.resolve() fails.
        """
        try:
            return str(Path(p).resolve())
        except Exception:
            try:
                import os
                return os.path.normcase(os.path.abspath(str(p)))
            except Exception:
                return str(p)

    def setup_gui(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(2, weight=1)

        title_label = ttk.Label(main_frame, text="MSTS Consist Editor - TSRE5 Style", font=('Arial', 16, 'bold'))
        title_label.grid(row=0, column=0, columnspan=3, pady=(0, 20))

        left_panel = ttk.LabelFrame(main_frame, text="File Selection & Controls", padding="10")
        left_panel.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(0, 10))
        self.setup_file_selection(left_panel)
        self.setup_controls(left_panel)

        right_panel = ttk.Frame(main_frame)
        right_panel.grid(row=1, column=1, rowspan=2, sticky=(tk.W, tk.E, tk.N, tk.S))
        right_panel.columnconfigure(0, weight=1)
        right_panel.rowconfigure(1, weight=1)

        viewer_frame = ttk.LabelFrame(right_panel, text="Consist Viewer", padding="10")
        viewer_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 10))
        viewer_frame.columnconfigure(0, weight=1)
        viewer_frame.rowconfigure(0, weight=1)
        self.setup_consist_viewer(viewer_frame)

        output_frame = ttk.LabelFrame(right_panel, text="Output & Status", padding="10")
        output_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        output_frame.columnconfigure(0, weight=1)
        output_frame.rowconfigure(0, weight=1)
        self.setup_output_area(output_frame)

    def setup_file_selection(self, parent):
        ttk.Label(parent, text="Consists Directory:").grid(row=0, column=0, sticky=tk.W, pady=(0, 5))
        c_frame = ttk.Frame(parent); c_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        c_frame.columnconfigure(0, weight=1)
        # Use Combobox so we can show last-used paths as hints while retaining free text
        self.consists_combo = ttk.Combobox(c_frame, textvariable=self.consists_path, values=[], width=40)
        self.consists_combo.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=(0, 5))
        ttk.Button(c_frame, text="Browse", command=self.browse_consists_folder).grid(row=0, column=1)

        ttk.Label(parent, text="Trainset Directory:").grid(row=2, column=0, sticky=tk.W, pady=(0, 5))
        t_frame = ttk.Frame(parent); t_frame.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        t_frame.columnconfigure(0, weight=1)
        # Use Combobox for trainset path as well
        self.trainset_combo = ttk.Combobox(t_frame, textvariable=self.trainset_path, values=[], width=40)
        self.trainset_combo.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=(0, 5))
        ttk.Button(t_frame, text="Browse", command=self.browse_trainset_folder).grid(row=0, column=1)

        load_frame = ttk.Frame(parent); load_frame.grid(row=4, column=0, sticky=(tk.W, tk.E), pady=(10, 0))
        load_frame.columnconfigure(0, weight=1)
        load_frame.columnconfigure(1, weight=0)
        load_frame.columnconfigure(2, weight=0)
        self.load_button = ttk.Button(load_frame, text="Load & Analyze Consists", command=self.load_and_analyze)
        self.load_button.grid(row=0, column=0, pady=10, sticky=(tk.W, tk.E))

        # Consist file filter (All / Broken / No Error) - placed next to Load button for alignment
        self.consist_filter_var = tk.StringVar(value='All')
        ttk.Label(load_frame, text='Show:').grid(row=0, column=1, sticky=tk.W, padx=(6,4))
        self.consist_filter_cb = ttk.Combobox(load_frame, textvariable=self.consist_filter_var, values=['All','Broken','No Error'], state='readonly', width=14)
        self.consist_filter_cb.grid(row=0, column=2, sticky=tk.W)
        self.consist_filter_cb.bind('<<ComboboxSelected>>', lambda e: self._apply_consist_filter())
        # Small status label to show number visible / total
        self.consist_filter_status_var = tk.StringVar(value='')
        # Fixed width label so changes in the counter don't resize the panel
        self.consist_filter_status = ttk.Label(load_frame, textvariable=self.consist_filter_status_var, width=18)
        self.consist_filter_status.grid(row=0, column=3, sticky=tk.W, padx=(8,0))

        files_frame = ttk.LabelFrame(parent, text="Consist Files", padding="6")
        files_frame.grid(row=5, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(10, 0))
        files_frame.columnconfigure(0, weight=1)  # Treeview column
        files_frame.columnconfigure(1, weight=0)  # Vertical scrollbar column
        # Use a Treeview with a fixed width for the filename column so long names
        # don't resize the whole file selection panel. Add a horizontal scrollbar
        # so users can scroll long filenames instead of forcing layout changes.
        self.consist_files_tree = ttk.Treeview(files_frame)
        # Primary text column (#0) shows the filename; keep it fixed width
        self.consist_files_tree.heading('#0', text='Consist File')
        # Keep filename column at a fixed width and do not allow it to stretch
        try:
            self.consist_files_tree.column('#0', width=260, minwidth=120, stretch=False)
        except Exception:
            # Some ttk versions may not support minwidth; fallback to width only
            try:
                self.consist_files_tree.column('#0', width=260, stretch=False)
            except Exception:
                pass
        # Missing count column
        self.consist_files_tree['columns'] = ('missing',)
        self.consist_files_tree.heading('missing', text='Missing')
        self.consist_files_tree.column('missing', width=80, anchor=tk.CENTER, stretch=True)

        # Place tree and scrollbars; reserve a horizontal scrollbar to avoid layout jumps
        self.consist_files_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        files_vscroll = ttk.Scrollbar(files_frame, orient='vertical', command=self.consist_files_tree.yview)
        files_vscroll.grid(row=0, column=1, sticky=(tk.N, tk.S))
        files_hscroll = ttk.Scrollbar(files_frame, orient='horizontal', command=self.consist_files_tree.xview)
        files_hscroll.grid(row=1, column=0, sticky=(tk.W, tk.E))
        self.consist_files_tree.configure(yscrollcommand=files_vscroll.set, xscrollcommand=files_hscroll.set)
        self.consist_files_tree.bind('<<TreeviewSelect>>', self.on_consist_file_selected)
        self.consist_files_tree.tag_configure('missing', foreground=self.colors['missing'])
        self.consist_files_tree.tag_configure('no_missing', foreground=self.colors['existing'])
        self.consist_files_tree.tag_configure('error', foreground='#A52A2A')


    def setup_controls(self, parent):
        ttk.Separator(parent, orient='horizontal').grid(row=6, column=0, sticky=(tk.W, tk.E), pady=20)

        controls_frame = ttk.LabelFrame(parent, text="Resolver Options", padding="10")
        controls_frame.grid(row=7, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        self.dry_run_var = tk.BooleanVar(value=True)
        self.explain_var = tk.BooleanVar(value=False)
        self.debug_var = tk.BooleanVar(value=False)
        self.resolve_mode_var = tk.StringVar(value='selected')
        ttk.Checkbutton(controls_frame, text="Dry Run (Preview only)", variable=self.dry_run_var).grid(row=0, column=0, sticky=tk.W)
        ttk.Checkbutton(controls_frame, text="Explain (Detailed info)", variable=self.explain_var).grid(row=1, column=0, sticky=tk.W)
        ttk.Checkbutton(controls_frame, text="Debug Mode", variable=self.debug_var).grid(row=2, column=0, sticky=tk.W)

        mode_frame = ttk.Frame(controls_frame); mode_frame.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=(10, 0))
        ttk.Label(mode_frame, text="Resolve:").grid(row=0, column=0, sticky=tk.W)
        ttk.Radiobutton(mode_frame, text="Selected file only", variable=self.resolve_mode_var, value='selected').grid(row=0, column=1, sticky=tk.W, padx=(10,0))
        ttk.Radiobutton(mode_frame, text="All files in directory", variable=self.resolve_mode_var, value='all').grid(row=1, column=1, sticky=tk.W, padx=(10,0))

        buttons_frame = ttk.Frame(parent); buttons_frame.grid(row=8, column=0, sticky=(tk.W, tk.E), pady=10)
        buttons_frame.columnconfigure(0, weight=1); buttons_frame.columnconfigure(1, weight=1)
        # Use a fixed-width resolve button so changing its label (or filename) doesn't
        # cause the surrounding layout to jump. Keep text short and stable.
        self.resolve_button = ttk.Button(buttons_frame, text="Resolve Selected File", command=self.run_resolver, state='disabled', width=22)
        self.resolve_button.grid(row=0, column=0, padx=(0,5), pady=2, sticky=(tk.W, tk.E))
        self.refresh_button = ttk.Button(buttons_frame, text="Refresh View", command=self.refresh_consist_view)
        self.refresh_button.grid(row=0, column=1, padx=(5,0), pady=2, sticky=(tk.W, tk.E))
        self.resolve_mode_var.trace_add('write', self._update_resolve_button_text)
        self.refresh_counts_button = ttk.Button(buttons_frame, text="Refresh Counts", command=self.refresh_counts)
        self.refresh_counts_button.grid(row=1, column=0, columnspan=2, pady=(6,0), sticky=(tk.W, tk.E))

        self.scan_status_label = ttk.Label(parent, text='')
        self.scan_status_label.grid(row=10, column=0, sticky=(tk.W), pady=(4,0))
        
        # Add progress bar for consist scanning
        self.consist_scan_progress_var = tk.DoubleVar(value=0.0)
        # Create an orange progressbar style for consist scanning to match 'missing'/orange color
        try:
            s = ttk.Style()
            s.configure('Orange.Horizontal.TProgressbar', background=self.colors.get('missing', '#FF9800'), troughcolor='#e6e6e6')
            pb_style = 'Orange.Horizontal.TProgressbar'
        except Exception:
            pb_style = None
        if pb_style:
            # Use default progressbar length so it doesn't expand; style applied via pb_style
            self.consist_scan_progress = ttk.Progressbar(parent, style=pb_style, orient='horizontal', mode='determinate', variable=self.consist_scan_progress_var)
        else:
            # Use default progressbar length so it doesn't expand
            self.consist_scan_progress = ttk.Progressbar(parent, orient='horizontal', mode='determinate', variable=self.consist_scan_progress_var)
        self.consist_scan_progress_visible = False
        
        self.resolver_progress = ttk.Progressbar(parent, orient='horizontal', length=400, mode='determinate', variable=self.resolver_progress_var)
        # Create a red style for the resolver progress bar (use 'unresolved' color)
        try:
            s2 = ttk.Style()
            s2.configure('Red.Horizontal.TProgressbar', background=self.colors.get('unresolved', '#F44336'), troughcolor='#e6e6e6')
            resolver_pb_style = 'Red.Horizontal.TProgressbar'
        except Exception:
            resolver_pb_style = None
        if resolver_pb_style:
            try:
                # replace resolver_progress with styled progressbar
                self.resolver_progress = ttk.Progressbar(parent, style=resolver_pb_style, orient='horizontal', length=400, mode='determinate', variable=self.resolver_progress_var)
            except Exception:
                pass

    def setup_consist_viewer(self, parent):
        columns = ('Type', 'Folder', 'Name', 'Status')
        self.consist_tree = ttk.Treeview(parent, columns=columns, show='headings', height=15)
        # Make columns adaptive to available space - allow stretching to eliminate white space
        for c, w in [('Type',80), ('Folder',200), ('Name',250), ('Status',100)]:
            try:
                self.consist_tree.column(c, width=w, minwidth=max(60, w//2), stretch=True)
            except Exception:
                # Some ttk versions may not accept minwidth/stretch; fallback to width only
                try:
                    self.consist_tree.column(c, width=w, stretch=True)
                except Exception:
                    self.consist_tree.column(c, width=w)
            self.consist_tree.heading(c, text=c)
        self.consist_tree.tag_configure('missing', foreground=self.colors['missing'])
        self.consist_tree.tag_configure('existing', foreground=self.colors['existing'])
        self.consist_tree.tag_configure('unresolved', foreground=self.colors['unresolved'])
        self.consist_tree.tag_configure('changed', foreground=self.colors['changed'])
        self.consist_tree.tag_configure('unknown', foreground='#666666')

        tree_scroll_v = ttk.Scrollbar(parent, orient='vertical', command=self.consist_tree.yview)
        tree_scroll_h = ttk.Scrollbar(parent, orient='horizontal', command=self.consist_tree.xview)
        self.consist_tree.configure(yscrollcommand=tree_scroll_v.set, xscrollcommand=tree_scroll_h.set)
        self.consist_tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        tree_scroll_v.grid(row=0, column=1, sticky=(tk.N, tk.S))
        tree_scroll_h.grid(row=1, column=0, sticky=(tk.W, tk.E))

        # Configure parent frame columns for proper expansion
        parent.columnconfigure(0, weight=1)  # Treeview column
        parent.columnconfigure(1, weight=0)  # Vertical scrollbar column
        parent.columnconfigure(2, weight=0)  # Stores frame column
        stores_frame = ttk.LabelFrame(parent, text="Stores (Engines / Wagons)", padding="6")
        stores_frame.grid(row=0, column=2, rowspan=4, sticky=(tk.N, tk.S, tk.E), padx=(10,0))

        # Columns in stores_frame: let column 0 stretch for inputs/labels; others fixed
        stores_frame.columnconfigure(0, weight=1)
        stores_frame.columnconfigure(1, weight=0)
        stores_frame.columnconfigure(2, weight=0)
        stores_frame.columnconfigure(3, weight=0)
        stores_frame.rowconfigure(2, weight=1)  # list row expands
        stores_frame.rowconfigure(14, weight=0)  # fixed row for progress bar

        self.store_filter_var = tk.StringVar(value='All')
        self.scan_all_subfolders_var = tk.BooleanVar(value=False)
        self.store_subfolder_var = tk.StringVar(value='')

        self.store_subfolder_cb = ttk.Combobox(stores_frame, textvariable=self.store_subfolder_var, values=[''], state='readonly', width=20)
        self.store_subfolder_cb.grid(row=0, column=2, padx=(6,0), pady=(0,6))
        self.store_subfolder_cb.bind('<<ComboboxSelected>>', lambda e: self.load_store_items())
        self.store_subfolder_cb.configure(postcommand=self.update_store_subfolders)
        self.store_subfolder_cb.bind("<Button-1>", lambda e: self.store_subfolder_cb.event_generate("<Down>") if self.store_subfolder_cb['state'] == 'readonly' else None)

        ttk.Checkbutton(stores_frame, text='Scan all top-level subfolders', variable=self.scan_all_subfolders_var, 
                        command=lambda: (self.load_store_items(), self._update_folder_dropdown_state())).grid(row=0, column=3, padx=(6,0))

        # Add explanatory text for folder dropdown - directly below the checkbox
        ttk.Label(stores_frame, text='(Folder selection only works when unchecked)', 
                 font=('', 8), foreground='green').grid(row=1, column=3, sticky=tk.W, pady=(0,6))

        store_filter = ttk.Combobox(stores_frame, textvariable=self.store_filter_var, values=['All','Engines','Wagons'], state='readonly', width=12)
        store_filter.grid(row=0, column=0, columnspan=2, pady=(0,6))
        store_filter.bind('<<ComboboxSelected>>', lambda e: self.load_store_items())

        ttk.Label(stores_frame, text='Search:').grid(row=1, column=0, sticky=tk.W, pady=(6,0))
        search_entry = ttk.Entry(stores_frame, textvariable=self.store_search_var, width=20)
        search_entry.grid(row=1, column=1, columnspan=2, sticky=(tk.W, tk.E), pady=(6,0))
        search_entry.bind('<KeyRelease>', lambda e: self._filter_store_items())
        search_entry.bind('<FocusOut>', lambda e: self._filter_store_items())

        # Dedicated subframe for list + scrollbar to eliminate misalignment
        list_area = ttk.Frame(stores_frame)
        list_area.grid(row=2, column=0, columnspan=3, sticky=(tk.N, tk.S, tk.E, tk.W))
        list_area.columnconfigure(0, weight=1)  # list stretches
        list_area.columnconfigure(1, weight=0)  # scrollbar fixed
        list_area.rowconfigure(0, weight=1)

        self.store_listbox = tk.Listbox(list_area, height=20, exportselection=False)  # no width param
        self.store_listbox.grid(row=0, column=0, sticky=(tk.N, tk.S, tk.E, tk.W))
        store_scroll = ttk.Scrollbar(list_area, orient='vertical', command=self.store_listbox.yview)
        store_scroll.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.store_listbox.configure(yscrollcommand=store_scroll.set)

        self.store_message_label = ttk.Label(stores_frame, text='')
        self.store_message_label.grid(row=3, column=0, columnspan=3, pady=(6,0))

        ttk.Button(stores_frame, text='Refresh Stores', command=self._refresh_store_cache).grid(row=6, column=0, columnspan=2, pady=(8,0))

        self.store_scan_label_var = tk.StringVar(value='')
        ttk.Label(stores_frame, textvariable=self.store_scan_label_var, width=30).grid(row=15, column=0, columnspan=3, pady=(6,0))

        # Add progress bar for store scanning with purple style
        try:
            s = ttk.Style()
            s.configure('Purple.Horizontal.TProgressbar', background='#800080', troughcolor='#e6e6e6')
            store_pb_style = 'Purple.Horizontal.TProgressbar'
        except Exception:
            store_pb_style = None
        try:
            self.store_progress = ttk.Progressbar(stores_frame, style='Purple.Horizontal.TProgressbar', orient='horizontal', length=200, mode='determinate', variable=self.store_progress_var)
        except Exception:
            # Fallback if style doesn't exist
            self.store_progress = ttk.Progressbar(stores_frame, orient='horizontal', length=200, mode='determinate', variable=self.store_progress_var)
        # Initially hide the progress bar
        self.store_progress.place(relx=0, rely=1.0, anchor='sw', relwidth=1.0, height=20)
        self.store_progress.place_forget()
        self._store_progress_visible = False

        ttk.Label(stores_frame, text='Num to add:').grid(row=4, column=0, sticky=tk.W, pady=(6,0))
        self.add_number_var = tk.StringVar(value='1')
        ttk.Entry(stores_frame, textvariable=self.add_number_var, width=6).grid(row=4, column=1, sticky=tk.W, pady=(6,0))

        btn_frame = ttk.Frame(stores_frame)
        btn_frame.grid(row=5, column=0, columnspan=2, pady=(8,0))
        ttk.Button(btn_frame, text='Add Beg', command=lambda: self.insert_store_item('beg')).grid(row=0, column=0, padx=2, pady=2)
        ttk.Button(btn_frame, text='Add Cur', command=lambda: self.insert_store_item('cur')).grid(row=0, column=1, padx=2, pady=2)
        ttk.Button(btn_frame, text='Add End', command=lambda: self.insert_store_item('end')).grid(row=0, column=2, padx=2, pady=2)
        ttk.Button(btn_frame, text='Add N', command=lambda: self.insert_store_item('at')).grid(row=0, column=3, padx=2, pady=2)

        ttk.Label(stores_frame, text='Replace with:').grid(row=9, column=0, sticky=tk.W, pady=(6,0))
        self.store_replace_var = tk.StringVar()
        self.store_replace_cb = ttk.Combobox(stores_frame, textvariable=self.store_replace_var, state='readonly', width=30)
        self.store_replace_cb.grid(row=9, column=1, columnspan=2, sticky=(tk.W, tk.E), pady=(6,0))
        self.store_replace_cb['values'] = []

        action_frame = ttk.Frame(stores_frame)
        action_frame.grid(row=11, column=0, columnspan=3, pady=(8,0))
        ttk.Button(action_frame, text='Move Up', command=self.move_selected_up).grid(row=0, column=0, padx=2, pady=2)
        ttk.Button(action_frame, text='Move Down', command=self.move_selected_down).grid(row=0, column=1, padx=2, pady=2)
        ttk.Button(action_frame, text='Replace', command=self.replace_selected_with).grid(row=0, column=2, padx=2, pady=2)
        self.save_button = ttk.Button(action_frame, text="Save As", command=self.save_current_consist, state='disabled')
        self.save_button.grid(row=0, column=3, padx=2, pady=2)
        self.delete_button = ttk.Button(action_frame, text="Delete", command=self.delete_selected_entry, state='disabled')
        self.delete_button.grid(row=0, column=4, padx=2, pady=2)

        try:
            self.load_store_items()
            self.update_store_subfolders()
        except Exception:
            pass

        # Initialize folder dropdown state based on checkbox
        try:
            self._update_folder_dropdown_state()
        except Exception:
            pass

        # Load recent paths (non-blocking and tolerant)
        try:
            self._load_recent_paths()
        except Exception:
            pass

        try:
            self._trainset_update_after_id = None
            def _debounced_update(*args):
                try:
                    if getattr(self, '_trainset_update_after_id', None):
                        self.root.after_cancel(self._trainset_update_after_id)
                    def _delayed():
                        self.load_store_items()
                        self.update_store_subfolders()
                    self._trainset_update_after_id = self.root.after(300, _delayed)
                except Exception:
                    try:
                        self.load_store_items(); self.update_store_subfolders()
                    except Exception:
                        pass
            self.trainset_path.trace_add('write', _debounced_update)
        except Exception:
            try:
                self.update_store_subfolders()
            except Exception:
                pass

        self.status_frame = ttk.Frame(parent)
        self.status_frame.grid(row=2, column=0, columnspan=2, sticky=(tk.W, tk.E), pady=(10, 0))
        self.status_labels = {}
        col = 0
        for s in ['Total','Missing','Resolved','Changed']:
            lbl = ttk.Label(self.status_frame, text=f"{s}: 0")
            lbl.grid(row=0, column=col, padx=10)
            self.status_labels[s.lower()] = lbl
            col += 1

        missing_frame = ttk.LabelFrame(parent, text="Missing Items (Selected Consist)", padding="6")
        missing_frame.grid(row=3, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(10, 0))
        missing_frame.columnconfigure(0, weight=1)
        self.missing_text = scrolledtext.ScrolledText(missing_frame, height=8, wrap=tk.WORD)
        self.missing_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.missing_text.insert(tk.END, 'Select a consist file to view missing items.')
        self.missing_text.config(state='disabled')

    def setup_output_area(self, parent):
        self.output_text = scrolledtext.ScrolledText(parent, height=20, wrap=tk.WORD)
        self.output_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.log_message("MSTS Consist Editor Tool - Ready")
        self.log_message("Select consists directory and trainset directory, then click 'Load & Analyze'")
        if not self.resolver_script_path:
            self.log_message("WARNING: consistEditor.py not found in current directory")
            self.log_message("Please ensure the resolver script is available")
        if self.venv_python_path != sys.executable:
            self.log_message("Virtual environment detected - resolver will use venv Python")
        else:
            # Check if we're currently running in a venv or using system Python
            import os
            system_python = os.path.join(os.path.dirname(sys.executable), 'python.exe')
            if 'venv' in sys.executable.lower() or '.venv' in sys.executable.lower():
                self.log_message("Running in virtual environment - resolver will use venv Python")
            else:
                self.log_message("Using system Python for resolver (no virtual environment detected)")

    def browse_consists_folder(self):
        folder = filedialog.askdirectory(title="Select Consists Directory")
        if folder:
            self.consists_path.set(folder)
            self.log_message(f"Consists directory set to: {folder}")
            try:
                self._add_recent_path('consists', folder)
                self._refresh_recent_comboboxes()
            except Exception:
                pass

    def browse_trainset_folder(self):
        folder = filedialog.askdirectory(title="Select Trainset Directory")
        if folder:
            self.trainset_path.set(folder)
            self.log_message(f"Trainset directory set to: {folder}")
            try:
                self._add_recent_path('trainsets', folder)
                self._refresh_recent_comboboxes()
            except Exception:
                pass
            try:
                self.load_store_items()
            except Exception as e:
                self.log_message(f"Error loading store items: {e}")
            try:
                self.update_store_subfolders()
            except Exception:
                pass
            self.update_missing_items_display()

    # ---------------- Core actions (unchanged logic) ----------------
    # The rest of the methods (load_and_analyze, parse_consist_file, analyze_single_consist,
    # store scanning, filtering, insert/replace/move/delete, resolver integration, etc.)
    # remain identical to the previous version, except geometry changes above.
    # For brevity in this excerpt, keep your prior implementations of those methods.
    # ----------------------------------------------------------------

    # From here down, re-use your previous implementations without layout changes.
    # ... paste all remaining methods from your original file unchanged ...

    # Due to message length constraints, keep your existing implementations for:
    # load_and_analyze, save_current_consist, parse_consist_file, analyze_single_consist,
    # load_store_items, _filter_store_items, _update_store_listbox, _load_store_items_bg,
    # _refresh_store_cache, update_store_subfolders, _update_replace_combobox,
    # move_selected_up, move_selected_down, replace_selected_with, insert_store_item,
    # refresh_consist_tree_from_current_entries, delete_selected_entry, update_status_summary,
    # update_missing_items_display, refresh_consist_view, refresh_counts, process_messages,
    # log_message, _on_tree_motion, _hide_tooltip, run_resolver and helpers,
    # _refresh_single_file_missing_count, _update_resolve_button_text, on_consist_file_selected.

    # The run() method is defined later in the file

    
    def load_and_analyze(self):
        """Load and analyze consist files"""
        
        consists_dir = self.consists_path.get()
        
        if not consists_dir:
            messagebox.showerror("Error", "Please select a consists directory")
            return
        
        # Process directory
        consists_path = Path(consists_dir)
        if not consists_path.exists():
            messagebox.showerror("Error", f"Consists directory not found: {consists_dir}")
            return
        
        consist_files = list(consists_path.glob("*.con"))
        if not consist_files:
            messagebox.showwarning("Warning", f"No .con files found in: {consists_dir}")
            return
        
        self.log_message(f"Found {len(consist_files)} consist files in: {consists_dir}")
        
        # Show initial scan message with file count
        if len(consist_files) > 20:
            self.log_message(f"Scanning {len(consist_files)} consist files - this may take a moment...")
        
        # Populate the consist files list with missing counts asynchronously
        # Clear current list
        self.consist_files_tree.delete(*self.consist_files_tree.get_children())

        def worker(files):
            # signal scan start
            self.message_queue.put(('scan_start', None))
            results = []
            total_files = len(files)
            
            for i, cf in enumerate(files, 1):
                # Skip any backup files created by Save As (e.g., file.con.bak)
                try:
                    if str(cf).lower().endswith('.bak') or cf.name.lower().endswith('.bak'):
                        continue
                except Exception:
                    pass
                # Send progress update for large scans (every 10 files to reduce overhead)
                if total_files > 20 and (i % 10 == 0 or i == total_files):  # Only show detailed progress for very large scans
                    self.message_queue.put(('consist_scan_progress', (i, total_files)))
                
                missing_count = 0
                err = None
                try:
                    entries = self.parse_consist_file(str(cf))
                    if self.trainset_path.get():
                        trainset_path = Path(self.trainset_path.get())
                        for e in entries:
                            asset_path = trainset_path / e['folder'] / f"{e['name']}.{e['extension']}"
                            if not asset_path.exists():
                                missing_count += 1
                except Exception as ex:
                    missing_count = -1
                    err = str(ex)

                results.append((str(cf), cf.name, missing_count, err))

            # Send results to main thread via message queue and signal scan done
            # store results in message so main thread can cache and filter
            # Filter out any results that are backup files (safety)
            try:
                filtered_results = [r for r in results if not (str(r[0]).lower().endswith('.bak') or str(r[1]).lower().endswith('.bak'))]
            except Exception:
                filtered_results = results
            self.message_queue.put(('consist_list_update', filtered_results))
            self.message_queue.put(('scan_done', None))

        threading.Thread(target=worker, args=(consist_files,), daemon=True).start()

        # Analyze the first file by default once worker populates the tree; as quick fallback, analyze immediately
        if consist_files:
            try:
                first_file = str(consist_files[0])
                self.analyze_single_consist(first_file)
                # Update missing items display for the first file
                self.update_missing_items_display(first_file)
            except Exception:
                pass
        
        # Update resolver button text based on current mode
        self._update_resolve_button_text()
        
        # Enable resolver button if we have paths set up
        if self.consists_path.get() and (self.trainset_path.get() or self.resolver_script_path):
            self.resolve_button.config(state='normal')
        # Persist recent paths on successful load
        try:
            if consists_dir:
                self._add_recent_path('consists', consists_dir)
            tpath = self.trainset_path.get()
            if tpath:
                self._add_recent_path('trainsets', tpath)
            self._refresh_recent_comboboxes()
        except Exception:
            pass

        # Refresh store subfolders and items after loading consists
        try:
            self.update_store_subfolders()
            self.load_store_items()
        except Exception as e:
            self.log_message(f"Store update error: {e}")

    # ---------- Recent paths persistence helpers ----------
    def _load_recent_paths(self):
        try:
            if self._recent_paths_file.exists():
                with open(self._recent_paths_file, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                    if isinstance(data, dict):
                        self._recent_paths.update({k: v for k, v in data.items() if k in self._recent_paths})
        except Exception:
            # ignore errors here
            pass
        # Populate comboboxes
        self._refresh_recent_comboboxes()

    def _save_recent_paths(self):
        try:
            # Keep only last 2 of each list to limit size
            data = {
                'consists': self._recent_paths.get('consists', [])[:2],
                'trainsets': self._recent_paths.get('trainsets', [])[:2]
            }
            with open(self._recent_paths_file, 'w', encoding='utf-8') as fh:
                json.dump(data, fh, indent=2)
        except Exception:
            pass

    def _add_recent_path(self, kind: str, path: str):
        if kind not in ('consists', 'trainsets'):
            return
        lst = self._recent_paths.setdefault(kind, [])
        # Normalize
        p = str(path)
        if p in lst:
            lst.remove(p)
        lst.insert(0, p)
        # Trim to 5 internally but persist only 2
        self._recent_paths[kind] = lst[:5]
        self._save_recent_paths()

    def _refresh_recent_comboboxes(self):
        try:
            # Update combobox values while preserving current text
            cvals = self._recent_paths.get('consists', [])[:2]
            tvals = self._recent_paths.get('trainsets', [])[:2]
            if hasattr(self, 'consists_combo'):
                cur = self.consists_path.get()
                self.consists_combo['values'] = cvals
                # If entry empty and we have a recent value, set the first one as hint (do not override user's typed value)
                if not cur and cvals:
                    self.consists_path.set(cvals[0])
            if hasattr(self, 'trainset_combo'):
                cur2 = self.trainset_path.get()
                self.trainset_combo['values'] = tvals
                if not cur2 and tvals:
                    self.trainset_path.set(tvals[0])
        except Exception:
            pass

    def save_current_consist(self):
        """Save current_entries to a user-specified .con file while preserving original structure BUT removing corruption."""
        try:
            if not self.current_entries:
                messagebox.showwarning('Warning', 'No entries to save')
                return
            
            # Get target file path
            sel = self.consist_files_tree.selection()
            if sel:
                file_path = sel[0]
            else:
                file_path = filedialog.asksaveasfilename(
                    defaultextension='.con', 
                    filetypes=[('Consist files', '*.con'), ('All files','*.*')]
                )
                if not file_path:
                    return
            
            # Handle existing file confirmation and backup
            target_exists = Path(file_path).exists()
            if target_exists:
                if not messagebox.askyesno('Confirm Save', f"Save changes to existing file?\n{file_path}"):
                    return
                
                # Create backup
                try:
                    bak_path = str(file_path) + '.bak'
                    shutil.copy(file_path, bak_path)
                    self.log_message(f"Backup created: {bak_path}")
                except Exception as ex:
                    self.log_message(f"Warning: failed to create backup: {ex}")
            
            # Read and preserve original file structure
            original_content = None
            original_encoding = 'utf-8'
            original_line_ending = '\n'
            
            if target_exists:
                try:
                    # Detect encoding and line endings
                    with open(file_path, 'rb') as bf:
                        raw_data = bf.read()
                    
                    # Detect encoding
                    if raw_data.startswith(b'\xff\xfe'):
                        original_encoding = 'utf-16le'
                        original_content = raw_data.decode('utf-16le')
                    elif raw_data.startswith(b'\xfe\xff'):
                        original_encoding = 'utf-16be'
                        original_content = raw_data.decode('utf-16be')
                    else:
                        original_encoding = 'utf-8'
                        try:
                            original_content = raw_data.decode('utf-8')
                        except UnicodeDecodeError:
                            original_content = raw_data.decode('utf-8', errors='ignore')
                    
                    # Detect line endings
                    if '\r\n' in original_content:
                        original_line_ending = '\r\n'
                    elif '\r' in original_content:
                        original_line_ending = '\r'
                    else:
                        original_line_ending = '\n'
                        
                except Exception as ex:
                    self.log_message(f"Error reading original file: {ex}")
                    original_content = None
            
            # Generate new content preserving structure
            if original_content:
                # Clean and preserve original structure, removing any corruption
                new_content = self._clean_and_preserve_structure(original_content, self.current_entries, original_line_ending)
            else:
                # Create new file with standard MSTS structure
                new_content = self._generate_new_consist_content(self.current_entries, '\r\n')
                original_encoding = 'utf-16le'
                original_line_ending = '\r\n'
            
            # Write the file with original encoding and line endings
            try:
                if original_encoding.startswith('utf-16'):
                    # Write as UTF-16 with BOM
                    with open(file_path, 'wb') as f:
                        if original_encoding == 'utf-16le':
                            f.write(new_content.encode('utf-16le'))
                        else:
                            f.write(new_content.encode('utf-16be'))
                else:
                    # Write as UTF-8
                    with open(file_path, 'w', encoding='utf-8', newline='') as f:
                        f.write(new_content)
                        
            except Exception as e:
                self.log_message(f"Error writing file: {e}")
                raise
            
            # Update internal state
            self._unsaved_changes = False
            try:
                self.save_button.config(state='disabled')
            except Exception:
                pass
            
            self.log_message(f"Saved consist to: {file_path}")
            
            # Update cached results and refresh UI
            self._update_cached_scan_results(file_path)
            self._refresh_ui_after_save(file_path)
            
        except Exception as e:
            self.log_message(f"Error saving consist: {e}")

    def _clean_and_preserve_structure(self, original_content, entries, line_ending):
        """
        Clean the file by removing corruption while preserving original structure.
        Keeps all valid content and removes only truly corrupted Engine/Wagon blocks.
        """
        lines = original_content.splitlines()
        result_lines = []
        entry_index = 0
        inside_train = False
        inside_traincfg = False
        train_depth = 0
        traincfg_depth = 0

        i = 0
        while i < len(lines):
            line = lines[i]
            line_stripped = line.strip().lower()

            # Track Train block entry
            if line_stripped.startswith('train ('):
                inside_train = True
                train_depth = 1
                result_lines.append(line)
                i += 1
                continue
            
            # Track TrainCfg block entry
            if inside_train and 'traincfg (' in line_stripped:
                inside_traincfg = True
                traincfg_depth = 1
                result_lines.append(line)
                i += 1
                continue            # Update depths for current line
            if inside_train:
                train_depth += line.count('(') - line.count(')')
            if inside_traincfg:
                traincfg_depth += line.count('(') - line.count(')')

            # Check if we've exited TrainCfg
            if inside_traincfg and traincfg_depth <= 0:
                inside_traincfg = False
                result_lines.append(line)  # Add the closing TrainCfg line
                i += 1
                continue

            # Check if we've exited Train
            if inside_train and train_depth <= 0:
                inside_train = False
                result_lines.append(line)  # Add the closing Train line
                i += 1
                continue

            # Process content based on location
            if inside_traincfg:
                # Inside TrainCfg - process Engine/Wagon blocks and keep other content
                if line_stripped.startswith('engine (') or line_stripped.startswith('wagon ('):
                    # Extract complete block
                    block_lines, block_end_index = self._extract_complete_block(lines, i)

                    if entry_index < len(entries):
                        # Update block with new data
                        updated_block = self._update_block_preserve_structure(block_lines, entries[entry_index])
                        result_lines.extend(updated_block)
                        entry_index += 1
                    else:
                        # Keep original block if no more entries
                        result_lines.extend(block_lines)

                    i = block_end_index + 1
                else:
                    # Keep all other lines inside TrainCfg (parameters, comments, etc.)
                    result_lines.append(line)
                    i += 1

            elif inside_train:
                # Inside Train but outside TrainCfg - keep all structure lines
                result_lines.append(line)
                i += 1

            else:
                # Outside Train block - keep ALL content (headers, comments, metadata, etc.)
                result_lines.append(line)
                i += 1

        # Add any remaining entries that weren't processed
        if entry_index < len(entries):
            # Find the TrainCfg closing parenthesis to insert before it
            traincfg_close_index = -1
            depth = 0
            in_traincfg = False

            for idx, line in enumerate(result_lines):
                line_lower = line.strip().lower()
                if 'traincfg(' in line_lower:
                    in_traincfg = True
                    depth = 1
                    continue

                if in_traincfg:
                    depth += line.count('(') - line.count(')')
                    if depth <= 0:
                        traincfg_close_index = idx
                        break

            if traincfg_close_index > 0:
                # Insert new entries before the TrainCfg closing
                new_entries = []
                for idx in range(entry_index, len(entries)):
                    new_block = self._create_new_block_matching_style(
                        entries[idx],
                        idx,
                        result_lines
                    )
                    new_entries.extend(new_block)

                # Insert the new entries before TrainCfg close
                result_lines[traincfg_close_index:traincfg_close_index] = new_entries

        return line_ending.join(result_lines)

    def _extract_complete_block(self, lines, start_index):
        """Extract a complete Engine/Wagon block, preserving exact formatting."""
        block_lines = []
        paren_depth = 0
        i = start_index
        
        while i < len(lines):
            line = lines[i]
            block_lines.append(line)
            
            # Count parentheses to find block end
            open_parens = line.count('(')
            close_parens = line.count(')')
            paren_depth += open_parens - close_parens
            
            if paren_depth == 0 and i > start_index:
                break
            i += 1
        
        return block_lines, i

    def _update_block_preserve_structure(self, block_lines, entry):
        """Update Engine/Wagon block data while preserving ALL original formatting."""
        result = []
        has_flip = False
        
        for line in block_lines:
            line_lower = line.lower().strip()
            
            # Update EngineData or WagonData line
            if 'enginedata (' in line_lower or 'wagondata (' in line_lower:
                # Preserve exact indentation and formatting style
                indent = line[:len(line) - len(line.lstrip())]
                
                # Determine if it's Engine or Wagon
                is_engine = entry.get('type', '').lower().startswith('e')
                data_type = 'EngineData' if is_engine else 'WagonData'
                
                name = entry.get('name', '')
                folder = entry.get('folder', '')
                
                # Parse the original line to preserve formatting
                import re
                # Match the original format: EngineData/WagonData ( name folder ) or EngineData/WagonData ( name "folder" )
                match = re.search(r'(EngineData|WagonData)\s*\(\s*([^)]+)\s*\)', line, re.IGNORECASE)
                if match:
                    original_content = match.group(2).strip()
                    # Check if the original had quotes around folder
                    if '"' in original_content:
                        # Original had quotes, preserve them
                        result.append(f'{indent}{data_type} ( {name} "{folder}" )')
                    else:
                        # Original didn't have quotes, don't add them
                        result.append(f'{indent}{data_type} ( {name} {folder} )')
                else:
                    # Fallback to quoted format
                    result.append(f'{indent}{data_type} ( {name} "{folder}" )')
            
            # Handle Flip() line
            elif 'flip (' in line_lower:
                has_flip = True
                if entry.get('flip', False):
                    result.append(line)  # Keep existing Flip line
                # If flip is False, skip this line
            
            # Keep all other lines exactly as they are
            else:
                result.append(line)
        
        # Add Flip() if needed and not present
        if entry.get('flip', False) and not has_flip:
            # Find appropriate insertion point
            insert_index = len(result) - 1
            for i, line in enumerate(result):
                if 'uid (' in line.lower():
                    insert_index = i
                    break
            
            # Get indentation from context
            indent = '\t\t\t'
            if insert_index > 0:
                prev_line = result[insert_index - 1]
                indent = prev_line[:len(prev_line) - len(prev_line.lstrip())]
            
            result.insert(insert_index, f'{indent}Flip ( )')
        
        return result

    def _create_new_block_matching_style(self, entry, uid, existing_lines):
        """Create new Engine/Wagon block matching existing style."""
        # Detect indentation patterns
        base_indent = '\t\t'
        block_indent = '\t\t\t'
        
        # Find actual indentation from existing blocks
        for line in existing_lines:
            line_lower = line.lower().strip()
            if line_lower.startswith('engine(') or line_lower.startswith('wagon('):
                base_indent = line[:len(line) - len(line.lstrip())]
                break
        
        for line in existing_lines:
            line_lower = line.lower().strip()
            if 'enginedata(' in line_lower or 'wagondata(' in line_lower:
                block_indent = line[:len(line) - len(line.lstrip())]
                break
        
        is_engine = entry.get('type', '').lower().startswith('e')
        block_type = 'Engine' if is_engine else 'Wagon'
        data_type = 'EngineData' if is_engine else 'WagonData'
        
        name = entry.get('name', '')
        folder = entry.get('folder', '')
        flip = entry.get('flip', False)
        
        # Build block
        new_block = []
        new_block.append(f'{base_indent}{block_type} (')
        new_block.append(f'{block_indent}{data_type} ( {name} "{folder}" )')
        
        if flip:
            new_block.append(f'{block_indent}Flip ( )')
        
        new_block.append(f'{block_indent}UiD ( {uid} )')
        new_block.append(f'{base_indent})')
        
        return new_block

    def _generate_new_consist_content(self, entries, line_ending):
        """Generate completely new .con file."""
        content_lines = []
        
        # MSTS header
        content_lines.append('SIMISA@@@@@@@@@@JINX0D0t______')
        content_lines.append('')
        content_lines.append('Train (')
        content_lines.append('\tTrainCfg ( "Generated Consist"')
        content_lines.append('\t\tSerial ( 1 )')
        content_lines.append('\t\tMaxVelocity ( 38.88889 0.39338 )')
        content_lines.append(f'\t\tNextWagonUID ( {len(entries)} )')
        content_lines.append('\t\tDurability ( 1.00000 )')
        
        # Add entries
        for i, entry in enumerate(entries):
            is_engine = entry.get('type', '').lower().startswith('e')
            block_type = 'Engine' if is_engine else 'Wagon'
            data_type = 'EngineData' if is_engine else 'WagonData'
            
            name = entry.get('name', '')
            folder = entry.get('folder', '')
            flip = entry.get('flip', False)
            uid = entry.get('uid', i)
            
            content_lines.append(f'\t\t{block_type} (')
            content_lines.append(f'\t\t\t{data_type} ( {name} "{folder}" )')
            
            if flip:
                content_lines.append('\t\t\tFlip ( )')
            
            content_lines.append(f'\t\t\tUiD ( {uid} )')
            content_lines.append('\t\t)')
        
        content_lines.append('\t)')
        content_lines.append(')')
        
        return line_ending.join(content_lines)

    def _update_cached_scan_results(self, file_path):
        """Update cached scan results."""
        try:
            entries = self.parse_consist_file(file_path)
            missing_count = 0
            
            if self.trainset_path.get():
                trainset_path = Path(self.trainset_path.get())
                for e in entries:
                    asset_path = trainset_path / e['folder'] / f"{e['name']}.{e['extension']}"
                    if not asset_path.exists():
                        missing_count += 1
            
            err = None
        except Exception as ex:
            missing_count = -1
            err = str(ex)
        
        # Update cached results
        try:
            lst = list(getattr(self, '_last_consist_scan_results', []) or [])
            updated = []
            found = False
            display_name = Path(file_path).name
            
            for path_str, dname, mc, er in lst:
                if str(path_str) == str(file_path):
                    updated.append((str(file_path), display_name, missing_count, err))
                    found = True
                else:
                    updated.append((path_str, dname, mc, er))
            
            if not found:
                updated.append((str(file_path), display_name, missing_count, err))
            
            try:
                self._last_consist_scan_results = self._dedupe_consist_scan_results(updated)
            except Exception:
                self._last_consist_scan_results = updated
        except Exception:
            pass

    def _refresh_ui_after_save(self, file_path):
        """Refresh UI after save."""
        try:
            self._populate_consist_files_tree()
        except Exception:
            pass
        
        try:
            saved_folder = str(Path(file_path).parent)
            if saved_folder and hasattr(self, 'consists_path') and self.consists_path.get() != saved_folder:
                self._add_recent_path('consists', saved_folder)
                if hasattr(self, 'consists_combo'):
                    vals = self.consists_combo['values'] or []
                    if saved_folder not in vals:
                        newvals = [saved_folder] + list(vals)
                        self.consists_combo['values'] = newvals[:2]
                    self.consists_path.set(saved_folder)
        except Exception:
            pass
        
        try:
            saved_path = str(file_path)
            if hasattr(self, 'consist_files_tree'):
                if self.consist_files_tree.exists(saved_path):
                    self.consist_files_tree.selection_set(saved_path)
                else:
                    self._populate_consist_files_tree()
                    if self.consist_files_tree.exists(saved_path):
                        self.consist_files_tree.selection_set(saved_path)
        except Exception:
            pass
        
        try:
            self.current_consist_file = saved_path
            self.analyze_single_consist(saved_path)
            self.update_missing_items_display(saved_path)
        except Exception:
            pass
    
    def parse_consist_file(self, file_path):
        """Parse consist file and extract entries"""
        
        entries = []
        
        try:
            # Try to detect BOM first to pick correct encoding (many .con files are UTF-16)
            content = None
            try:
                with open(file_path, 'rb') as bf:
                    raw = bf.read()
                # UTF-16 LE BOM
                if raw.startswith(b"\xff\xfe"):
                    try:
                        content = raw.decode('utf-16')
                    except Exception:
                        content = raw.decode('utf-16-le', errors='ignore')
                # UTF-16 BE BOM
                elif raw.startswith(b"\xfe\xff"):
                    try:
                        content = raw.decode('utf-16')
                    except Exception:
                        content = raw.decode('utf-16-be', errors='ignore')
                else:
                    # Fall back to trying common encodings
                    encodings = ['utf-8', 'cp1252', 'latin-1']
                    for encoding in encodings:
                        try:
                            content = raw.decode(encoding)
                            break
                        except Exception:
                            continue
            except Exception:
                content = None
            
            if content is None:
                # Fallback: try a permissive decode to salvage text (may mangle characters)
                try:
                    # Re-read raw bytes if not available
                    try:
                        raw
                    except NameError:
                        with open(file_path, 'rb') as bf:
                            raw = bf.read()
                    content = raw.decode('latin-1', errors='replace')
                    # Log a warning so user/diagnostics can see that fallback was used
                    try:
                        self.log_message(f"Warning: Could not decode {file_path} with standard encodings; used latin-1 fallback (replace)")
                    except Exception:
                        pass
                except Exception:
                    raise ValueError("Could not decode file with any known encoding")
            
            # Simple regex-based parsing for Engine and Wagon entries.
            # Many consist files include lines like:
            #   Engine( ... EngineData(NAME "FOLDER") ... )
            #   Wagon( ... WagonData(NAME "FOLDER") ... )
            # But variants exist: different spacing, commas, or EngineData/WagonData on their own line.

            # Try several regex patterns to be robust against formatting differences.
            patterns = [
                # EngineData/WagonData alone with quoted folder
                (r'(?:EngineData|WagonData)\s*\(\s*([^\s\)]+)\s*"([^"]+)"\s*\)', None, 1, 2),
                # EngineData/WagonData without quotes: name folder
                (r'(?:EngineData|WagonData)\s*\(\s*([^\s\)]+)\s+([^"\s\)]+)\s*\)', None, 1, 2),
                # EngineData/WagonData with parentheses around name: (NAME) "FOLDER"
                (r'(?:EngineData|WagonData)\s*\(\s*\(\s*([^\s\)]+)\s*\)\s+"([^"]+)"\s*\)', None, 1, 2),
            ]

            # Collect all matches with their positions
            all_matches = []
            for pat, kind_group, name_group, folder_group in patterns:
                for match in re.finditer(pat, content, flags=re.IGNORECASE | re.DOTALL):
                    all_matches.append((match.start(), match, kind_group, name_group, folder_group))
            
            # Sort matches by position in the file
            all_matches.sort(key=lambda x: x[0])
            
            # Collect UiD matches - be specific to avoid matching NextWagonUID
            # Only match UiD that appears after Engine( or Wagon( and before EngineData/WagonData
            uid_pattern = r'(?:Engine\s*\(|Wagon\s*\().*?UiD\s*\(\s*(\d+)\s*\)'
            uid_matches = list(re.finditer(uid_pattern, content, re.IGNORECASE | re.DOTALL))
            
            # Process matches in correct order
            for idx, (start_pos, match, kind_group, name_group, folder_group) in enumerate(all_matches):
                try:
                    if kind_group is not None:
                        kind = match.group(kind_group)
                    else:
                        # determine kind by finding the nearest enclosing 'Engine (' or 'Wagon ('
                        # Search backwards from the match start for the last occurrence of these tokens
                        search_span = content[max(0, match.start() - 400):match.start()]
                        last_engine = search_span.rfind('Engine (')
                        last_wagon = search_span.rfind('Wagon (')
                        if last_engine == -1 and last_wagon == -1:
                            # fallback to simple context keyword search
                            ctx = search_span.lower()
                            kind = 'engine' if 'engine' in ctx else 'wagon' if 'wagon' in ctx else 'Wagon'
                        else:
                            kind = 'Engine' if last_engine > last_wagon else 'Wagon'

                    name = match.group(name_group).strip().strip('"')
                    folder = match.group(folder_group).strip().strip('"')
                    entry_type = 'Engine' if kind.lower().startswith('e') else 'Wagon'
                    # Get UID - ensure we don't accidentally use NextWagonUID
                    uid = str(idx)  # Default fallback
                    if idx < len(uid_matches):
                        uid_value = uid_matches[idx].group(1)
                        # Additional safety check - UID should be a reasonable number (not too large)
                        try:
                            uid_num = int(uid_value)
                            if 0 <= uid_num <= 10000:  # Reasonable UID range
                                uid = uid_value
                        except ValueError:
                            pass  # Keep default

                    # Check for Flip in this Engine/Wagon block
                    flip = False
                    # Find the start of this Engine/Wagon block
                    # Look backwards from the current position to find the block start
                    search_text = content[max(0, start_pos - 500):start_pos]
                    
                    # Find the last Engine or Wagon before this position
                    engine_pos = search_text.rfind('Engine (')
                    wagon_pos = search_text.rfind('Wagon (')
                    
                    if engine_pos > wagon_pos:
                        block_start = max(0, start_pos - 500) + engine_pos
                    elif wagon_pos >= 0:
                        block_start = max(0, start_pos - 500) + wagon_pos
                    else:
                        block_start = start_pos - 100  # fallback

                    # Find the end of this block (next Engine/Wagon or end of content)
                    next_engine = content.find('Engine (', start_pos)
                    next_wagon = content.find('Wagon (', start_pos)
                    
                    if next_engine == -1 and next_wagon == -1:
                        block_end = len(content)
                    elif next_engine == -1:
                        block_end = next_wagon
                    elif next_wagon == -1:
                        block_end = next_engine
                    else:
                        block_end = min(next_engine, next_wagon)

                    search_area = content[block_start:block_end]
                    if re.search(r'\bFlip\s*\(\s*\)', search_area, re.IGNORECASE):
                        flip = True

                    # Keep ALL entries (including duplicates) since a consist can have multiple instances of the same wagon
                    entries.append({
                        'type': entry_type,
                        'name': name,
                        'folder': folder,
                        'extension': 'eng' if entry_type == 'Engine' else 'wag',
                        'uid': uid,
                        'flip': flip
                    })
                except Exception:
                    continue

            # Fallback: scan lines for simple patterns like 'WagonData(NAME FOLDER)'
            if not entries:
                for line in content.splitlines():
                    line = line.strip()
                    # Skip comments
                    if not line or line.startswith('//') or line.startswith('#'):
                        continue
                    m = re.search(r'(EngineData|WagonData)\s*\(\s*([^\s\)]+)\s+"?([^"\)]+)"?\s*\)', line, flags=re.IGNORECASE)
                    if m:
                        entry_type = 'Engine' if m.group(1).lower().startswith('e') else 'Wagon'
                        name = m.group(2).strip().strip('"')
                        folder = m.group(3).strip().strip('"')
                        # Keep ALL entries (including duplicates) since a consist can have multiple instances of the same wagon
                        entries.append({
                            'type': entry_type,
                            'name': name,
                            'folder': folder,
                            'extension': 'eng' if entry_type == 'Engine' else 'wag',
                            'uid': str(len(entries)),  # Fallback UID
                            'flip': False  # Fallback flip
                        })

        except Exception as e:
            self.log_message(f"Error parsing consist file: {str(e)}")
            raise
        
        # Note: Entries are already in the correct order from re.finditer
        # No reordering needed as the regex finds matches in file order

        return entries

    def analyze_single_consist(self, file_path):
        """Parse a single consist file and populate the main consist tree"""
        try:
            self.log_message(f"Analyzing consist file: {file_path}")
            
            # Parse the file
            entries = self.parse_consist_file(file_path)
            
            # Set current entries
            self.current_entries = entries
            
            # Refresh the main consist tree
            self.refresh_consist_tree_from_current_entries()
            
            # Update status
            self.update_status_summary()
            
            # Enable save button
            try:
                self.save_button.config(state='normal')
            except Exception:
                pass
            
            # Store the current file path for saving
            self.current_consist_file = file_path
            
            self.log_message(f"Loaded {len(entries)} entries from {Path(file_path).name}")
            
        except Exception as e:
            self.log_message(f"Error analyzing consist file: {e}")
            messagebox.showerror("Error", f"Failed to analyze consist file:\n{str(e)}")

    def load_store_items(self):
        """Load store items from trainset folder or fallback store files into the listbox."""
        # Clear previous items
        try:
            self.store_items.clear()
        except Exception:
            self.store_items = []

        try:
            self.store_listbox.delete(0, tk.END)
        except Exception:
            pass

        store_filter = self.store_filter_var.get() if hasattr(self, 'store_filter_var') else 'All'

        # Prefer scanning trainset folder if set. Use cache when available for the same trainset path.
        ts = self.trainset_path.get()
        if ts:
            try:
                # Check if trainset path has changed - if so, clear cache
                if self._previous_trainset_path != ts:
                    self._clear_store_cache()
                    self._previous_trainset_path = ts

                # Determine selected immediate subfolder (may be empty)
                sub = ''
                try:
                    sub = self.store_subfolder_var.get() if hasattr(self, 'store_subfolder_var') else ''
                except Exception:
                    sub = ''

                # Cache key includes selected subfolder, scan-all flag, and filter so scans differ
                scan_all = bool(self.scan_all_subfolders_var.get()) if hasattr(self, 'scan_all_subfolders_var') else False
                cache_key = f"{ts}::{sub}::all={int(scan_all)}::filter={store_filter}"

                # If cache exists and trainset+subfolder unchanged, reuse
                if self._store_cache is not None and self._store_cache_trainset == cache_key:
                    self.store_items = list(self._store_cache)
                else:
                    ts_path = Path(ts)
                    # If user selected an immediate subfolder, use it as the scan base
                    if sub:
                        ts_path = ts_path / sub

                    # Update scan label to show what is being scanned
                    try:
                        if hasattr(self, 'store_scan_label_var'):
                            if sub:
                                self.store_scan_label_var.set(f"Scanning: {sub} (top-level)")
                            else:
                                self.store_scan_label_var.set('Scanning: top-level')
                    except Exception:
                        pass

                    if ts_path.exists():
                        # If scanning all subfolders and there are many children, run in background
                        scan_all = bool(self.scan_all_subfolders_var.get()) if hasattr(self, 'scan_all_subfolders_var') else False
                        if scan_all:
                            # launch background worker to populate store_items and update listbox when done
                            threading.Thread(target=self._load_store_items_bg, args=(ts, store_filter, cache_key), daemon=True).start()
                            # Clear scanning message since background scanning will handle progress
                            try:
                                if hasattr(self, 'store_scan_label_var'):
                                    self.store_scan_label_var.set('')
                            except Exception:
                                pass
                            # Update message to indicate scanning is in progress
                            try:
                                self.store_message_label.config(text='Scanning trainset...')
                            except Exception:
                                pass
                            return
                        patterns = []
                        if store_filter in ('All', 'Engines'):
                            patterns.append('*.eng')
                        if store_filter in ('All', 'Wagons'):
                            patterns.append('*.wag')

                        if scan_all:
                            # iterate immediate subdirectories and collect their top-level files
                            for child in sorted(Path(ts).iterdir()):
                                if not child.is_dir():
                                    continue
                                for pat in patterns:
                                    for p in child.glob(pat):
                                        if p.parent != child:
                                            continue
                                        rel = p.relative_to(Path(ts))
                                        folder = str(rel.parent).replace('\\', '/') if rel.parent != Path('.') else ''
                                        name = p.stem
                                        ext = p.suffix.lstrip('.')
                                        display = f"{folder}/{name}.{ext}" if folder else f"{name}.{ext}"
                                        item = {'display': display, 'folder': folder, 'name': name, 'extension': ext}
                                        self.store_items.append(item)
                        else:
                            for pat in patterns:
                                for p in ts_path.glob(pat):
                                    # top-level only (no recursion)
                                    if p.parent != ts_path:
                                        continue
                                    rel = p.relative_to(ts_path)
                                    # if user explicitly selected a subfolder, use that as the folder name
                                    if sub:
                                        folder = sub
                                    else:
                                        folder = str(rel.parent).replace('\\', '/') if rel.parent != Path('.') else ''
                                    name = p.stem
                                    ext = p.suffix.lstrip('.')
                                    display = f"{folder}/{name}.{ext}" if folder else f"{name}.{ext}"
                                    item = {'display': display, 'folder': folder, 'name': name, 'extension': ext}
                                    self.store_items.append(item)

                        # cache results (keyed by trainset+subfolder+scan_all flag)
                        self._store_cache = list(self.store_items)
                        self._store_cache_trainset = cache_key
            except Exception:
                pass

        # If no trainset or nothing found, leave list empty (no fallback to .txt files per request)

        # Initialize filtered items and populate listbox
        self.filtered_store_items = self.store_items
        # Apply any existing search filter
        self._filter_store_items()
        # Ensure combobox is updated
        self._update_replace_combobox()

        # Update message label
        try:
            if not self.trainset_path.get():
                self.store_message_label.config(text='Select a trainset directory to populate the stores.')
            elif not self.store_items:
                self.store_message_label.config(text='No assets found in the selected trainset.')
            else:
                self.store_message_label.config(text='')
        except Exception:
            pass

        # Clear scanning message after scanning is complete
        try:
            if hasattr(self, 'store_scan_label_var'):
                self.store_scan_label_var.set('')
        except Exception:
            pass

    def _load_store_items_bg(self, ts, store_filter, cache_key):
        """Background method to load store items from all subfolders."""
        try:
            # Update progress bar to show scanning is in progress
            self.root.after(0, lambda: self.store_progress_var.set(10))

            ts_path = Path(ts)
            patterns = []
            if store_filter in ('All', 'Engines'):
                patterns.append('*.eng')
            if store_filter in ('All', 'Wagons'):
                patterns.append('*.wag')

            temp_items = []
            total_dirs = 0
            processed_dirs = 0

            # Count total directories for progress
            for child in ts_path.iterdir():
                if child.is_dir():
                    total_dirs += 1

            # Update progress bar
            self.root.after(0, lambda: self.store_progress_var.set(20))

            # Scan all subdirectories
            for child in sorted(ts_path.iterdir()):
                if not child.is_dir():
                    continue

                processed_dirs += 1
                progress = 20 + (processed_dirs / total_dirs) * 70 if total_dirs > 0 else 20
                self.root.after(0, lambda p=progress: self.store_progress_var.set(p))

                for pat in patterns:
                    for p in child.glob(pat):
                        if p.parent != child:
                            continue
                        rel = p.relative_to(ts_path)
                        folder = str(rel.parent).replace('\\', '/') if rel.parent != Path('.') else ''
                        name = p.stem
                        ext = p.suffix.lstrip('.')
                        display = f"{folder}/{name}.{ext}" if folder else f"{name}.{ext}"
                        item = {'display': display, 'folder': folder, 'name': name, 'extension': ext}
                        temp_items.append(item)

            # Update progress to 90%
            self.root.after(0, lambda: self.store_progress_var.set(90))

            # Update the main thread with results
            self.root.after(0, lambda: self._finish_bg_store_load(temp_items, cache_key))

        except Exception as e:
            # Handle errors in background thread
            self.root.after(0, lambda: self._handle_bg_store_error(str(e)))

    def _finish_bg_store_load(self, items, cache_key):
        """Finish background store loading by updating UI with results."""
        try:
            self.store_items = items

            # Cache results
            self._store_cache = list(self.store_items)
            self._store_cache_trainset = cache_key

            # Update progress to 100%
            self.store_progress_var.set(100)

            # Initialize filtered items and populate listbox
            self.filtered_store_items = self.store_items
            self._filter_store_items()
            self._update_replace_combobox()

            # Update message label
            if not self.store_items:
                self.store_message_label.config(text='No assets found in the selected trainset.')
            else:
                self.store_message_label.config(text='')

            # Clear scanning message
            if hasattr(self, 'store_scan_label_var'):
                self.store_scan_label_var.set('')

            # Reset progress after a short delay
            self.root.after(1000, lambda: [self.store_progress_var.set(0), self._hide_store_progress()])

        except Exception as e:
            self._handle_bg_store_error(str(e))

    def _handle_bg_store_error(self, error_msg):
        """Handle errors that occur during background store loading."""
        try:
            self.store_progress_var.set(0)
            if hasattr(self, 'store_scan_label_var'):
                self.store_scan_label_var.set('')
            self.store_message_label.config(text=f'Error scanning trainset: {error_msg}')
        except Exception:
            pass

    def _clear_store_cache(self):
        """Completely clear the store cache - use when trainset changes or major updates needed."""
        try:
            self._store_cache = None
            self._store_cache_trainset = None
            self.store_items = []
            self.filtered_store_items = []
            # Clear the listbox
            try:
                self.store_listbox.delete(0, tk.END)
            except Exception:
                pass
            # Update combobox
            try:
                self._update_replace_combobox()
            except Exception:
                pass
            self.log_message('Store cache cleared')
        except Exception as e:
            self.log_message(f'Error clearing store cache: {e}')

    def _update_folder_dropdown_state(self):
        """Enable/disable folder dropdown based on scan_all_subfolders checkbox state."""
        try:
            if hasattr(self, 'scan_all_subfolders_var') and hasattr(self, 'store_subfolder_cb'):
                scan_all = bool(self.scan_all_subfolders_var.get())
                if scan_all:
                    self.store_subfolder_cb.config(state='disabled')
                    self.store_subfolder_var.set('')
                else:
                    self.store_subfolder_cb.config(state='readonly')
                    # Refresh subfolder list when enabling
                    self.update_store_subfolders()
        except Exception as e:
            print(f"Error updating folder dropdown state: {e}")

    def _hide_store_progress(self):
        """Hide the store progress bar smoothly without layout shifts."""
        try:
            if self._store_progress_visible:
                self.store_progress.place_forget()
                self._store_progress_visible = False
        except Exception:
            pass

    def _filter_store_items(self):
        """Filter store items based on search text and update the listbox."""
        search_text = self.store_search_var.get().lower().strip()
        
        if not search_text:
            # No search text, show all items
            self.filtered_store_items = self.store_items
        else:
            # Split search text by spaces and check that ALL terms are present (AND logic)
            search_terms = search_text.split()
            
            def matches_all_terms(display_name):
                """Check if display name contains all search terms."""
                display_lower = display_name.lower()
                return all(term in display_lower for term in search_terms)
            
            # Filter items based on all search terms matching display names
            self.filtered_store_items = [
                item for item in self.store_items 
                if matches_all_terms(item['display'])
            ]
        
        # Update the listbox with filtered items
        self._update_store_listbox()

    def _update_store_listbox(self):
        """Update the store listbox with current filtered items."""
        try:
            # Clear the listbox
            self.store_listbox.delete(0, tk.END)
            
            # Populate with filtered items
            for item in self.filtered_store_items:
                self.store_listbox.insert(tk.END, item['display'])
            
            # Force UI update to prevent misalignment
            self.root.update_idletasks()
            
            # Update the replace combobox
            self._update_replace_combobox()
        except Exception:
            pass

    def _update_replace_combobox(self):
        """Update the replace combobox values from current filtered store_items."""
        try:
            if not hasattr(self, 'store_replace_cb') or not self.store_replace_cb:
                return
                
            vals = [it['display'] for it in self.filtered_store_items]
            self.store_replace_cb['values'] = vals
            if vals:
                self.store_replace_var.set(vals[0])
            else:
                self.store_replace_var.set('')
                self.store_replace_cb['values'] = ['(No items available)']
            # Force UI update
            self.root.update_idletasks()
        except Exception as e:
            print(f"Error updating replace combobox: {e}")
            try:
                self.store_replace_cb['values'] = ['(Error loading items)']
                self.store_replace_var.set('')
                self.root.update_idletasks()
            except Exception:
                pass

    def _load_store_items_bg(self, ts, store_filter, cache_key):
        """Background worker to scan immediate subfolders (top-level only) and report progress.

        Posts messages to self.message_queue:
         - ('store_scan_progress', (current, total))
         - ('store_scan_done', (items, cache_key))
        """
        try:
            items = []
            ts_path = Path(ts)
            patterns = []
            if store_filter in ('All', 'Engines'):
                patterns.append('*.eng')
            if store_filter in ('All', 'Wagons'):
                patterns.append('*.wag')

            # list immediate subdirs and pre-count matching files for per-file progress
            children = [c for c in sorted(ts_path.iterdir()) if c.is_dir()]
            # gather all matching files (top-level only) across immediate children
            all_matches = []
            try:
                for child in children:
                    for pat in patterns:
                        for p in child.glob(pat):
                            if p.parent != child:
                                continue
                            all_matches.append((child, p))
            except Exception:
                pass

            total_files = len(all_matches)
            if total_files == 0:
                # nothing to do, return empty
                self.message_queue.put(('store_scan_done', ([], cache_key)))
                return

            processed_files = 0
            for child, p in all_matches:
                try:
                    rel = p.relative_to(Path(ts))
                    folder = str(rel.parent).replace('\\', '/') if rel.parent != Path('.') else ''
                    name = p.stem
                    ext = p.suffix.lstrip('.')
                    display = f"{folder}/{name}.{ext}" if folder else f"{name}.{ext}"
                    items.append({'display': display, 'folder': folder, 'name': name, 'extension': ext})

                    processed_files += 1
                    # Post progress update every 10 files to reduce message overhead
                    if processed_files % 10 == 0 or processed_files == total_files:
                        try:
                            self.message_queue.put(('store_scan_progress', (processed_files, total_files)))
                        except Exception:
                            pass
                except Exception:
                    # still count as processed to avoid stalling progress
                    try:
                        processed_files += 1
                        # Post progress update every 10 files to reduce message overhead
                        if processed_files % 10 == 0 or processed_files == total_files:
                            self.message_queue.put(('store_scan_progress', (processed_files, total_files)))
                    except Exception:
                        pass

            # cache and send done
            try:
                self._store_cache = list(items)
                self._store_cache_trainset = cache_key
            except Exception:
                pass

            self.message_queue.put(('store_scan_done', (items, cache_key)))
        except Exception as e:
            # On error, still post done with empty list
            try:
                self.message_queue.put(('store_scan_done', ([], cache_key)))
            except Exception:
                pass

    def _refresh_store_cache(self):
        """Clear store cache and reload items from trainset immediately."""
        try:
            self._store_cache = None
            self._store_cache_trainset = None
            self.load_store_items()
            self.log_message('Store cache refreshed')
        except Exception as e:
            self.log_message(f'Error refreshing store cache: {e}')

    def update_store_subfolders(self):
        """Populate the subfolder combobox with immediate subdirectories under the selected trainset path.

        User can choose one immediate subfolder; when selected, store scanning will use that folder's top-level files only.
        """
        try:
            ts = self.trainset_path.get()
            values = ['']
            if ts:
                ts_path = Path(ts)
                if ts_path.exists():
                    for child in sorted(ts_path.iterdir()):
                        if child.is_dir():
                            values.append(child.name)

            # update combobox values
            try:
                self.store_subfolder_cb['values'] = values
                # keep selection if still valid, else reset to top-level
                cur = self.store_subfolder_var.get() if hasattr(self, 'store_subfolder_var') else ''
                if cur not in values:
                    self.store_subfolder_var.set('')
                self.store_subfolder_cb.update_idletasks()
            except Exception:
                pass
        except Exception as e:
            self.log_message(f'Error updating store subfolders: {e}')
        
        # Load store items after updating subfolders
        try:
            self.load_store_items()
        except Exception as e:
            self.log_message(f'Error loading store items: {e}')

    def _refresh_store_cache(self):
        """Clear store cache and reload items from trainset immediately."""
        try:
            # Clear the cache key to force rescan, but keep the cached data as backup
            # This allows quick fallback if the rescan fails
            self._store_cache_trainset = None
            self.load_store_items()
            self.log_message('Store cache refreshed - rescanning trainset')
        except Exception as e:
            self.log_message(f'Error refreshing store cache: {e}')
            # If refresh fails, try to restore the cache key if we have cached data
            if self._store_cache:
                try:
                    # Try to reconstruct the cache key from current settings
                    ts = self.trainset_path.get()
                    if ts:
                        sub = self.store_subfolder_var.get() if hasattr(self, 'store_subfolder_var') else ''
                        store_filter = self.store_filter_var.get() if hasattr(self, 'store_filter_var') else 'All'
                        scan_all = bool(self.scan_all_subfolders_var.get()) if hasattr(self, 'scan_all_subfolders_var') else False
                        cache_key = f"{ts}::{sub}::all={int(scan_all)}::filter={store_filter}"
                        self._store_cache_trainset = cache_key
                        self.log_message('Restored store cache after refresh failure')
                except Exception:
                    pass

    def move_selected_up(self):
        """Move the selected entry up by one position."""
        try:
            sel = self.consist_tree.selection()
            if not sel:
                messagebox.showwarning('Warning', 'No entry selected')
                return
            iid = sel[0]
            if not iid.startswith('e'):
                return
            idx = int(iid[1:])
            if idx <= 0:
                return
            # swap
            self.current_entries[idx-1], self.current_entries[idx] = self.current_entries[idx], self.current_entries[idx-1]
            self._unsaved_changes = True
            try:
                self.save_button.config(state='normal')
            except Exception:
                pass
            self.refresh_consist_tree_from_current_entries()
            # reselect moved item
            try:
                self.consist_tree.selection_set(f'e{idx-1}')
            except Exception:
                pass
            self.update_status_summary()
        except Exception as e:
            self.log_message(f'Move up error: {e}')

    def move_selected_down(self):
        """Move the selected entry down by one position."""
        try:
            sel = self.consist_tree.selection()
            if not sel:
                messagebox.showwarning('Warning', 'No entry selected')
                return
            iid = sel[0]
            if not iid.startswith('e'):
                return
            idx = int(iid[1:])
            if idx >= len(self.current_entries)-1:
                return
            # swap
            self.current_entries[idx+1], self.current_entries[idx] = self.current_entries[idx], self.current_entries[idx+1]
            self._unsaved_changes = True
            try:
                self.save_button.config(state='normal')
            except Exception:
                pass
            self.refresh_consist_tree_from_current_entries()
            try:
                self.consist_tree.selection_set(f'e{idx+1}')
            except Exception:
                pass
            self.update_status_summary()
        except Exception as e:
            self.log_message(f'Move down error: {e}')

    def replace_selected_with(self):
        """Replace the selected consist entry with the selected store item."""
        try:
            sel = self.consist_tree.selection()
            if not sel:
                messagebox.showwarning('Warning', 'No entry selected')
                return
            iid = sel[0]
            if not iid.startswith('e'):
                return
            idx = int(iid[1:])
            repl = self.store_replace_var.get()
            if not repl:
                messagebox.showwarning('Warning', 'No replacement selected')
                return
            # find store item by display
            found = None
            for it in self.store_items:
                if it['display'] == repl:
                    found = it
                    break
            if not found:
                messagebox.showwarning('Warning', f'Replacement item not found: {repl}')
                return
            new_entry = {'type': 'Engine' if found['extension'].lower().startswith('eng') else 'Wagon',
                         'name': found['name'],
                         'folder': found['folder'],
                         'extension': found['extension'],
                         'uid': self.current_entries[idx].get('uid', str(idx)),
                         'flip': self.current_entries[idx].get('flip', False)}  # Preserve original flip value
            # replace
            if 0 <= idx < len(self.current_entries):
                self.current_entries[idx] = new_entry
                self._unsaved_changes = True
                try:
                    self.save_button.config(state='normal')
                except Exception:
                    pass
                self.refresh_consist_tree_from_current_entries()
                self.update_status_summary()
        except Exception as e:
            self.log_message(f'Replace error: {e}')

    def insert_store_item(self, mode: str):
        """Insert selected store items into the current_entries at position mode.

        mode: 'beg', 'cur', 'end', 'at'
        """
        try:
            sel = self.store_listbox.curselection()
            if not sel:
                messagebox.showwarning('Warning', 'No store item selected')
                return

            # Build list of selected items from the filtered listbox (indices refer to filtered_store_items)
            selected = [self.filtered_store_items[i] for i in sel]

            # number to add
            try:
                count = max(1, int(self.add_number_var.get()))
            except Exception:
                count = 1

            insert_index = None
            if mode == 'beg':
                insert_index = 0
            elif mode == 'end':
                insert_index = len(self.current_entries)
            elif mode == 'cur':
                # Insert at the currently-selected tree row position (if any), otherwise append
                cur = self.consist_tree.selection()
                if cur:
                    try:
                        sel_item = cur[0]
                        if sel_item.startswith('e'):
                            sel_idx = int(sel_item[1:])
                            insert_index = sel_idx
                        else:
                            insert_index = len(self.current_entries)
                    except Exception:
                        insert_index = len(self.current_entries)
                else:
                    insert_index = len(self.current_entries)
            elif mode == 'at':
                # ask user for 1-based position
                pos = simpledialog.askinteger('Insert Position', 'Enter 1-based position to insert at', minvalue=1, maxvalue=max(1, len(self.current_entries)+1))
                if pos is None:
                    return
                insert_index = pos - 1
            else:
                insert_index = len(self.current_entries)

            # Insert items (each selected item is added 'count' times)
            max_uid = max([int(e.get('uid', '0')) for e in self.current_entries] + [0])
            for it in selected:
                for _ in range(count):
                    new_entry = {'type': 'Engine' if it['extension'].lower().startswith('eng') else 'Wagon',
                                 'name': it['name'],
                                 'folder': it['folder'],
                                 'extension': it['extension'],
                                 'uid': str(max_uid + 1),
                                 'flip': False}  # New entries default to no flip
                    max_uid += 1
                    # clamp index
                    if insert_index < 0:
                        insert_index = 0
                    if insert_index > len(self.current_entries):
                        self.current_entries.append(new_entry)
                    else:
                        self.current_entries.insert(insert_index, new_entry)
                        insert_index += 1

            # Refresh GUI tree
            self.refresh_consist_tree_from_current_entries()
            self.update_status_summary()
            # mark as unsaved and enable Save As
            try:
                self._unsaved_changes = True
                try:
                    self.save_button.config(state='normal')
                except Exception:
                    pass
            except Exception:
                pass
        except Exception as e:
            self.log_message(f"Error inserting store item: {e}")

    def refresh_consist_tree_from_current_entries(self):
        """Re-render consist_tree from self.current_entries preserving list order."""
        try:
            # clear previous - use more robust clearing
            try:
                self.consist_tree.delete(*self.consist_tree.get_children())
            except:
                # fallback to individual deletion
                for item in self.consist_tree.get_children():
                    self.consist_tree.delete(item)
            for i, entry in enumerate(self.current_entries):
                status = 'Unknown'
                status_color = ''
                if self.trainset_path.get():
                    trainset_path = Path(self.trainset_path.get())
                    asset_path = trainset_path / entry['folder'] / f"{entry['name']}.{entry['extension']}"
                    if asset_path.exists():
                        status = 'Exists'
                        status_color = self.colors['existing']
                    else:
                        status = 'Missing'
                        status_color = self.colors['missing']

                # display as folder/name.ext (or name.ext if no folder)
                display_text = f"{entry['folder']}/{entry['name']}.{entry['extension']}" if entry.get('folder') else f"{entry['name']}.{entry['extension']}"
                # use iid to keep stable references (use index)
                iid = f"e{int(i)}"
                
                # Determine tag based on status
                if status == 'Missing':
                    tag = 'missing'
                elif status == 'Exists':
                    tag = 'existing'
                elif status == 'Unresolved':
                    tag = 'unresolved'
                elif status == 'Changed':
                    tag = 'changed'
                elif status == 'Unknown':
                    tag = 'unknown'
                else:
                    tag = ''
                
                item = self.consist_tree.insert('', 'end', iid=iid, text=display_text, 
                                               values=(entry['type'], entry['folder'], entry['name'], status),
                                               tags=(tag,) if tag else ())

            # bind selection change to enable delete button
            try:
                def _on_tree_select(event=None):
                    sel = self.consist_tree.selection()
                    try:
                        if sel:
                            self.delete_button.config(state='normal')
                        else:
                            self.delete_button.config(state='disabled')
                    except Exception:
                        pass

                self.consist_tree.bind('<<TreeviewSelect>>', _on_tree_select)
            except Exception:
                pass
        except Exception as e:
            self.log_message(f"Error refreshing consist tree: {e}")

    def delete_selected_entry(self):
        """Delete the selected entry (or entries) from current_entries."""
        try:
            sel = self.consist_tree.selection()
            if not sel:
                messagebox.showwarning('Warning', 'No entry selected')
                return
            # Convert iids back to indices and remove entries
            indices = []
            for iid in sel:
                try:
                    if iid.startswith('e'):
                        indices.append(int(iid[1:]))
                except Exception:
                    pass
            # remove in reverse order to keep indices valid
            for idx in sorted(indices, reverse=True):
                try:
                    del self.current_entries[idx]
                except Exception:
                    pass

            # mark unsaved and enable Save As
            try:
                self._unsaved_changes = True
                self.save_button.config(state='normal')
            except Exception:
                pass

            # refresh tree and status
            self.refresh_consist_tree_from_current_entries()
            self.update_status_summary()
        except Exception as e:
            self.log_message(f"Error deleting entry: {e}")


    
    def update_status_summary(self):
        """Update status summary labels"""
        
        total = len(self.current_entries)
        missing = 0
        existing = 0
        
        if self.trainset_path.get():
            trainset_path = Path(self.trainset_path.get())
            
            for entry in self.current_entries:
                asset_path = trainset_path / entry['folder'] / f"{entry['name']}.{entry['extension']}"
                if asset_path.exists():
                    existing += 1
                else:
                    missing += 1
        
        self.status_labels['total'].config(text=f"Total: {total}")
        self.status_labels['missing'].config(text=f"Missing: {missing}")
        self.status_labels['resolved'].config(text=f"Existing: {existing}")
        self.status_labels['changed'].config(text=f"Changed: 0")  # Will be updated after resolver runs
    
    def update_missing_items_display(self, file_path=None):
        """Update the missing items display for the specified file or selected file"""
        try:
            if file_path is None:
                # Get the currently selected file from the tree
                sel = self.consist_files_tree.selection()
                if sel:
                    file_path = sel[0]
                else:
                    # No file selected, clear the display
                    self.missing_text.config(state='normal')
                    self.missing_text.delete(1.0, tk.END)
                    self.missing_text.insert(tk.END, 'Select a consist file to view missing items.')
                    self.missing_text.config(state='disabled')
                    return
            # If the requested file is the currently loaded consist, reuse parsed entries
            if hasattr(self, 'current_consist_file') and self.current_consist_file and str(file_path) == str(self.current_consist_file):
                entries = list(self.current_entries)
            else:
                # Parse the file to get entries (only if not already cached)
                entries = self.parse_consist_file(file_path)
            
            # Calculate missing items
            missing_items = []
            if self.trainset_path.get():
                trainset_path = Path(self.trainset_path.get())
                
                for entry in entries:
                    asset_path = trainset_path / entry['folder'] / f"{entry['name']}.{entry['extension']}"
                    if not asset_path.exists():
                        missing_items.append(f"{entry['folder']}/{entry['name']}.{entry['extension']}")
            
            # Update the display
            self.missing_text.config(state='normal')
            self.missing_text.delete(1.0, tk.END)
            
            if missing_items:
                self.missing_text.insert(tk.END, f"Missing items in {Path(file_path).name}:\n\n")
                for item in missing_items:
                    self.missing_text.insert(tk.END, f"• {item}\n")
                self.missing_text.insert(tk.END, f"\nTotal missing: {len(missing_items)}")
            else:
                self.missing_text.insert(tk.END, f"No missing items found in {Path(file_path).name}")
            
            self.missing_text.config(state='disabled')
            
        except Exception as e:
            self.missing_text.config(state='normal')
            self.missing_text.delete(1.0, tk.END)
            self.missing_text.insert(tk.END, f"Error loading missing items: {str(e)}")
            self.missing_text.config(state='disabled')
            self.log_message(f"Error updating missing items display: {e}")
    
    def refresh_consist_view(self):
        """Refresh the consist view"""
        
        # First try to use the current consist file (set during analysis)
        if hasattr(self, 'current_consist_file') and self.current_consist_file:
            self.log_message(f"Refreshing view for current file: {self.current_consist_file}")
            self.analyze_single_consist(self.current_consist_file)
            return
        
        # Fallback to selected consist
        single_consist = self.selected_consist.get()
        if single_consist:
            self.log_message(f"Refreshing view for selected file: {single_consist}")
            self.analyze_single_consist(single_consist)
        else:
            consists_dir = self.consists_path.get()
            if consists_dir:
                consists_path = Path(consists_dir)
                consist_files = list(consists_path.glob("*.con"))
                if consist_files:
                    self.log_message(f"Refreshing view for first file: {consist_files[0]}")
                    self.analyze_single_consist(str(consist_files[0]))
        
        # Also refresh the missing items display for the current selection
        self.update_missing_items_display()

    def refresh_counts(self):
        """Refresh missing counts for all listed consist files (preserve selection)."""
        consists_dir = self.consists_path.get()
        if not consists_dir:
            messagebox.showwarning('Warning', 'No consists directory set')
            return

        consists_path = Path(consists_dir)
        if not consists_path.exists():
            messagebox.showwarning('Warning', f'Consists directory not found: {consists_dir}')
            return

        consist_files = list(consists_path.glob('*.con'))
        if not consist_files:
            messagebox.showwarning('Warning', f'No .con files found in: {consists_dir}')
            return

        # preserve current selection
        sel = self.consist_files_tree.selection()
        sel_iid = sel[0] if sel else None

        def worker(files):
            # signal scan start
            self.message_queue.put(('scan_start', None))
            results = []
            total_files = len(files)
            
            for i, cf in enumerate(files, 1):
                # Send progress update for large scans (every 10 files to reduce overhead)
                if total_files > 20 and (i % 10 == 0 or i == total_files):  # Only show detailed progress for very large scans
                    self.message_queue.put(('consist_scan_progress', (i, total_files)))
                
                missing_count = 0
                err = None
                try:
                    entries = self.parse_consist_file(str(cf))
                    if self.trainset_path.get():
                        trainset_path = Path(self.trainset_path.get())
                        for e in entries:
                            asset_path = trainset_path / e['folder'] / f"{e['name']}.{e['extension']}"
                            if not asset_path.exists():
                                missing_count += 1
                except Exception as ex:
                    missing_count = -1
                    err = str(ex)

                results.append((str(cf), cf.name, missing_count, err))

            # signal start/done around this worker via messages
            self.message_queue.put(('consist_list_update', results))
            self.message_queue.put(('scan_done', None))

        threading.Thread(target=worker, args=(consist_files,), daemon=True).start()
    
    def process_messages(self):
        """Process messages from background threads"""
        
        # Safety check: ensure message_queue exists
        if not hasattr(self, 'message_queue') or self.message_queue is None:
            # Schedule next check
            self.root.after(100, self.process_messages)
            return
        
        try:
            # Process up to 10 messages at a time to avoid blocking the UI
            messages_processed = 0
            max_messages_per_batch = 10
            
            while messages_processed < max_messages_per_batch:
                try:
                    msg_type, data = self.message_queue.get_nowait()
                    messages_processed += 1
                    
                    if msg_type == 'log':
                        self.log_message(data)
                    elif msg_type == 'button_state':
                        state, text = data
                        self.resolve_button.config(state=state, text=text)
                    elif msg_type == 'refresh':
                        # Backwards-compatible refresh message. Do a lightweight
                        # refresh of the currently loaded consist viewer only.
                        self.log_message("Processing refresh message (light): updating current consist viewer")
                        try:
                            self.refresh_consist_view()
                        except Exception:
                            pass
                    elif msg_type == 'resolver_progress_show':
                        try:
                            if not self.resolver_progress_visible:
                                self.resolver_progress.grid(row=13, column=0, sticky=(tk.W, tk.E), pady=(4, 0))
                                self.resolver_progress_visible = True
                                self.resolver_progress.config(mode='indeterminate')
                                self.resolver_progress.start()
                                self.scan_status_label.config(text='Starting resolver...')
                        except Exception:
                            pass
                    elif msg_type == 'resolver_progress_update':
                        try:
                            processed, total, status = data
                            if total > 0:
                                self.resolver_progress.config(mode='determinate')
                                self.resolver_progress.stop()  # Stop indeterminate mode
                                pct = int((processed / total) * 100)
                                self.resolver_progress_var.set(pct)
                                self.scan_status_label.config(text=f"{status} ({processed}/{total})")
                            else:
                                self.resolver_progress.config(mode='indeterminate')
                                self.resolver_progress.start()
                                self.scan_status_label.config(text=status)
                        except Exception:
                            pass
                    elif msg_type == 'resolver_progress_hide':
                        try:
                            if self.resolver_progress_visible:
                                self.resolver_progress.stop()
                                self.resolver_progress.grid_forget()
                                self.resolver_progress_visible = False
                                self.scan_status_label.config(text='')
                        except Exception:
                            pass
                    elif msg_type == 'scan_start':
                        # show scanning status
                        try:
                            self.scan_status_label.config(text='Scanning consists...')
                            self.refresh_counts_button.config(state='disabled')
                            # Show progress bar for large scans - will be shown when first progress message arrives
                        except Exception:
                            pass
                    elif msg_type == 'consist_scan_progress':
                        # Update progress for large consist scans
                        try:
                            current, total = data
                            if total > 0:
                                # Only show progress bar for very large scans
                                if total > 20 and hasattr(self, 'consist_scan_progress'):
                                    if not self.consist_scan_progress_visible:
                                        self.consist_scan_progress.grid(row=11, column=0, sticky=(tk.W, tk.E), pady=(2, 0))
                                        self.consist_scan_progress_visible = True
                                    self.consist_scan_progress.config(mode='determinate')
                                    self.consist_scan_progress.stop()
                                    pct = int((current / total) * 100)
                                    self.consist_scan_progress_var.set(pct)
                            # Keep status message fixed so the UI doesn't reflow due to filename length
                            self.scan_status_label.config(text=f'Scanning... ({current}/{total})')
                        except Exception:
                            pass
                    elif msg_type == 'scan_done':
                        try:
                            self.scan_status_label.config(text='Scan complete')
                            # Hide progress bar
                            if hasattr(self, 'consist_scan_progress') and self.consist_scan_progress_visible:
                                self.consist_scan_progress.stop()
                                self.consist_scan_progress.grid_forget()
                                self.consist_scan_progress_visible = False
                                self.consist_scan_progress_var.set(0)
                            # Clear the status after a brief delay
                            self.root.after(2000, lambda: self.scan_status_label.config(text=''))
                            self.refresh_counts_button.config(state='normal')
                        except Exception:
                            pass
                    elif msg_type == 'consist_list_update':
                        # data: list of tuples (path_str, display_name, missing_count, err)
                        results = data
                        try:
                            # cache results so the filter can be re-applied without re-scanning
                            try:
                                self._last_consist_scan_results = self._dedupe_consist_scan_results(results)
                            except Exception:
                                self._last_consist_scan_results = list(results)
                            # populate tree according to current filter
                            self._populate_consist_files_tree()
                            # Force GUI update
                            try:
                                self.root.update()
                            except Exception:
                                pass
                        except Exception as e:
                            self.log_message(f"Error updating consist files list: {e}")
                    elif msg_type == 'store_scan_progress':
                        if not self._store_progress_visible:
                            self._store_progress_visible = True
                            try:
                                # Ensure progress bar is properly positioned with fixed size
                                self.store_progress.place(relx=0, rely=1.0, anchor='sw', relwidth=1.0, height=20)
                            except Exception:
                                pass
                        try:
                            # data is (processed, total)
                            if isinstance(data, (list, tuple)) and len(data) >= 2:
                                processed = data[0]
                                total = data[1]
                            else:
                                processed = 0
                                total = 0

                            try:
                                # switch to determinate and update percent and label
                                pct = int((processed / total) * 100) if total else 100
                            except Exception:
                                pct = 0
                            try:
                                self.store_progress.configure(mode='determinate')
                                self.store_progress_var.set(pct)
                            except Exception:
                                pass
                            try:
                                # Update status label with current progress
                                self.store_scan_label_var.set(f'Scanning stores... ({processed}/{total})')
                            except Exception:
                                pass
                        except Exception:
                            pass
                    elif msg_type == 'store_scan_done':
                        # Reset progress bar and hide it smoothly
                        try:
                            self.store_progress_var.set(0)
                            # Use after() to delay hiding for smoother transition
                            self.root.after(500, lambda: self._hide_store_progress())
                        except Exception:
                            pass
                        try:
                            items, cache_key = data
                            try:
                                self.store_items = list(items)
                                # Update filtered items to match all items initially
                                self.filtered_store_items = list(items)
                                try:
                                    self.store_listbox.delete(0, tk.END)
                                except Exception:
                                    pass
                                for it in self.filtered_store_items:
                                    try:
                                        self.store_listbox.insert(tk.END, it['display'])
                                    except Exception:
                                        pass
                                # Force UI update after populating listbox
                                try:
                                    self.root.update_idletasks()
                                except Exception:
                                    pass
                                try:
                                    self._store_cache = list(self.store_items)
                                    self._store_cache_trainset = cache_key
                                except Exception:
                                    pass
                                try:
                                    # Update replace combobox after store items populated
                                    self._update_replace_combobox()
                                except Exception:
                                    pass
                                # Apply any existing search filter
                                try:
                                    self._filter_store_items()
                                except Exception:
                                    pass
                                # Update message label
                                try:
                                    if not self.trainset_path.get():
                                        self.store_message_label.config(text='Select a trainset directory to populate the stores.')
                                    elif not items:
                                        self.store_message_label.config(text='No assets found in the selected trainset.')
                                    else:
                                        self.store_message_label.config(text='')
                                except Exception:
                                    pass
                            except Exception:
                                pass
                            # Clear scanning label after a short delay
                            try:
                                self.root.after(1000, lambda: self.store_scan_label_var.set(''))
                            except Exception:
                                pass
                        except Exception:
                            pass
                    elif msg_type == 'files_changed':
                        # Offload recomputation to background worker to avoid blocking the UI
                        try:
                            changed = data or []
                            # Quick path: if current file is in changed set, ask main thread to refresh it
                            try:
                                cur = getattr(self, 'current_consist_file', None)
                                if cur and any(self._normalize_path(str(cur)) == self._normalize_path(str(p)) for p in changed):
                                    self.log_message(f"Current consist updated by resolver: {cur}")
                                    # Refresh current consist on main thread (lightweight)
                                    self.message_queue.put(('refresh_current_consist', None))
                            except Exception:
                                pass

                            # Take a snapshot of current cache to pass to worker
                            try:
                                cached_snapshot = list(getattr(self, '_last_consist_scan_results', []) or [])
                            except Exception:
                                cached_snapshot = []

                            # Start background worker to recompute missing counts for changed files
                            def _files_changed_bg(snapshot, changed_list):
                                try:
                                    self._files_changed_worker(snapshot, changed_list)
                                except Exception as e:
                                    try:
                                        self.message_queue.put(('log', f"Error in files_changed worker: {e}"))
                                    except Exception:
                                        pass

                            threading.Thread(target=_files_changed_bg, args=(cached_snapshot, changed), daemon=True).start()
                        except Exception as e:
                            self.log_message(f"Error dispatching files_changed worker: {e}")
                    elif msg_type == 'refresh_current_consist':
                        # Refresh the consist viewer for the currently loaded file (after resolver updates)
                        try:
                            if hasattr(self, 'current_consist_file') and self.current_consist_file:
                                self.log_message(f"Refreshing consist viewer for updated file: {self.current_consist_file}")
                                self.analyze_single_consist(self.current_consist_file)
                                try:
                                    self.update_missing_items_display(self.current_consist_file)
                                except Exception:
                                    pass
                        except Exception as e:
                            self.log_message(f"Error refreshing current consist after resolver: {e}")
                        
                except queue.Empty:
                    break
            
            # If we processed the max batch size, schedule immediate processing of remaining messages
            if messages_processed >= max_messages_per_batch:
                self.root.after(10, self.process_messages)
            else:
                # Schedule next check
                self.root.after(100, self.process_messages)
        except Exception as e:
            # Log error and reschedule
            try:
                self.log_message(f"Error in process_messages: {e}")
            except Exception:
                pass
            self.root.after(100, self.process_messages)
    
    def log_message(self, message):
        """Add message to output area"""
        try:
            if hasattr(self, 'output_text') and self.output_text:
                timestamp = time.strftime("%H:%M:%S")
                formatted_message = f"[{timestamp}] {message}\n"
                
                self.output_text.insert(tk.END, formatted_message)
                self.output_text.see(tk.END)
            else:
                # If output_text doesn't exist yet, print to console
                timestamp = time.strftime("%H:%M:%S")
                print(f"[{timestamp}] {message}")
        except Exception:
            # Fallback to console output
            timestamp = time.strftime("%H:%M:%S")
            print(f"[{timestamp}] {message}")

    def _on_tree_motion(self, event):
        """Show tooltip for error items on hover"""
        try:
            iid = self.consist_files_tree.identify_row(event.y)
            if not iid:
                self._hide_tooltip()
                return

            tags = self.consist_files_tree.item(iid, 'tags') or []
            if 'error' not in tags:
                self._hide_tooltip()
                return

            err = self._consist_errors.get(iid, 'Error details not available')
            # if tooltip already shown with same text, keep it
            if self._tooltip_window:
                # update text if different
                try:
                    label = self._tooltip_window.children.get('!label')
                    if label and label.cget('text') == err:
                        return
                except Exception:
                    pass
                self._hide_tooltip()

            # create small toplevel window near pointer
            x = self.root.winfo_pointerx() + 10
            y = self.root.winfo_pointery() + 10
            tw = tk.Toplevel(self.root)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f'+{x}+{y}')
            lbl = ttk.Label(tw, text=err, background='#FFFACD', relief='solid', padding=6)
            lbl.pack()
            self._tooltip_window = tw
        except Exception:
            pass

    def _hide_tooltip(self, event=None):
        try:
            if self._tooltip_window:
                try:
                    self._tooltip_window.destroy()
                except Exception:
                    pass
                self._tooltip_window = None
        except Exception:
            pass

    def _apply_consist_filter(self):
        """Called when the consist filter combobox changes; re-populate the tree from cached scan results."""
        try:
            self._populate_consist_files_tree()
        except Exception as e:
            self.log_message(f"Error applying consist filter: {e}")
    def _populate_consist_files_tree(self):
        """Populate the consist_files_tree using the cached _last_consist_scan_results and the selected filter.

        Filter options:
          - All: show everything
          - Broken: show only items with errors or missing assets (err != None OR missing_count > 0 OR missing_count == -1)
          - No Error: show only items without errors and with zero missing assets (err is None AND missing_count == 0)
        """
        try:
            results = list(getattr(self, '_last_consist_scan_results', []) or [])
            # Defensive: filter out any .bak entries from cache (shouldn't exist but be safe)
            try:
                results = [r for r in results if not (str(r[0]).lower().endswith('.bak') or str(r[1]).lower().endswith('.bak'))]
            except Exception:
                pass

            # clear existing
            try:
                self.consist_files_tree.delete(*self.consist_files_tree.get_children())
            except Exception:
                for item in self.consist_files_tree.get_children():
                    try:
                        self.consist_files_tree.delete(item)
                    except Exception:
                        pass

            first_iid = None
            self._consist_errors.clear()

            filt = (self.consist_filter_var.get() if hasattr(self, 'consist_filter_var') else 'All')

            for path_str, display_name, missing_count, err in results:
                # Skip backup files (safety)
                try:
                    if str(path_str).lower().endswith('.bak') or str(display_name).lower().endswith('.bak'):
                        continue
                except Exception:
                    pass
                # decide whether this file is considered 'broken'
                # broken if there was a parse/io error (err), or missing_count indicates missing assets (>0),
                # or the worker used -1 to indicate an error when counting
                is_broken = bool(err) or (isinstance(missing_count, int) and (missing_count > 0 or missing_count == -1))
                if filt == 'Broken' and not is_broken:
                    continue
                if filt == 'No Error' and is_broken:
                    continue

                # store error detail if available
                if err:
                    self._consist_errors[path_str] = err

                display_missing = missing_count if not (isinstance(missing_count, int) and missing_count == -1) else 'ERR'
                if display_missing == 'ERR':
                    tag = 'error'
                else:
                    tag = 'missing' if (isinstance(missing_count, int) and missing_count > 0) else 'no_missing'

                try:
                    norm_key = self._normalize_path(path_str)
                    self.consist_files_tree.insert('', 'end', iid=norm_key, values=(display_missing,), text=display_name, tags=(tag,))
                except Exception:
                    # fallback to inserting without iid
                    try:
                        self.consist_files_tree.insert('', 'end', values=(display_missing,), text=display_name, tags=(tag,))
                    except Exception:
                        pass

                if first_iid is None:
                    try:
                        first_iid = norm_key
                    except Exception:
                        first_iid = path_str

            # configure tag colors
            try:
                self.consist_files_tree.tag_configure('missing', foreground=self.colors['missing'])
                self.consist_files_tree.tag_configure('no_missing', foreground=self.colors['existing'])
                self.consist_files_tree.tag_configure('error', foreground='#A52A2A')
            except Exception:
                pass

            # bind tooltip events for error items
            try:
                self.consist_files_tree.bind('<Motion>', self._on_tree_motion)
                self.consist_files_tree.bind('<Leave>', self._hide_tooltip)
            except Exception:
                pass

            # Preserve the user's current selection if possible; otherwise auto-select first item
            try:
                cur = getattr(self, 'current_consist_file', None)
                norm_cur = None
                if cur:
                    try:
                        norm_cur = self._normalize_path(str(cur))
                    except Exception:
                        norm_cur = str(cur)

                if norm_cur and self.consist_files_tree.exists(norm_cur):
                    try:
                        self.consist_files_tree.selection_set(norm_cur)
                        # Re-analyze and update viewer for the current file so changes are visible
                        self.analyze_single_consist(norm_cur)
                        self.update_missing_items_display(norm_cur)
                    except Exception:
                        pass
                elif first_iid:
                    try:
                        self.consist_files_tree.selection_set(first_iid)
                        self.analyze_single_consist(first_iid)
                        # Update missing items display for the first file
                        self.update_missing_items_display(first_iid)
                    except Exception:
                        pass
            except Exception:
                pass

            # Update the showing counter (visible / total) with fixed width for alignment
            try:
                total = len(results)
                visible = len(self.consist_files_tree.get_children(''))
                if hasattr(self, 'consist_filter_status_var'):
                    try:
                        # Determine digits needed for alignment based on total
                        try:
                            total_digits = max(1, len(str(int(total))))
                        except Exception:
                            total_digits = 4
                        # Build formatted string with dynamic padding
                        fmt = f"{{:>{total_digits}d}} / {{:>{total_digits}d}}"
                        try:
                            self.consist_filter_status_var.set(fmt.format(visible, total))
                        except Exception:
                            self.consist_filter_status_var.set(f"{visible} / {total}")
                        # Set label width to accommodate the string plus prefix
                        try:
                            label_width = (total_digits * 2) + 3
                            self.consist_filter_status.configure(width=label_width)
                        except Exception:
                            pass
                        # Attempt monospace font and right alignment
                        try:
                            # Try multiple monospace fonts in order of preference
                            fonts_to_try = ['Consolas', 'Courier New', 'Monaco', 'Menlo', 'DejaVu Sans Mono']
                            font_set = False
                            for font_name in fonts_to_try:
                                try:
                                    self.consist_filter_status.configure(font=(font_name, 9), anchor='e', justify='right')
                                    font_set = True
                                    break
                                except Exception:
                                    continue
                            if not font_set:
                                # Fallback to default font with right alignment
                                self.consist_filter_status.configure(anchor='e', justify='right')
                        except Exception:
                            try:
                                self.consist_filter_status.configure(anchor='e', justify='right')
                            except Exception:
                                pass
                    except Exception:
                        try:
                            self.consist_filter_status_var.set(f"Showing {visible} / {total}")
                        except Exception:
                            pass
            except Exception:
                pass

        except Exception as e:
            self.log_message(f"Error populating consist files tree: {e}")
    
    def run_resolver(self):
        """Run the consist resolver based on selected mode"""
        
        if not self.resolver_script_path:
            messagebox.showerror("Error", "consistEditor.py script not found!")
            return
        
        consists_dir = self.consists_path.get()
        trainset_dir = self.trainset_path.get()
        
        if not consists_dir or not trainset_dir:
            messagebox.showerror("Error", "Please set both consists and trainset directories")
            return
        
        resolve_mode = self.resolve_mode_var.get()
        
        if resolve_mode == 'selected':
            # Resolve only the selected file
            selected_file = self._get_selected_consist_file()
            if not selected_file:
                messagebox.showerror("Error", "No consist file selected. Please select a file from the consist files list.")
                return
            self._run_resolver_for_file(selected_file, trainset_dir)
        else:
            # Resolve all files currently shown by the filter (don't re-scan the whole directory)
            self._run_resolver_for_filtered(consists_dir, trainset_dir)
    
    def _get_selected_consist_file(self):
        """Get the currently selected consist file path"""
        try:
            sel = self.consist_files_tree.selection()
            if sel:
                return sel[0]  # The iid is the file path
        except Exception:
            pass
        return None
    
    def _run_resolver_for_file(self, consist_file, trainset_dir):
        """Run resolver for a single consist file"""
        
        # Create a temporary directory with just this file
        import tempfile
        import shutil
        
        temp_dir = None
        try:
            temp_dir = tempfile.mkdtemp(prefix='msts_resolve_')
            temp_file = Path(temp_dir) / Path(consist_file).name
            shutil.copy2(consist_file, temp_file)

            # Run resolver in a background thread so the GUI remains responsive and the
            # progress bar can be updated via the existing message_queue handlers.
            def _start_worker():
                try:
                    self._resolver_file_worker(temp_dir, consist_file, trainset_dir)
                except Exception as e:
                    self.message_queue.put(('log', f"Error in resolver worker: {e}"))

            threading.Thread(target=_start_worker, daemon=True).start()
            # Return immediately to keep UI responsive; worker will copy back and refresh when done.
            return

        except Exception as e:
            messagebox.showerror("Error", f"Failed to prepare single file resolution: {e}")
            # Attempt cleanup if temp_dir was created
            if temp_dir and Path(temp_dir).exists():
                try:
                    shutil.rmtree(temp_dir)
                except Exception:
                    pass
    
    def _run_resolver_for_directory(self, consists_dir, trainset_dir):
        """Run resolver for all files in directory"""
        # Run resolver in background so GUI stays responsive and progress bar updates work
        def _dir_worker():
            try:
                self._run_resolver_thread(consists_dir, trainset_dir, refresh_after=True)
            except Exception as e:
                self.message_queue.put(('log', f"Error running resolver for directory: {e}"))

        threading.Thread(target=_dir_worker, daemon=True).start()

    def _run_resolver_for_filtered(self, consists_dir, trainset_dir):
        """Run resolver only for files currently shown in the consist files tree (respects filter).

        Copies the visible .con files into a temp directory, runs the resolver there and
        then copies resolved files back to their original locations. This avoids re-scanning
        or processing files that are not currently shown by the filter.
        """
        import tempfile
        import shutil

        # Collect visible items from the tree (iids are file paths)
        try:
            visible_iids = []
            for iid in self.consist_files_tree.get_children(''):
                # The tree only contains currently-populated (filtered) items
                visible_iids.append(iid)
        except Exception:
            visible_iids = []

        if not visible_iids:
            messagebox.showinfo("Info", "No consist files are currently shown by the filter to resolve.")
            return

        # Create temp directory and copy only visible files, using unique temp names and a mapping
        try:
            temp_dir = tempfile.mkdtemp(prefix='msts_resolve_filter_')
            temp_to_original = {}
            for idx, iid in enumerate(visible_iids):
                try:
                    src = Path(iid)
                    if src.exists():
                        temp_name = f"{idx}_{src.name}"
                        dst = Path(temp_dir) / temp_name
                        shutil.copy2(src, dst)
                        temp_to_original[temp_name] = self._normalize_path(str(src))
                except Exception as e:
                    self.message_queue.put(('log', f"Warning copying {iid} to temp dir: {e}"))
        except Exception as e:
            messagebox.showerror("Error", f"Failed to prepare temporary directory for filtered resolve: {e}")
            return

        # Run resolver in background thread similar to directory runner, but using temp_dir
        def _filtered_worker():
            try:
                # Use the same _run_resolver_thread which will detect changed files
                # Run resolver on temp dir but do NOT auto-enqueue temp-file changes
                # (we will enqueue original paths after copying back). This avoids
                # temporary files being added to the main cache and tree.
                return_code = self._run_resolver_thread(temp_dir, trainset_dir, refresh_after=False)

                # After resolver returns, copy changed files back by comparing mtimes
                try:
                    files_to_update = set()
                    # For each file in temp_dir, if a resolved version exists, copy back to its original
                    for p in Path(temp_dir).glob('*.con'):
                        try:
                            if not p.exists():
                                continue
                            temp_name = p.name
                            # Only copy back if this temp file corresponds to an original we prepared
                            orig_path = temp_to_original.get(temp_name)
                            if not orig_path:
                                # Unknown output file from resolver; skip to avoid creating duplicates
                                self.message_queue.put(('log', f"Skipping unknown resolver output: {p.name}"))
                                continue
                            # Only copy back if resolver succeeded
                            if return_code == 0:
                                try:
                                    shutil.copy2(str(p), str(orig_path))
                                    files_to_update.add(str(self._normalize_path(orig_path)))
                                except Exception as e:
                                    self.message_queue.put(('log', f"Error copying resolved file {p} back to {orig_path}: {e}"))
                        except Exception as e:
                            self.message_queue.put(('log', f"Error processing resolved file {p}: {e}"))
                    
                    # Also include all visible files for re-checking (in case resolver made no changes)
                    for iid in visible_iids:
                        files_to_update.add(str(self._normalize_path(iid)))
                    
                    # Send one files_changed message with all files that need updating
                    if files_to_update:
                        try:
                            self.message_queue.put(('files_changed', list(files_to_update)))
                        except Exception:
                            pass
                                
                except Exception as e:
                    self.message_queue.put(('log', f"Error during filtered copy-back: {e}"))
                finally:
                    # Cleanup temp dir
                    try:
                        shutil.rmtree(temp_dir)
                    except Exception:
                        pass
            except Exception as e:
                self.message_queue.put(('log', f"Error in filtered resolver worker: {e}"))

        threading.Thread(target=_filtered_worker, daemon=True).start()

    def _resolver_file_worker(self, temp_dir, original_consist_path, trainset_dir):
        """Worker to run resolver for a single-file temp directory, copy back results and clean up."""
        try:
            # Run resolver on temp dir without automated refresh; we'll copy back and enqueue the original path.
            return_code = self._run_resolver_thread(temp_dir, trainset_dir, refresh_after=False)

            # Copy the resolved file back to original location if resolver succeeded
            if return_code == 0:
                resolved_temp_file = Path(temp_dir) / Path(original_consist_path).name
                self.message_queue.put(('log', f"Checking for resolved temp file: {resolved_temp_file}"))

                if resolved_temp_file.exists():
                    try:
                        self.message_queue.put(('log', f"Copying resolved file from {resolved_temp_file} to {original_consist_path}"))
                        shutil.copy2(str(resolved_temp_file), original_consist_path)
                        self.message_queue.put(('log', f"Resolved file copied back to: {original_consist_path}"))

                        # Verify the copy worked and refresh missing count
                        if Path(original_consist_path).exists():
                            self.message_queue.put(('log', f"Verified: Original file exists at {original_consist_path}"))
                            try:
                                # Refresh the missing count for this specific file on the main thread
                                self._refresh_single_file_missing_count(original_consist_path)

                                # If this is the currently loaded consist file, refresh the consist viewer with updated colors
                                if hasattr(self, 'current_consist_file') and self.current_consist_file and str(self.current_consist_file) == str(original_consist_path):
                                    self.message_queue.put(('log', f"Refreshing consist viewer for updated file: {original_consist_path}"))
                                    self.message_queue.put(('refresh_current_consist', None))

                                # Also enqueue a targeted files_changed message for the main thread
                                try:
                                    # enqueue normalized original path
                                    self.message_queue.put(('files_changed', [str(self._normalize_path(original_consist_path))]))
                                except Exception:
                                    pass
                            except Exception as e:
                                self.message_queue.put(('log', f"Error refreshing after resolver: {e}"))
                        else:
                            self.message_queue.put(('log', f"Error: Original file not found after copy at {original_consist_path}"))
                    except Exception as e:
                        self.message_queue.put(('log', f"Error copying resolved file back: {e}"))
                else:
                    self.message_queue.put(('log', f"Warning: Resolved temp file not found: {resolved_temp_file}"))
                    try:
                        contents = list(Path(temp_dir).iterdir())
                        self.message_queue.put(('log', f"Temp directory contents: {[str(p) for p in contents]}"))
                    except Exception as e:
                        self.message_queue.put(('log', f"Error listing temp directory: {e}"))
            else:
                self.message_queue.put(('log', f"Resolver failed with code {return_code}, not copying back resolved file"))

        except Exception as e:
            self.message_queue.put(('log', f"Error in single-file resolver worker: {e}"))
        finally:
            # Clean up temp directory after resolver finishes and file copy is done
            if temp_dir and Path(temp_dir).exists():
                try:
                    # Schedule cleanup after a short delay to ensure file operations complete
                    def cleanup():
                        time.sleep(5)
                        try:
                            shutil.rmtree(temp_dir)
                        except Exception:
                            pass
                    threading.Thread(target=cleanup, daemon=True).start()
                except Exception:
                    pass

    def _files_changed_worker(self, cached_snapshot, changed_list):
        """Background worker to recompute missing counts for changed files and update cache.

        Posts a single 'consist_list_update' message with the updated, deduplicated results
        so the main thread can re-populate the tree in one go.
        """
        try:
            # Normalize inputs
            try:
                changed_set = set([self._normalize_path(p) for p in (changed_list or [])])
            except Exception:
                changed_set = set([str(p) for p in (changed_list or [])])

            # Build lookup from snapshot
            snapshot_map = {}
            try:
                for p, dname, mc, er in (cached_snapshot or []):
                    try:
                        key = self._normalize_path(p)
                    except Exception:
                        key = str(p)
                    snapshot_map[key] = (dname, mc, er)
            except Exception:
                snapshot_map = {}

            # Recompute entries for changed files
            updated_results = []
            all_keys = set(list(snapshot_map.keys())) | set(changed_set)
            for key in sorted(all_keys):
                if key in changed_set:
                    # recompute missing count
                    try:
                        missing_count = 0
                        err = None
                        try:
                            entries = self.parse_consist_file(key)
                            if self.trainset_path.get():
                                trainset_path = Path(self.trainset_path.get())
                                for e in entries:
                                    asset_path = trainset_path / e['folder'] / f"{e['name']}.{e['extension']}"
                                    if not asset_path.exists():
                                        missing_count += 1
                        except Exception as ex:
                            missing_count = -1
                            err = str(ex)
                    except Exception:
                        missing_count = -1
                        err = 'Error computing missing count'
                    display_name = Path(key).name
                    updated_results.append((key, display_name, missing_count, err))
                else:
                    # keep existing
                    try:
                        dname, mc, er = snapshot_map.get(key, (Path(key).name, None, None))
                        updated_results.append((key, dname, mc, er))
                    except Exception:
                        updated_results.append((key, Path(key).name, None, None))

            # Deduplicate and post back to main thread
            try:
                deduped = self._dedupe_consist_scan_results(updated_results)
            except Exception:
                deduped = list(updated_results)

            # Emit limited per-file debug logs to help trace persistent 'broken' state
            try:
                max_debug = 50
                debug_lines = []
                # Build a quick map of previous values for comparison
                prev_map = {}
                try:
                    for p, dname, mc, er in (cached_snapshot or []):
                        prev_map[self._normalize_path(p)] = (mc, er)
                except Exception:
                    prev_map = {}

                count = 0
                for p, dname, mc, er in deduped:
                    if count >= max_debug:
                        break
                    prev = prev_map.get(self._normalize_path(p)) if prev_map else None
                    if prev is None:
                        debug_lines.append(f"NEW: {dname} -> missing={mc} err={bool(er)}")
                    else:
                        debug_lines.append(f"CHG: {dname} prev_missing={prev[0]} now_missing={mc} prev_err={bool(prev[1])} now_err={bool(er)}")
                    count += 1
                if debug_lines:
                    try:
                        self.message_queue.put(('log', f"FilesChanged debug (first {len(debug_lines)}):"))
                        for l in debug_lines:
                            self.message_queue.put(('log', l))
                    except Exception:
                        pass
            except Exception:
                pass

            try:
                # Log counts for diagnostic visibility
                changed_count = len(changed_set)
                total_count = len(deduped)
                try:
                    self.message_queue.put(('log', f"Files changed processing complete: {changed_count} files recomputed, {total_count} total entries in cache"))
                except Exception:
                    pass
                self.message_queue.put(('consist_list_update', deduped))
            except Exception:
                pass
        except Exception as e:
            try:
                self.message_queue.put(('log', f"Error in files_changed_worker: {e}"))
            except Exception:
                pass
    
    def _run_resolver_thread(self, consists_dir, trainset_dir, refresh_after=True):
        """Run resolver in background thread"""
        
        try:
            # Build command using virtual environment Python
            cmd = [self.venv_python_path, self.resolver_script_path, consists_dir, trainset_dir]
            
            if self.dry_run_var.get():
                cmd.append('--dry-run')
            if self.explain_var.get():
                cmd.append('--explain')
            if self.debug_var.get():
                cmd.append('--debug')
            
            # Verify the Python executable exists before running
            import os
            if not os.path.exists(self.venv_python_path):
                self.message_queue.put(('log', f"ERROR: Python executable not found: {self.venv_python_path}"))
                self.message_queue.put(('log', "Trying to find alternative Python..."))
                
                # Try to find alternative Python
                import shutil
                alt_python = shutil.which('python')
                if alt_python:
                    self.message_queue.put(('log', f"Using alternative Python: {alt_python}"))
                    cmd[0] = alt_python
                else:
                    self.message_queue.put(('log', "ERROR: No Python executable found"))
                    return
            
            # Verify the resolver script exists
            if not os.path.exists(self.resolver_script_path):
                self.message_queue.put(('log', f"ERROR: Resolver script not found: {self.resolver_script_path}"))
                return
            
            # Snapshot .con files mtimes before running resolver so we can detect
            # which files change and avoid rescanning everything.
            pre_mtimes = {}
            try:
                cd = Path(consists_dir)
                if cd.exists():
                    for p in cd.glob('*.con'):
                        try:
                            pre_mtimes[str(p)] = p.stat().st_mtime
                        except Exception:
                            pre_mtimes[str(p)] = None
            except Exception:
                pre_mtimes = {}

            self.message_queue.put(('log', f"Running resolver command: {' '.join(cmd)}"))
            self.message_queue.put(('log', "Resolver started..."))
            
            # Disable button during processing
            self.message_queue.put(('button_state', ('disabled', 'Running...')))
            
            # Show progress bar
            self.message_queue.put(('resolver_progress_show', None))
            
            # Run process
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                universal_newlines=True,
                bufsize=1
            )
            
            # Read output in real-time
            total_files = 0
            total_entries = 0
            while True:
                output = process.stdout.readline()
                if output == '' and process.poll() is not None:
                    break
                if output:
                    stripped = output.strip()
                    self.message_queue.put(('log', stripped))
                    
                    # Parse progress information
                    if 'Found' in stripped and 'consist files' in stripped:
                        try:
                            import re
                            match = re.search(r'Found (\d+) consist files', stripped)
                            if match:
                                total_files = int(match.group(1))
                                self.message_queue.put(('resolver_progress_update', (0, total_files, 'Scanning files...')))
                        except Exception:
                            pass
                    elif 'with' in stripped and 'asset references' in stripped:
                        try:
                            import re
                            match = re.search(r'with (\d+) asset references', stripped)
                            if match:
                                total_entries = int(match.group(1))
                                self.message_queue.put(('resolver_progress_update', (0, total_entries, 'Processing assets...')))
                        except Exception:
                            pass
                    elif 'Asset resolution completed' in stripped and 'entries' in stripped:
                        try:
                            import re
                            match = re.search(r'Processed (\d+) entries', stripped)
                            if match:
                                processed = int(match.group(1))
                                self.message_queue.put(('resolver_progress_update', (processed, total_entries or processed, 'Completing...')))
                        except Exception:
                            pass
            
            # Wait for completion
            return_code = process.wait()
            
            self.message_queue.put(('log', f"Resolver finished with return code: {return_code}"))
            
            # Re-enable button
            self.message_queue.put(('button_state', ('normal', 'Run Resolver')))
            
            # Hide progress bar
            self.message_queue.put(('resolver_progress_hide', None))
            
            # After resolver finishes, snapshot mtimes again and diff to find
            # changed files. Send the list of changed files to the main thread
            # so it can refresh only those entries (cheap, targeted update).
            changed_files = []
            try:
                post_mtimes = {}
                cd = Path(consists_dir)
                if cd.exists():
                    for p in cd.glob('*.con'):
                        try:
                            post_mtimes[str(p)] = p.stat().st_mtime
                        except Exception:
                            post_mtimes[str(p)] = None

                # Detect added/modified/removed
                all_paths = set(pre_mtimes.keys()) | set(post_mtimes.keys())
                for path in all_paths:
                    pre = pre_mtimes.get(path)
                    post = post_mtimes.get(path)
                    if pre != post:
                        # Changed, added or removed -> include for refresh
                        # Only include files that currently exist (we refresh present files).
                        if post is not None and Path(path).exists():
                            changed_files.append(path)
                        else:
                            # If file was removed, still attempt to update tree item
                            changed_files.append(path)
            except Exception as e:
                self.message_queue.put(('log', f"Error determining changed files: {e}"))

            # Send changed files list to main thread for targeted refresh
            if refresh_after:
                try:
                    if changed_files:
                            # Normalize paths to ensure cache keys and tree iids match
                            try:
                                norm_changed = [self._normalize_path(p) for p in changed_files]
                            except Exception:
                                norm_changed = changed_files
                            self.message_queue.put(('files_changed', norm_changed))
                            self.log_message(f"Files changed: {len(norm_changed)} -> queued targeted refresh")
                    else:
                        # Fallback: if nothing changed, still ask to refresh current file only
                        self.message_queue.put(('refresh_current_consist', None))
                        self.log_message("No changed files detected; doing light refresh of current consist")
                except Exception:
                    pass
            else:
                self.log_message("Refresh skipped (refresh_after=False)")
            
            return return_code
            
        except Exception as e:
            self.message_queue.put(('log', f"Error running resolver: {str(e)}"))
            self.message_queue.put(('button_state', ('normal', 'Run Resolver')))
            
            # Hide progress bar
            self.message_queue.put(('resolver_progress_hide', None))
            
            # Refresh view only if requested (even on error)
            if refresh_after:
                self.message_queue.put(('refresh', None))
            
            return 1  # Return error code
    
    def _refresh_single_file_missing_count(self, file_path):
        """Refresh the missing count for a single file in the tree view"""
        try:
            self.log_message(f"Refreshing missing count for: {file_path}")
            
            # Calculate missing count for this file
            missing_count = 0
            err = None
            try:
                entries = self.parse_consist_file(file_path)
                if self.trainset_path.get():
                    trainset_path = Path(self.trainset_path.get())
                    for e in entries:
                        asset_path = trainset_path / e['folder'] / f"{e['name']}.{e['extension']}"
                        if not asset_path.exists():
                            missing_count += 1
            except Exception as ex:
                missing_count = -1
                err = str(ex)
                self.log_message(f"Error calculating missing count for {file_path}: {err}")

            # Update the tree item
            file_path_str = str(file_path)
            try:
                # Check if the item exists in the tree
                if self.consist_files_tree.exists(file_path_str):
                    # Update the values and tags
                    display_missing = missing_count if not (isinstance(missing_count, int) and missing_count == -1) else 'ERR'
                    
                    if display_missing == 'ERR':
                        tag = 'error'
                    else:
                        tag = 'missing' if (isinstance(missing_count, int) and missing_count > 0) else 'no_missing'
                    
                    # Update the tree item
                    self.consist_files_tree.item(file_path_str, values=(display_missing,), tags=(tag,))
                    
                    # Update the error map if needed
                    if err:
                        self._consist_errors[file_path_str] = err
                    elif file_path_str in self._consist_errors:
                        del self._consist_errors[file_path_str]
                    
                    self.log_message(f"Updated missing count for {Path(file_path).name}: {display_missing} (tag: {tag})")
                else:
                    self.log_message(f"Tree item not found for: {file_path_str}")
                # If this refreshed file is the currently loaded consist, also update the missing-items panel
                try:
                    if hasattr(self, 'current_consist_file') and self.current_consist_file and str(self.current_consist_file) == str(file_path):
                        try:
                            self.update_missing_items_display(file_path)
                        except Exception:
                            pass
                except Exception:
                    pass
            except Exception as e:
                self.log_message(f"Error updating tree item for {file_path}: {e}")
                
        except Exception as e:
            self.log_message(f"Error in _refresh_single_file_missing_count: {e}")
    
    def _update_resolve_button_text(self, *args):
        """Update resolver button text based on selected mode"""
        try:
            mode = self.resolve_mode_var.get()
            if mode == 'selected':
                # Keep the button text stable to avoid layout changes; do not embed filename
                self.resolve_button.config(text="Resolve Selected File")
            else:
                self.resolve_button.config(text="Resolve All Files")
        except Exception:
            self.resolve_button.config(text="Resolve Selected File")
    
    def on_consist_file_selected(self, event=None):
        """Handle selection of a consist file from the tree"""
        # Safety check: ensure required attributes exist
        if not hasattr(self, 'consist_files_tree') or not hasattr(self, 'message_queue'):
            return
            
        try:
            sel = self.consist_files_tree.selection()
            if sel:
                file_path = sel[0]
                # Analyze and populate main consist view
                try:
                    # If this file is already the current loaded file, skip re-analysis
                    if not (hasattr(self, 'current_consist_file') and self.current_consist_file and str(self.current_consist_file) == str(file_path)):
                        self.analyze_single_consist(file_path)
                except Exception as e:
                    self.log_message(f"Error analyzing selected consist: {e}")
                
                # Update missing items display for the selected file
                self.update_missing_items_display(file_path)
                
                # Update resolver button text if in selected mode
                if self.resolve_mode_var.get() == 'selected':
                    self._update_resolve_button_text()
        except Exception as e:
            self.log_message(f"Error handling consist file selection: {e}")
    
    def run(self):
        """Start the GUI application"""
        self.root.mainloop()

def main():
    """Main entry point"""
    try:
        app = ConsistEditorGUI()
        app.run()
    except Exception as e:
        print(f"Error starting GUI: {e}")
        print("Make sure you have tkinter installed and a display available")

if __name__ == "__main__":
    main()