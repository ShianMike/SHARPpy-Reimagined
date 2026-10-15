// No console window opens behind the app, in debug builds as well: logs go to
// the developer log file, and `tauri dev` still receives stdout through the
// handles it passes to the process.
#![windows_subsystem = "windows"]

fn main() {
    sharppy_reimagined_lib::run();
}
