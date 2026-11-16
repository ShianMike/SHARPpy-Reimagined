//! A child process uses its own noninteractive window station and clipboard.

use super::*;
use std::process::Command;

#[link(name = "user32")]
unsafe extern "system" {
    fn CreateWindowStationW(
        name: *const u16,
        flags: u32,
        access: u32,
        attributes: *const c_void,
    ) -> Handle;
    fn SetProcessWindowStation(station: Handle) -> i32;
    fn GetProcessWindowStation() -> Handle;
    fn CreateDesktopW(
        name: *const u16,
        device: *const u16,
        devmode: *const c_void,
        flags: u32,
        access: u32,
        attributes: *const c_void,
    ) -> Handle;
    fn SetThreadDesktop(desktop: Handle) -> i32;
    fn CreateWindowExW(
        ex_style: u32,
        class: *const u16,
        title: *const u16,
        style: u32,
        x: i32,
        y: i32,
        width: i32,
        height: i32,
        parent: Handle,
        menu: Handle,
        instance: Handle,
        param: *const c_void,
    ) -> Handle;
}

#[link(name = "kernel32")]
unsafe extern "system" {
    fn GlobalSize(memory: Handle) -> usize;
}

const GIF: &[u8] = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b";
const CHILD_FLAG: &str = "SHARPPY_ISOLATED_GIF_CLIPBOARD_TEST";

#[test]
#[ignore = "requires creating an isolated Windows window station"]
fn isolated_windows_clipboard_round_trip() {
    if std::env::var_os(CHILD_FLAG).is_some() {
        run_child();
        return;
    }
    let output = Command::new(std::env::current_exe().unwrap())
        .args([
            "isolated_windows_clipboard_round_trip",
            "--ignored",
            "--nocapture",
        ])
        .env(CHILD_FLAG, "1")
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "isolated child failed:\n{}\n{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
}

fn run_child() {
    let name: Vec<u16> = format!("SHARPpy-Clipboard-Test-{}\0", std::process::id())
        .encode_utf16()
        .collect();
    let desktop_name: Vec<u16> = "Private\0".encode_utf16().collect();
    let class: Vec<u16> = "STATIC\0".encode_utf16().collect();
    let title: Vec<u16> = "GIF test\0".encode_utf16().collect();
    // SAFETY: only this dedicated child process switches to a newly created,
    // noninteractive window station with its own clipboard. All strings are
    // NUL-terminated and each handle is checked before it is used.
    unsafe {
        let original = GetProcessWindowStation();
        // Request only enumeration, desktop creation, clipboard and atom access.
        let station = CreateWindowStationW(name.as_ptr(), 0, 0x2f, ptr::null());
        assert!(
            !station.is_null(),
            "CreateWindowStationW: {}",
            io::Error::last_os_error()
        );
        assert_ne!(station, original);
        assert_ne!(
            SetProcessWindowStation(station),
            0,
            "SetProcessWindowStation: {}",
            io::Error::last_os_error()
        );
        assert_eq!(GetProcessWindowStation(), station);
        let desktop = CreateDesktopW(
            desktop_name.as_ptr(),
            ptr::null(),
            ptr::null(),
            0,
            0x10000000,
            ptr::null(),
        );
        assert!(
            !desktop.is_null(),
            "CreateDesktopW: {}",
            io::Error::last_os_error()
        );
        assert_ne!(
            SetThreadDesktop(desktop),
            0,
            "SetThreadDesktop: {}",
            io::Error::last_os_error()
        );
        let hwnd = CreateWindowExW(
            0,
            class.as_ptr(),
            title.as_ptr(),
            0,
            0,
            0,
            1,
            1,
            ptr::null_mut(),
            ptr::null_mut(),
            ptr::null_mut(),
            ptr::null(),
        );
        assert!(
            !hwnd.is_null(),
            "CreateWindowExW: {}",
            io::Error::last_os_error()
        );

        let directory =
            std::env::temp_dir().join(format!("sharppy-private-gif-test-{}", std::process::id()));
        let mut bytes = GIF[..GIF.len() - 1].to_vec();
        bytes.extend_from_slice(&GIF[19..]);
        super::super::gif::validate(&bytes).unwrap();
        copy(&directory, hwnd as usize, &bytes).unwrap();
        let first;
        {
            let clipboard = Clipboard::open(hwnd).unwrap();
            let format: Vec<u16> = "GIF\0".encode_utf16().collect();
            let gif_format = RegisterClipboardFormatW(format.as_ptr());
            let memory = GetClipboardData(gif_format);
            assert!(!memory.is_null());
            assert!(GlobalSize(memory) >= bytes.len());
            let data = GlobalLock(memory) as *const u8;
            assert!(!data.is_null());
            assert_eq!(
                std::slice::from_raw_parts(data, bytes.len()),
                bytes.as_slice()
            );
            GlobalUnlock(memory);
            let paths = clipboard.protected_files().unwrap();
            assert_eq!(paths.len(), 1);
            first = paths[0].clone();
            assert_eq!(std::fs::read(&first).unwrap(), bytes);
        }
        // Startup recovery must not remove the path still on the clipboard.
        cleanup(&directory).unwrap();
        assert!(first.exists());
        copy(&directory, hwnd as usize, &bytes).unwrap();
        let second = {
            let clipboard = Clipboard::open(hwnd).unwrap();
            clipboard.protected_files().unwrap().remove(0)
        };
        assert_ne!(first, second);
        let old = SystemTime::now() - Duration::from_secs(11 * 60);
        std::fs::OpenOptions::new()
            .write(true)
            .open(&first)
            .unwrap()
            .set_modified(old)
            .unwrap();
        std::fs::OpenOptions::new()
            .write(true)
            .open(&second)
            .unwrap()
            .set_modified(old)
            .unwrap();
        cleanup(&directory).unwrap();
        assert!(!first.exists());
        assert!(second.exists());
        {
            let _clipboard = Clipboard::open(hwnd).unwrap();
            assert_ne!(EmptyClipboard(), 0);
        }
        cleanup(&directory).unwrap();
        assert!(!second.exists());
        std::fs::remove_dir(directory).unwrap();
    }
}
