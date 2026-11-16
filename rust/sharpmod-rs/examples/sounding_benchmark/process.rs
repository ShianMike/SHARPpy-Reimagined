use serde_json::{json, Value};

#[cfg(target_os = "linux")]
pub fn counters() -> std::io::Result<Value> {
    #[repr(C)]
    struct Timespec {
        seconds: std::os::raw::c_long,
        nanoseconds: std::os::raw::c_long,
    }
    unsafe extern "C" {
        fn clock_gettime(clock: i32, output: *mut Timespec) -> i32;
    }
    let mut time = Timespec {
        seconds: 0,
        nanoseconds: 0,
    };
    // SAFETY: CLOCK_PROCESS_CPUTIME_ID=2 on Linux and output has libc's timespec layout.
    if unsafe { clock_gettime(2, &mut time) } != 0 {
        return Err(std::io::Error::last_os_error());
    }
    let status = std::fs::read_to_string("/proc/self/status")?;
    let bytes = |key| {
        status.lines().find_map(|line| {
            line.strip_prefix(key)?
                .split_whitespace()
                .next()?
                .parse::<u64>()
                .ok()
                .map(|kb| kb * 1024)
        })
    };
    Ok(json!({
        "cpu_seconds": time.seconds as f64 + time.nanoseconds as f64 / 1e9,
        "working_set_bytes": bytes("VmRSS:"), "peak_working_set_bytes": bytes("VmHWM:"),
        "private_bytes": null,
    }))
}

#[cfg(windows)]
pub fn counters() -> std::io::Result<Value> {
    #[repr(C)]
    struct Memory {
        size: u32,
        faults: u32,
        values: [usize; 9],
    }
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetCurrentProcess() -> *mut std::ffi::c_void;
        fn GetProcessTimes(
            process: *mut std::ffi::c_void,
            created: *mut u64,
            exited: *mut u64,
            kernel: *mut u64,
            user: *mut u64,
        ) -> i32;
        fn K32GetProcessMemoryInfo(
            process: *mut std::ffi::c_void,
            memory: *mut Memory,
            size: u32,
        ) -> i32;
    }
    let mut memory = Memory {
        size: std::mem::size_of::<Memory>() as u32,
        faults: 0,
        values: [0; 9],
    };
    let (mut created, mut exited, mut kernel, mut user) = (0, 0, 0, 0);
    let size = memory.size;
    // SAFETY: the process pseudo-handle is borrowed and all output buffers have the Windows ABI layout.
    unsafe {
        let process = GetCurrentProcess();
        if GetProcessTimes(process, &mut created, &mut exited, &mut kernel, &mut user) == 0
            || K32GetProcessMemoryInfo(process, &mut memory, size) == 0
        {
            return Err(std::io::Error::last_os_error());
        }
    }
    Ok(json!({"cpu_seconds": (kernel + user) as f64 / 10_000_000.,
        "working_set_bytes": memory.values[1], "peak_working_set_bytes": memory.values[0],
        "private_bytes": memory.values[8]}))
}

#[cfg(not(any(target_os = "linux", windows)))]
pub fn counters() -> std::io::Result<Value> {
    Ok(json!({"cpu_seconds": null, "working_set_bytes": null,
        "peak_working_set_bytes": null, "private_bytes": null}))
}
