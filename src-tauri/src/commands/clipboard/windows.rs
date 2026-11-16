//! Win32 clipboard ownership and CF_HDROP file-paste support.

use std::ffi::c_void;
use std::io;
use std::os::windows::ffi::{OsStrExt, OsStringExt};
use std::path::{Path, PathBuf};
use std::ptr;
use std::thread;
use std::time::{Duration, SystemTime};

use super::cache::{self, PendingFile};

type Handle = *mut c_void;
const CF_HDROP: u32 = 15;
const GMEM_MOVEABLE: u32 = 0x0002;
const WAIT_OBJECT_0: u32 = 0;
const WAIT_ABANDONED: u32 = 0x80;

#[link(name = "user32")]
unsafe extern "system" {
    fn OpenClipboard(hwnd: Handle) -> i32;
    fn CloseClipboard() -> i32;
    fn EmptyClipboard() -> i32;
    fn SetClipboardData(format: u32, data: Handle) -> Handle;
    fn GetClipboardData(format: u32) -> Handle;
    fn IsClipboardFormatAvailable(format: u32) -> i32;
    fn RegisterClipboardFormatW(name: *const u16) -> u32;
}

#[link(name = "kernel32")]
unsafe extern "system" {
    fn GlobalAlloc(flags: u32, bytes: usize) -> Handle;
    fn GlobalLock(memory: Handle) -> *mut c_void;
    fn GlobalUnlock(memory: Handle) -> i32;
    fn GlobalFree(memory: Handle) -> Handle;
    fn SetLastError(error: u32);
    fn GetLastError() -> u32;
    fn CreateMutexW(attributes: *const c_void, initial_owner: i32, name: *const u16) -> Handle;
    fn WaitForSingleObject(handle: Handle, milliseconds: u32) -> u32;
    fn ReleaseMutex(handle: Handle) -> i32;
    fn CloseHandle(handle: Handle) -> i32;
}

#[link(name = "shell32")]
unsafe extern "system" {
    fn DragQueryFileW(drop: Handle, index: u32, path: *mut u16, capacity: u32) -> u32;
}

fn error(action: &str) -> String {
    format!("{action}: {}", io::Error::last_os_error())
}

struct CacheLock(Handle);

impl CacheLock {
    fn acquire() -> Result<Self, String> {
        let name: Vec<u16> = "Local\\SHARPpy-Reimagined-ForecastGifClipboard-v1\0"
            .encode_utf16()
            .collect();
        // SAFETY: the name is NUL-terminated and default security is requested.
        let handle = unsafe { CreateMutexW(ptr::null(), 0, name.as_ptr()) };
        if handle.is_null() {
            return Err(error("Cannot lock the GIF clipboard cache"));
        }
        // SAFETY: handle is a valid mutex until it is closed below or in Drop.
        let result = unsafe { WaitForSingleObject(handle, 5000) };
        if result != WAIT_OBJECT_0 && result != WAIT_ABANDONED {
            unsafe { CloseHandle(handle) };
            return Err("The GIF clipboard cache is busy; try again.".to_owned());
        }
        Ok(Self(handle))
    }
}

impl Drop for CacheLock {
    fn drop(&mut self) {
        // SAFETY: this object owns the acquired mutex and its handle.
        unsafe {
            ReleaseMutex(self.0);
            CloseHandle(self.0);
        }
    }
}

struct Clipboard;

impl Clipboard {
    fn open(hwnd: Handle) -> Result<Self, String> {
        for attempt in 0..10 {
            // SAFETY: a real application HWND is used for writes; NULL is
            // allowed for the read-only startup inspection.
            if unsafe { OpenClipboard(hwnd) } != 0 {
                return Ok(Self);
            }
            if attempt != 9 {
                thread::sleep(Duration::from_millis(50));
            }
        }
        Err("The clipboard is in use by another application; try again.".to_owned())
    }

    fn protected_files(&self) -> Result<Vec<PathBuf>, String> {
        // SAFETY: the clipboard is open, and its returned handle is borrowed
        // until this Clipboard is dropped.
        unsafe {
            if IsClipboardFormatAvailable(CF_HDROP) == 0 {
                return Ok(Vec::new());
            }
            let drop = GetClipboardData(CF_HDROP);
            if drop.is_null() {
                return Err(error("Cannot inspect clipboard file paths"));
            }
            let count = DragQueryFileW(drop, u32::MAX, ptr::null_mut(), 0);
            if count == 0 || count > 4096 {
                return Err("Cannot inspect clipboard file paths safely.".to_owned());
            }
            let mut paths = Vec::with_capacity(count as usize);
            for index in 0..count {
                let len = DragQueryFileW(drop, index, ptr::null_mut(), 0);
                if len == 0 || len > 32767 {
                    return Err("Cannot inspect a clipboard file path safely.".to_owned());
                }
                let mut path = vec![0u16; len as usize + 1];
                if DragQueryFileW(drop, index, path.as_mut_ptr(), len + 1) != len {
                    return Err("Cannot inspect a clipboard file path safely.".to_owned());
                }
                path.truncate(len as usize);
                paths.push(PathBuf::from(std::ffi::OsString::from_wide(&path)));
            }
            Ok(paths)
        }
    }
}

impl Drop for Clipboard {
    fn drop(&mut self) {
        // SAFETY: this guard owns the clipboard opened on the current thread.
        unsafe { CloseClipboard() };
    }
}

struct GlobalMemory(Handle);

impl GlobalMemory {
    fn new(bytes: &[u8]) -> Result<Self, String> {
        // SAFETY: GMEM_MOVEABLE provides the HGLOBAL required by SetClipboardData.
        let handle = unsafe { GlobalAlloc(GMEM_MOVEABLE, bytes.len()) };
        if handle.is_null() {
            return Err(error("Cannot allocate GIF clipboard data"));
        }
        let memory = Self(handle);
        // SAFETY: the allocation is valid and large enough for bytes.
        let destination = unsafe { GlobalLock(handle) } as *mut u8;
        if destination.is_null() {
            return Err(error("Cannot access GIF clipboard data"));
        }
        unsafe {
            ptr::copy_nonoverlapping(bytes.as_ptr(), destination, bytes.len());
            SetLastError(0);
            let still_locked = GlobalUnlock(handle);
            if still_locked == 0 && GetLastError() != 0 {
                return Err(error("Cannot unlock GIF clipboard data"));
            }
        }
        Ok(memory)
    }

    fn transfer(&mut self, format: u32) -> Result<(), String> {
        // SAFETY: clipboard is open and emptied; on success the OS owns the
        // movable memory and it must no longer be freed by this process.
        if unsafe { SetClipboardData(format, self.0) }.is_null() {
            return Err(error("Cannot publish GIF clipboard data"));
        }
        self.0 = ptr::null_mut();
        Ok(())
    }
}

impl Drop for GlobalMemory {
    fn drop(&mut self) {
        if !self.0.is_null() {
            // SAFETY: this handle has not been transferred to the clipboard.
            unsafe { GlobalFree(self.0) };
        }
    }
}

fn file_drop(path: &Path) -> Result<Vec<u8>, String> {
    if !path.is_absolute() {
        return Err("GIF clipboard path is not absolute.".to_owned());
    }
    let name: Vec<u16> = path.as_os_str().encode_wide().collect();
    if name.contains(&0) || name.len() > 32767 {
        return Err("GIF clipboard path is too long or invalid.".to_owned());
    }
    // DROPFILES: pFiles=20, POINT=(0,0), fNC=0, fWide=1; followed by
    // a double-NUL-terminated UTF-16 filename list.
    let mut data = vec![0u8; 20];
    data[0..4].copy_from_slice(&20u32.to_le_bytes());
    data[16..20].copy_from_slice(&1u32.to_le_bytes());
    for word in name.into_iter().chain([0, 0]) {
        data.extend_from_slice(&word.to_le_bytes());
    }
    Ok(data)
}

pub(super) fn copy(directory: &Path, hwnd: usize, bytes: &[u8]) -> Result<(), String> {
    if hwnd == 0 {
        return Err("The application window is unavailable for GIF copying.".to_owned());
    }
    let _lock = CacheLock::acquire()?;
    let mut file = PendingFile::create(directory, bytes)
        .map_err(|error| format!("Cannot save the clipboard GIF: {error}"))?;
    let mut gif = GlobalMemory::new(bytes)?;
    let mut files = GlobalMemory::new(&file_drop(file.path())?)?;
    let format: Vec<u16> = "GIF\0".encode_utf16().collect();
    // SAFETY: format is a valid, NUL-terminated format name.
    let gif_format = unsafe { RegisterClipboardFormatW(format.as_ptr()) };
    if gif_format == 0 {
        return Err(error("Cannot register the GIF clipboard format"));
    }
    let _clipboard = Clipboard::open(hwnd as Handle)?;
    // SAFETY: clipboard is open with the application window as owner.
    if unsafe { EmptyClipboard() } == 0 {
        return Err(error("Cannot clear the clipboard"));
    }
    gif.transfer(gif_format)?;
    files.transfer(CF_HDROP)?;
    file.publish();
    if let Err(error) = cache::cleanup(directory, &[file.path().to_owned()], SystemTime::now()) {
        log::debug!("GIF clipboard cleanup deferred: {error}");
    }
    Ok(())
}

pub(super) fn cleanup(directory: &Path) -> Result<(), String> {
    let _lock = CacheLock::acquire()?;
    // A NULL owner is safe here because startup only reads, never empties.
    let clipboard = Clipboard::open(ptr::null_mut())?;
    let protected = clipboard.protected_files()?;
    cache::cleanup(directory, &protected, SystemTime::now()).map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn file_drop_exposes_a_unicode_path_to_windows_without_touching_the_clipboard() {
        let path = Path::new("C:\\Users\\Example\\天气 forecast.gif");
        let buffer = GlobalMemory::new(&file_drop(path).unwrap()).unwrap();
        // SAFETY: buffer contains a valid DROPFILES allocation and remains owned.
        unsafe {
            assert_eq!(DragQueryFileW(buffer.0, u32::MAX, ptr::null_mut(), 0), 1);
            let length = DragQueryFileW(buffer.0, 0, ptr::null_mut(), 0);
            let mut actual = vec![0u16; length as usize + 1];
            assert_eq!(
                DragQueryFileW(buffer.0, 0, actual.as_mut_ptr(), length + 1),
                length
            );
            actual.truncate(length as usize);
            assert_eq!(std::ffi::OsString::from_wide(&actual), path.as_os_str());
        }
    }
}

#[cfg(test)]
#[path = "windows_private_test.rs"]
mod private_tests;
