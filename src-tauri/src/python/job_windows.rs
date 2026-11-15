//! A named, kill-on-close job owns the bridge and its render workers.

use std::os::windows::io::RawHandle;

use windows_sys::Win32::Foundation::{
    CloseHandle, GetLastError, ERROR_ALREADY_EXISTS, ERROR_FILE_NOT_FOUND, ERROR_INVALID_PARAMETER,
    HANDLE, WAIT_OBJECT_0,
};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectBasicAccountingInformation,
    JobObjectExtendedLimitInformation, OpenJobObjectW, QueryInformationJobObject,
    SetInformationJobObject, TerminateJobObject, JOBOBJECT_BASIC_ACCOUNTING_INFORMATION,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows_sys::Win32::System::Threading::{OpenProcess, WaitForSingleObject};

const JOB_OBJECT_QUERY: u32 = 0x0004;

pub struct Job(HANDLE);

// SAFETY: a job handle is a kernel handle; it may be used and closed from any thread.
unsafe impl Send for Job {}
unsafe impl Sync for Job {}

fn wide_name(name: &str) -> Vec<u16> {
    name.encode_utf16().chain(std::iter::once(0)).collect()
}

impl Job {
    /// Assigns the child to a new, uniquely named job. A pre-existing name is
    /// rejected rather than changing another process's job limits.
    pub fn assign(process: RawHandle, name: &str) -> std::io::Result<Job> {
        let name = wide_name(name);
        // SAFETY: Win32 calls receive valid handles and initialized buffers.
        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), name.as_ptr());
            if handle.is_null() {
                return Err(std::io::Error::last_os_error());
            }
            let job = Job(handle);
            if GetLastError() == ERROR_ALREADY_EXISTS {
                return Err(std::io::Error::from(std::io::ErrorKind::AlreadyExists));
            }
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let size = u32::try_from(std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>())
                .unwrap_or(u32::MAX);
            if SetInformationJobObject(
                job.0,
                JobObjectExtendedLimitInformation,
                std::ptr::from_ref(&info).cast(),
                size,
            ) == 0
            {
                return Err(std::io::Error::last_os_error());
            }
            if AssignProcessToJobObject(job.0, process as HANDLE) == 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(job)
        }
    }

    pub fn active_processes(&self) -> std::io::Result<u32> {
        // SAFETY: the buffer has the size and type required by this information class.
        unsafe {
            let mut info: JOBOBJECT_BASIC_ACCOUNTING_INFORMATION = std::mem::zeroed();
            let size = u32::try_from(std::mem::size_of_val(&info)).unwrap_or(u32::MAX);
            if QueryInformationJobObject(
                self.0,
                JobObjectBasicAccountingInformation,
                std::ptr::from_mut(&mut info).cast(),
                size,
                std::ptr::null_mut(),
            ) == 0
            {
                return Err(std::io::Error::last_os_error());
            }
            Ok(info.ActiveProcesses)
        }
    }

    pub fn terminate_remaining(&self) -> std::io::Result<()> {
        if self.active_processes()? == 0 {
            return Ok(());
        }
        // SAFETY: the live handle belongs to the job created by this launcher.
        if unsafe { TerminateJobObject(self.0, 1) } == 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(())
    }
}

/// A missing job proves that its processes are gone. Access failures and a
/// still-existing job both leave the directory in place for a later attempt.
pub fn is_gone(name: &str) -> bool {
    let name = wide_name(name);
    // SAFETY: `name` is nul terminated and a returned handle is closed once.
    unsafe {
        let handle = OpenJobObjectW(JOB_OBJECT_QUERY, 0, name.as_ptr());
        if handle.is_null() {
            return GetLastError() == ERROR_FILE_NOT_FOUND;
        }
        CloseHandle(handle);
        false
    }
}

/// An inaccessible or reused PID is treated as live; only an absent or
/// signaled process handle proves that this bridge has exited.
pub fn process_is_gone(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    const SYNCHRONIZE: u32 = 0x0010_0000;
    // SAFETY: returned process handles are checked and closed exactly once.
    unsafe {
        let handle = OpenProcess(SYNCHRONIZE, 0, pid);
        if handle.is_null() {
            return GetLastError() == ERROR_INVALID_PARAMETER;
        }
        let gone = WaitForSingleObject(handle, 0) == WAIT_OBJECT_0;
        CloseHandle(handle);
        gone
    }
}

impl Drop for Job {
    fn drop(&mut self) {
        // SAFETY: the handle came from CreateJobObjectW and is closed exactly once.
        unsafe {
            CloseHandle(self.0);
        }
    }
}
