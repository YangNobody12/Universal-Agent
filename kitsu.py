#!/usr/bin/env python3
"""
Universal Autocomplete with Kitsu, the on-screen companion.

Runs the regular autocomplete app (universal_autocomplete.py, unchanged) and puts Kitsu on the screen as
its main UI. Run universal_autocomplete.py directly for the classic control-center window instead.
"""

import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import universal_autocomplete as ua
import npc_companion

if __name__ == "__main__":
    cfg = ua.load_config()
    if cfg.get("run_as_admin") and not ua.is_admin():
        # Same as universal_autocomplete.py: relaunch elevated so admin programs work too
        args = " ".join(f'"{a}"' for a in [os.path.abspath(__file__)] + sys.argv[1:])
        if ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, args, None, 1) > 32:
            sys.exit(0)
        print("[admin] elevation declined; running without admin rights")
    app = ua.UniversalAutocompleteApp(cfg)
    npc = npc_companion.attach(app)
    try:
        app.run()
    finally:
        npc.shutdown()  # close the Flet chat window process too
