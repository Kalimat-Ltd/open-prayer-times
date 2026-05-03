# pylint: disable=broad-exception-caught,protected-access,unused-argument
"""CSV persistence helpers for safe locations.csv rewrite with backup/restore semantics."""

import csv
import os
import shutil
from tkinter import messagebox

from src.app.presentation.gui.constants import FIELD_NAMES


def rewrite_location_file(app):
    try:
        if os.path.exists(app.location_file):
            shutil.copy2(app.location_file, app.location_file_backup)
    except Exception as error:
        messagebox.showerror(
            "Backup Error",
            f"Could not create backup file '{app.location_file_backup}':\n{error}",
        )
        return False

    try:
        with open(app.location_file, "w", encoding="utf-8", newline="") as file_obj:
            writer = csv.writer(file_obj, quoting=csv.QUOTE_MINIMAL)
            writer.writerow(FIELD_NAMES)
            sorted_data = sorted(app.locations_data, key=lambda loc: loc["id"])
            for location in sorted_data:
                writer.writerow(app._format_location_for_file(location))

        if os.path.exists(app.location_file_backup):
            try:
                os.remove(app.location_file_backup)
            except OSError as error:
                print(f"Warning: Could not remove backup file: {error}")

        return True
    except Exception as error:
        messagebox.showerror(
            "Error Saving File",
            f"An error occurred while writing to '{app.location_file}':\n{error}\n\nAttempting to restore from backup.",
        )
        if os.path.exists(app.location_file_backup):
            try:
                shutil.copy2(app.location_file_backup, app.location_file)
                messagebox.showinfo("Restore", "File restored from backup.")
            except Exception as restore_error:
                messagebox.showerror(
                    "Restore Failed", f"Could not restore from backup: {restore_error}"
                )

        return False
