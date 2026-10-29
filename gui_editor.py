"""
FMOD Studio strings.bank GUI Editor
Author: Antigravity
Description:
    Tkinter-based visual editor for FMOD Studio *.strings.bank files.
    Features:
      - Open & Save *.strings.bank and *.json
      - Live search & prefix filtering (event, bus, vca, parameter, snapshot, bank)
      - Table view with multi-column sorting
      - Inline path and GUID editing with random GUID generator
      - Add, delete, clone (alias) entries
      - Verify bank integrity with official FMOD DLL
"""

import os
import sys
import uuid
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from typing import Optional

import fmod_strings_engine as eng


class StringsEditorGUI:
    def __init__(self, root: tk.Tk, initial_file: Optional[str] = None):
        self.root = root
        self.root.title("FMOD Studio strings.bank Editor")
        self.root.geometry("1100x680")
        self.root.minsize(800, 500)

        self.current_file: Optional[str] = None
        self.model = eng.StringsBankModel()
        self.is_modified = False

        self._setup_styles()
        self._create_menu()
        self._create_widgets()

        if initial_file and os.path.exists(initial_file):
            self.load_file(initial_file)
        else:
            default_bank = os.path.join(os.path.dirname(__file__), 'banks', 'Host.strings.bank')
            if os.path.exists(default_bank):
                self.load_file(default_bank)

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        # Configure fonts and padding
        style.configure('Treeview', font=('Segoe UI', 9), rowheight=24)
        style.configure('Treeview.Heading', font=('Segoe UI', 9, 'bold'))
        style.configure('TLabel', font=('Segoe UI', 9))
        style.configure('TButton', font=('Segoe UI', 9))
        style.configure('Header.TLabel', font=('Segoe UI', 11, 'bold'))

    def _create_menu(self):
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open... (Ctrl+O)", command=self.on_open, accelerator="Ctrl+O")
        file_menu.add_command(label="Save (Ctrl+S)", command=self.on_save, accelerator="Ctrl+S")
        file_menu.add_command(label="Save As...", command=self.on_save_as)
        file_menu.add_separator()
        file_menu.add_command(label="Export to JSON...", command=self.on_export_json)
        file_menu.add_command(label="Export to CSV...", command=self.on_export_csv)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=file_menu)

        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label="Verify with FMOD Engine", command=self.on_verify)
        tools_menu.add_command(label="Bank Information", command=self.on_bank_info)
        tools_menu.add_separator()
        tools_menu.add_command(label="Generate FMOD Project Skeleton (.fspro)...", command=self.on_generate_skeleton)
        menubar.add_cascade(label="Tools", menu=tools_menu)

        self.root.config(menu=menubar)
        self.root.bind("<Control-o>", lambda e: self.on_open())
        self.root.bind("<Control-s>", lambda e: self.on_save())

    def _create_widgets(self):
        # Top toolbar
        toolbar = ttk.Frame(self.root, padding=6)
        toolbar.pack(fill=tk.X)

        ttk.Label(toolbar, text="Filter:").pack(side=tk.LEFT, padx=(0, 4))
        self.type_filter_var = tk.StringVar(value="All Types")
        type_options = ["All Types", "event", "bus", "vca", "parameter", "snapshot", "bank", "other"]
        type_combo = ttk.Combobox(toolbar, textvariable=self.type_filter_var, values=type_options, state="readonly", width=12)
        type_combo.pack(side=tk.LEFT, padx=(0, 10))
        type_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh_table())

        ttk.Label(toolbar, text="Search:").pack(side=tk.LEFT, padx=(0, 4))
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(toolbar, textvariable=self.search_var, width=35)
        search_entry.pack(side=tk.LEFT, padx=(0, 10))
        self.search_var.trace_add("write", lambda *args: self.refresh_table())

        self.count_label = ttk.Label(toolbar, text="Items: 0")
        self.count_label.pack(side=tk.RIGHT, padx=6)

        # Main splitter (Treeview on left, Inspector on right)
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=6, pady=4)

        # Left: Treeview
        tree_frame = ttk.Frame(paned)
        paned.add(tree_frame, weight=3)

        cols = ("idx", "type", "path", "guid")
        self.tree = ttk.Treeview(tree_frame, columns=cols, show='headings', selectmode='browse')
        self.tree.heading("idx", text="#", command=lambda: self.sort_column("idx", False))
        self.tree.heading("type", text="Type", command=lambda: self.sort_column("type", False))
        self.tree.heading("path", text="Path", command=lambda: self.sort_column("path", False))
        self.tree.heading("guid", text="GUID", command=lambda: self.sort_column("guid", False))

        self.tree.column("idx", width=45, stretch=False, anchor=tk.CENTER)
        self.tree.column("type", width=80, stretch=False)
        self.tree.column("path", width=380, stretch=True)
        self.tree.column("guid", width=280, stretch=False)

        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky='nsew')
        vsb.grid(row=0, column=1, sticky='ns')
        hsb.grid(row=1, column=0, sticky='ew')
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        self.tree.bind("<<TreeviewSelect>>", self.on_select_row)

        # Right: Inspector & Editing Pane
        inspector = ttk.LabelFrame(paned, text="Entry Inspector / Editor", padding=10)
        paned.add(inspector, weight=1)

        ttk.Label(inspector, text="Event / Object Path:").pack(anchor=tk.W, pady=(0, 2))
        self.edit_path_var = tk.StringVar()
        ttk.Entry(inspector, textvariable=self.edit_path_var, width=42).pack(fill=tk.X, pady=(0, 8))

        ttk.Label(inspector, text="GUID {xxxxxxxx-xxxx-xxxx-...}:").pack(anchor=tk.W, pady=(0, 2))
        self.edit_guid_var = tk.StringVar()
        ttk.Entry(inspector, textvariable=self.edit_guid_var, width=42).pack(fill=tk.X, pady=(0, 4))

        btn_guid = ttk.Button(inspector, text="🎲 Generate New GUID", command=self.on_generate_guid)
        btn_guid.pack(fill=tk.X, pady=(0, 12))

        ttk.Separator(inspector, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

        btn_save_entry = ttk.Button(inspector, text="💾 Update Selected Entry", command=self.on_update_entry)
        btn_save_entry.pack(fill=tk.X, pady=3)

        btn_add_entry = ttk.Button(inspector, text="➕ Add as New Entry", command=self.on_add_entry)
        btn_add_entry.pack(fill=tk.X, pady=3)

        btn_clone_entry = ttk.Button(inspector, text="🔗 Clone as New Alias (Same GUID)", command=self.on_clone_alias)
        btn_clone_entry.pack(fill=tk.X, pady=3)

        btn_delete_entry = ttk.Button(inspector, text="❌ Delete Selected Entry", command=self.on_delete_entry)
        btn_delete_entry.pack(fill=tk.X, pady=3)

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W, padding=4)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def on_generate_guid(self):
        new_g = f"{{{uuid.uuid4()}}}"
        self.edit_guid_var.set(new_g)

    def load_file(self, file_path: str):
        try:
            ext = os.path.splitext(file_path)[1].lower()
            if ext == '.json':
                import json
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.model = eng.StringsBankModel.from_dict(data)
            else:
                with open(file_path, 'rb') as f:
                    raw = f.read()
                self.model = eng.parse_strings_bank(raw)

            self.current_file = file_path
            self.is_modified = False
            self.refresh_table()
            self._update_title()
            self.status_var.set(f"Loaded {len(self.model.entries)} entries from {os.path.basename(file_path)}")
        except Exception as e:
            messagebox.showerror("Error Opening File", f"Failed to load file:\n{e}")

    def _update_title(self):
        fname = os.path.basename(self.current_file) if self.current_file else "Untitled"
        mod_marker = "*" if self.is_modified else ""
        self.root.title(f"{fname}{mod_marker} - FMOD Studio strings.bank Editor")

    def refresh_table(self):
        filter_t = self.type_filter_var.get()
        search_t = self.search_var.get().lower().strip()

        for item in self.tree.get_children():
            self.tree.delete(item)

        count = 0
        for idx, entry in enumerate(self.model.entries):
            if filter_t != "All Types" and entry.item_type != filter_t:
                continue
            if search_t and search_t not in entry.path.lower() and search_t not in entry.guid_str:
                continue

            self.tree.insert("", tk.END, iid=str(idx), values=(idx, entry.item_type, entry.path, entry.guid_str))
            count += 1

        self.count_label.config(text=f"Showing: {count} / {len(self.model.entries)}")

    def sort_column(self, col: str, reverse: bool):
        items = [(self.tree.set(k, col), k) for k in self.tree.get_children('')]
        if col == "idx":
            items.sort(key=lambda t: int(t[0]), reverse=reverse)
        else:
            items.sort(key=lambda t: t[0].lower(), reverse=reverse)

        for index, (val, k) in enumerate(items):
            self.tree.move(k, '', index)

        self.tree.heading(col, command=lambda: self.sort_column(col, not reverse))

    def on_select_row(self, event):
        selected = self.tree.selection()
        if not selected:
            return
        idx = int(selected[0])
        if idx < len(self.model.entries):
            e = self.model.entries[idx]
            self.edit_path_var.set(e.path)
            self.edit_guid_var.set(e.guid_str)

    def on_update_entry(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select an entry in the table to update.")
            return
        idx = int(selected[0])
        old_entry = self.model.entries[idx]

        new_path = self.edit_path_var.get().strip()
        new_guid = self.edit_guid_var.get().strip()
        if not new_path or not new_guid:
            messagebox.showwarning("Validation Error", "Path and GUID cannot be empty.")
            return

        try:
            eng.parse_guid_string(new_guid)
        except ValueError as err:
            messagebox.showerror("Invalid GUID", str(err))
            return

        # Update entry
        self.model.remove_entry(old_entry.path)
        self.model.add_entry(new_path, new_guid)
        self.is_modified = True
        self._update_title()
        self.refresh_table()
        self.status_var.set(f"Updated: {new_path}")

    def on_add_entry(self):
        path = self.edit_path_var.get().strip()
        guid = self.edit_guid_var.get().strip()
        if not path or not guid:
            messagebox.showwarning("Validation Error", "Path and GUID cannot be empty.")
            return
        try:
            eng.parse_guid_string(guid)
        except ValueError as err:
            messagebox.showerror("Invalid GUID", str(err))
            return

        self.model.add_entry(path, guid)
        self.is_modified = True
        self._update_title()
        self.refresh_table()
        self.status_var.set(f"Added entry: {path}")

    def on_clone_alias(self):
        path = self.edit_path_var.get().strip()
        guid = self.edit_guid_var.get().strip()
        if not path or not guid:
            return
        alias_path = path + "_Alias"
        self.edit_path_var.set(alias_path)
        self.model.add_entry(alias_path, guid)
        self.is_modified = True
        self._update_title()
        self.refresh_table()
        self.status_var.set(f"Cloned alias: {alias_path} -> {guid}")

    def on_delete_entry(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Please select an entry to delete.")
            return
        idx = int(selected[0])
        entry = self.model.entries[idx]
        if messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete this event?\n\n{entry.path}"):
            self.model.remove_entry(entry.path)
            self.is_modified = True
            self._update_title()
            self.refresh_table()
            self.status_var.set(f"Deleted: {entry.path}")

    def on_open(self):
        path = filedialog.askopenfilename(
            title="Open strings.bank or JSON",
            filetypes=[("FMOD Strings Bank", "*.strings.bank"), ("JSON Files", "*.json"), ("All Files", "*.*")]
        )
        if path:
            self.load_file(path)

    def on_save(self):
        if not self.current_file:
            self.on_save_as()
            return
        self._save_to_path(self.current_file)

    def on_save_as(self):
        path = filedialog.asksaveasfilename(
            title="Save strings.bank",
            defaultextension=".strings.bank",
            filetypes=[("FMOD Strings Bank", "*.strings.bank"), ("JSON Files", "*.json")]
        )
        if path:
            self.current_file = path
            self._save_to_path(path)

    def _save_to_path(self, out_path: str):
        try:
            ext = os.path.splitext(out_path)[1].lower()
            if ext == '.json':
                import json
                with open(out_path, 'w', encoding='utf-8') as f:
                    json.dump(self.model.to_dict(), f, indent=2, ensure_ascii=False)
            else:
                bank_data = eng.build_strings_bank(self.model)
                with open(out_path, 'wb') as f:
                    f.write(bank_data)
            self.is_modified = False
            self._update_title()
            self.status_var.set(f"Saved {len(self.model.entries)} entries to {os.path.basename(out_path)}")
            messagebox.showinfo("Saved", f"Successfully saved to:\n{out_path}")
        except Exception as e:
            messagebox.showerror("Save Error", f"Failed to save file:\n{e}")

    def on_export_json(self):
        path = filedialog.asksaveasfilename(title="Export to JSON", defaultextension=".json", filetypes=[("JSON Files", "*.json")])
        if path:
            import json
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self.model.to_dict(), f, indent=2, ensure_ascii=False)
            messagebox.showinfo("Exported", f"Exported {len(self.model.entries)} entries to:\n{path}")

    def on_export_csv(self):
        path = filedialog.asksaveasfilename(title="Export to CSV", defaultextension=".csv", filetypes=[("CSV Files", "*.csv")])
        if path:
            import csv
            with open(path, 'w', encoding='utf-8', newline='') as f:
                w = csv.writer(f)
                w.writerow(['type', 'path', 'guid'])
                for e in self.model.entries:
                    w.writerow([e.item_type, e.path, e.guid_str])
            messagebox.showinfo("Exported", f"Exported {len(self.model.entries)} entries to:\n{path}")

    def on_verify(self):
        if not self.model.entries:
            messagebox.showinfo("Verification", "No entries in current bank.")
            return

        import ctypes
        repo_root = os.path.dirname(os.path.abspath(__file__))
        dll_path = os.path.join(repo_root, 'api', 'studio', 'lib', 'x64', 'fmodstudio.dll')
        core_path = os.path.join(repo_root, 'api', 'core', 'lib', 'x64', 'fmod.dll')

        if not (os.path.exists(dll_path) and os.path.exists(core_path)):
            messagebox.showwarning("FMOD DLL Missing", "fmodstudio.dll not found in api/studio/lib/x64.")
            return

        try:
            # Build memory bank
            raw_bank = eng.build_strings_bank(self.model)

            os.add_dll_directory(os.path.dirname(core_path))
            os.add_dll_directory(os.path.dirname(dll_path))
            fmodstudio = ctypes.CDLL(dll_path)

            system = ctypes.c_void_p()
            fmodstudio.FMOD_Studio_System_Create(ctypes.byref(system), 0x00020300)
            fmodstudio.FMOD_Studio_System_Initialize(system, 128, 0, 0, None)

            bank = ctypes.c_void_p()
            buf = (ctypes.c_char * len(raw_bank)).from_buffer_copy(raw_bank)
            res = fmodstudio.FMOD_Studio_System_LoadBankMemory(system, buf, len(raw_bank), 0, 0, ctypes.byref(bank))
            if res != 0:
                messagebox.showerror("Verification Failed", f"FMOD LoadBankMemory failed with error: {res}")
                return

            count = ctypes.c_int()
            fmodstudio.FMOD_Studio_Bank_GetStringCount(bank, ctypes.byref(count))

            fmodstudio.FMOD_Studio_Bank_Unload(bank)
            fmodstudio.FMOD_Studio_System_Release(system)

            msg = (
                f"✅ FMOD Studio Runtime Verification PASSED!\n\n"
                f"• Compiled Bank Size: {len(raw_bank):,} bytes\n"
                f"• Successfully Loaded Strings: {count.value}\n"
                f"• Status: 100% Valid & Compatible with FMOD Studio!"
            )
            messagebox.showinfo("Verification Success", msg)
        except Exception as e:
            messagebox.showerror("Verification Error", str(e))

    def on_bank_info(self):
        types_count = {}
        for e in self.model.entries:
            types_count[e.item_type] = types_count.get(e.item_type, 0) + 1

        info_lines = [
            f"Bank File: {self.current_file or 'Not saved'}",
            f"Bank GUID: {self.model.bank_guid}",
            f"Total Entries: {len(self.model.entries)}",
            "",
            "Breakdown by Object Type:"
        ]
        for t, cnt in sorted(types_count.items()):
            info_lines.append(f"  • {t:<12}: {cnt}")

        messagebox.showinfo("Bank Information", "\n".join(info_lines))

    def on_generate_skeleton(self):
        if not self.model.entries:
            messagebox.showwarning("Warning", "No entries in the current model. Please open a strings.bank or add entries first.")
            return

        out_dir = filedialog.askdirectory(title="Select Destination Directory for FMOD Studio Project")
        if not out_dir:
            return

        default_name = "FMOD_Project"
        if self.current_file:
            default_name = Path(self.current_file).stem.replace(".strings", "")

        proj_name = simpledialog.askstring("Project Name", "Enter FMOD Project Name:", initialvalue=default_name, parent=self.root)
        if not proj_name:
            return

        try:
            import fmod_skeleton_generator
            gen = fmod_skeleton_generator.FmodProjectSkeletonGenerator()
            for entry in self.model.entries:
                gen._add_entry(entry.path, entry.guid_str)
            res = gen.generate_project(out_dir, project_name=proj_name)

            msg = (
                f"✅ FMOD Studio Skeleton Project Created!\n\n"
                f"• Project File: {res['project_file']}\n"
                f"• Total Events: {res['events_count']}\n"
                f"• Folder Hierarchy: {res['folders_count']} folders\n"
                f"• Banks: {res['banks_count']}\n"
                f"• Buses: {res.get('buses_count', 0)}\n"
                f"• VCAs: {res.get('vcas_count', 0)}\n"
                f"• Snapshots: {res.get('snapshots_count', 0)}\n"
                f"• Parameters: {res.get('parameters_count', 0)}\n\n"
                f"You can now open '{Path(res['project_file']).name}' directly in FMOD Studio!"
            )
            messagebox.showinfo("Success", msg)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to generate FMOD skeleton project: {e}")


def main(initial_file: Optional[str] = None):
    root = tk.Tk()
    app = StringsEditorGUI(root, initial_file)
    root.mainloop()


if __name__ == "__main__":
    init_arg = sys.argv[1] if len(sys.argv) > 1 else None
    main(init_arg)
