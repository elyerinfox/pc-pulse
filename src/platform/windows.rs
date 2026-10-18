use std::ffi::c_void;

use windows_sys::Win32::Foundation::{CloseHandle, HANDLE, LUID};
use windows_sys::Win32::Security::{
    AdjustTokenPrivileges, LUID_AND_ATTRIBUTES, LookupPrivilegeValueW, SE_PRIVILEGE_ENABLED,
    TOKEN_ADJUST_PRIVILEGES, TOKEN_PRIVILEGES, TOKEN_QUERY,
};
use windows_sys::Win32::System::Power::{
    GetSystemPowerStatus, SYSTEM_POWER_STATUS, SetSuspendState,
};
use windows_sys::Win32::System::RemoteDesktop::{
    WTS_CURRENT_SERVER_HANDLE, WTS_SESSION_INFOW, WTSActive, WTSDisconnected,
    WTSEnumerateSessionsW, WTSFreeMemory, WTSGetActiveConsoleSessionId, WTSINFOEXW,
    WTSLogoffSession, WTSQuerySessionInformationW, WTSSessionInfoEx, WTSUserName,
};
use windows_sys::Win32::System::Shutdown::LockWorkStation;
use windows_sys::Win32::System::SystemInformation::GetTickCount;
use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
    GetLastInputInfo, INPUT, INPUT_0, INPUT_MOUSE, LASTINPUTINFO, MOUSEEVENTF_MOVE, MOUSEINPUT,
    SendInput,
};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    HWND_BROADCAST, PostMessageW, SC_MONITORPOWER, WM_SYSCOMMAND,
};

use super::{Activity, Battery, PowerAction, run_checked};

const WTS_SESSIONSTATE_LOCK: i32 = 0;
const WTS_SESSIONSTATE_UNLOCK: i32 = 1;

fn idle_seconds() -> Option<u64> {
    let mut info = LASTINPUTINFO {
        cbSize: size_of::<LASTINPUTINFO>() as u32,
        dwTime: 0,
    };
    // Only works when the agent runs inside the interactive user's session.
    if unsafe { GetLastInputInfo(&mut info) } == 0 {
        return None;
    }
    let now = unsafe { GetTickCount() };
    Some(u64::from(now.wrapping_sub(info.dwTime)) / 1000)
}

fn query_session<T, R>(
    session: u32,
    class: i32,
    read: impl FnOnce(*const T, u32) -> R,
) -> Option<R> {
    let mut buf: *mut u16 = std::ptr::null_mut();
    let mut len = 0u32;
    let ok = unsafe {
        WTSQuerySessionInformationW(
            WTS_CURRENT_SERVER_HANDLE,
            session,
            class,
            &mut buf,
            &mut len,
        )
    };
    if ok == 0 || buf.is_null() {
        return None;
    }
    let result = read(buf as *const T, len);
    unsafe { WTSFreeMemory(buf as *mut c_void) };
    Some(result)
}

fn session_user(session: u32) -> Option<String> {
    query_session::<u16, _>(session, WTSUserName, |ptr, len| {
        let chars = (len as usize / 2).saturating_sub(1);
        let slice = unsafe { std::slice::from_raw_parts(ptr, chars) };
        String::from_utf16_lossy(slice)
    })
    .filter(|u| !u.is_empty())
}

fn session_locked(session: u32) -> Option<bool> {
    query_session::<WTSINFOEXW, _>(session, WTSSessionInfoEx, |ptr, _| {
        let info = unsafe { &*ptr };
        if info.Level != 1 {
            return None;
        }
        match unsafe { info.Data.WTSInfoExLevel1.SessionFlags } {
            WTS_SESSIONSTATE_LOCK => Some(true),
            WTS_SESSIONSTATE_UNLOCK => Some(false),
            _ => None,
        }
    })
    .flatten()
}

fn console_session() -> Option<u32> {
    let id = unsafe { WTSGetActiveConsoleSessionId() };
    (id != u32::MAX).then_some(id)
}

pub fn activity() -> Activity {
    Activity {
        idle_seconds: idle_seconds(),
        locked: console_session().and_then(session_locked),
    }
}

pub fn logged_in_users() -> Vec<String> {
    let mut ptr: *mut WTS_SESSION_INFOW = std::ptr::null_mut();
    let mut count = 0u32;
    if unsafe { WTSEnumerateSessionsW(WTS_CURRENT_SERVER_HANDLE, 0, 1, &mut ptr, &mut count) } == 0
    {
        return Vec::new();
    }
    let sessions = unsafe { std::slice::from_raw_parts(ptr, count as usize) };
    let users = sessions
        .iter()
        .filter(|s| s.State == WTSActive || s.State == WTSDisconnected)
        .filter_map(|s| session_user(s.SessionId))
        .collect();
    unsafe { WTSFreeMemory(ptr as *mut c_void) };
    users
}

pub fn supported_actions() -> Vec<PowerAction> {
    PowerAction::ALL.to_vec()
}

/// Suspend requires SeShutdownPrivilege to be enabled on the process token.
fn enable_shutdown_privilege() {
    unsafe {
        let mut token: HANDLE = std::ptr::null_mut();
        if OpenProcessToken(
            GetCurrentProcess(),
            TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
            &mut token,
        ) == 0
        {
            return;
        }
        let name: Vec<u16> = "SeShutdownPrivilege\0".encode_utf16().collect();
        let mut luid = LUID {
            LowPart: 0,
            HighPart: 0,
        };
        if LookupPrivilegeValueW(std::ptr::null(), name.as_ptr(), &mut luid) != 0 {
            let privileges = TOKEN_PRIVILEGES {
                PrivilegeCount: 1,
                Privileges: [LUID_AND_ATTRIBUTES {
                    Luid: luid,
                    Attributes: SE_PRIVILEGE_ENABLED,
                }],
            };
            AdjustTokenPrivileges(
                token,
                0,
                &privileges,
                0,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
            );
        }
        CloseHandle(token);
    }
}

fn suspend(hibernate: bool) -> anyhow::Result<()> {
    enable_shutdown_privilege();
    let ok = unsafe { SetSuspendState(hibernate, false, false) };
    anyhow::ensure!(
        ok,
        "SetSuspendState failed: {}",
        std::io::Error::last_os_error()
    );
    Ok(())
}

fn wiggle_mouse() -> anyhow::Result<()> {
    let make = |dx: i32| INPUT {
        r#type: INPUT_MOUSE,
        Anonymous: INPUT_0 {
            mi: MOUSEINPUT {
                dx,
                dy: 0,
                mouseData: 0,
                dwFlags: MOUSEEVENTF_MOVE,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    };
    let inputs = [make(1), make(-1)];
    let sent = unsafe {
        SendInput(
            inputs.len() as u32,
            inputs.as_ptr(),
            size_of::<INPUT>() as i32,
        )
    };
    anyhow::ensure!(sent as usize == inputs.len(), "SendInput failed");
    Ok(())
}

pub fn execute(action: PowerAction) -> anyhow::Result<()> {
    match action {
        PowerAction::Shutdown => run_checked("shutdown", &["/s", "/t", "0"]),
        PowerAction::Restart => run_checked("shutdown", &["/r", "/t", "0"]),
        PowerAction::Sleep => suspend(false),
        PowerAction::Hibernate => suspend(true),
        PowerAction::Lock => {
            if unsafe { LockWorkStation() } != 0 {
                Ok(())
            } else {
                anyhow::bail!(
                    "LockWorkStation failed (the agent must run in the user's session): {}",
                    std::io::Error::last_os_error()
                )
            }
        }
        PowerAction::Logoff => {
            let session = console_session().ok_or_else(|| anyhow::anyhow!("no console session"))?;
            if unsafe { WTSLogoffSession(WTS_CURRENT_SERVER_HANDLE, session, 0) } != 0 {
                Ok(())
            } else {
                run_checked("shutdown", &["/l"])
            }
        }
        PowerAction::MonitorOff => {
            unsafe {
                PostMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER as usize, 2);
            }
            Ok(())
        }
        PowerAction::MonitorOn => wiggle_mouse(),
    }
}

pub fn battery() -> Option<Battery> {
    let mut st: SYSTEM_POWER_STATUS = unsafe { std::mem::zeroed() };
    if unsafe { GetSystemPowerStatus(&mut st) } == 0 {
        return None;
    }
    // 128 = no system battery, 255 = unknown.
    if st.BatteryFlag & 128 != 0 || st.BatteryLifePercent > 100 {
        return None;
    }
    Some(Battery {
        percent: st.BatteryLifePercent as f32,
        charging: st.BatteryFlag & 8 != 0,
        plugged_in: match st.ACLineStatus {
            0 => Some(false),
            1 => Some(true),
            _ => None,
        },
        time_remaining_secs: (st.BatteryLifeTime != u32::MAX).then_some(st.BatteryLifeTime as u64),
    })
}
