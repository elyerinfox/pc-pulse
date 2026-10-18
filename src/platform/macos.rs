use super::{Activity, PowerAction, output_of, run_checked};

pub fn activity() -> Activity {
    // HIDIdleTime is reported in nanoseconds by the IOHIDSystem registry entry.
    let idle_seconds = output_of("ioreg", &["-c", "IOHIDSystem", "-d", "4"]).and_then(|out| {
        out.lines()
            .find(|l| l.contains("\"HIDIdleTime\""))
            .and_then(|l| l.rsplit('=').next())
            .and_then(|v| v.trim().parse::<u64>().ok())
            .map(|ns| ns / 1_000_000_000)
    });
    Activity {
        idle_seconds,
        locked: None,
    }
}

pub fn logged_in_users() -> Vec<String> {
    output_of("who", &[])
        .unwrap_or_default()
        .lines()
        .filter_map(|l| l.split_whitespace().next().map(str::to_owned))
        .collect()
}

pub fn supported_actions() -> Vec<PowerAction> {
    // macOS has no user-facing hibernate; it decides on its own when to hibernate.
    PowerAction::ALL
        .into_iter()
        .filter(|a| *a != PowerAction::Hibernate)
        .collect()
}

fn system_events(verb: &str) -> anyhow::Result<()> {
    run_checked(
        "osascript",
        &["-e", &format!("tell application \"System Events\" to {verb}")],
    )
}

pub fn execute(action: PowerAction) -> anyhow::Result<()> {
    match action {
        PowerAction::Shutdown => system_events("shut down"),
        PowerAction::Restart => system_events("restart"),
        PowerAction::Sleep | PowerAction::Hibernate => run_checked("pmset", &["sleepnow"]),
        PowerAction::Lock => system_events(
            "keystroke \"q\" using {control down, command down}",
        ),
        PowerAction::Logoff => system_events("log out"),
        PowerAction::MonitorOff => run_checked("pmset", &["displaysleepnow"]),
        PowerAction::MonitorOn => run_checked("caffeinate", &["-u", "-t", "1"]),
    }
}
