mod api;
mod autostart;
mod collector;
mod config;
mod discovery;
mod gpu;
mod hardware;
mod notify;
mod osupdates;
mod platform;
mod update;

use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use tokio::sync::RwLock;

use crate::collector::Collector;
use crate::config::Config;

#[derive(Parser)]
#[command(
    version,
    about = "Home Assistant agent: reports system info and accepts power commands"
)]
struct Cli {
    /// Path to the config file (created on first run).
    #[arg(long, global = true)]
    config: Option<PathBuf>,
    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Subcommand)]
enum Command {
    /// Run the agent (default).
    Run {
        /// Detach from the console and log to a file next to the config (used by autostart).
        #[arg(long)]
        background: bool,
    },
    /// Print connection details for Home Assistant.
    Info,
    /// Generate a new access token.
    RegenerateToken,
    /// Print a single status sample as JSON and exit.
    Sample,
    /// Start the agent automatically at login.
    InstallAutostart,
    /// Remove the login autostart entry.
    UninstallAutostart,
    /// Check GitHub for a newer release and install it.
    Update {
        /// Only report whether an update is available.
        #[arg(long)]
        check: bool,
    },
}

fn print_info(cfg: &Config, path: &std::path::Path) {
    let ip = local_ip_address::local_ip()
        .map(|ip| ip.to_string())
        .unwrap_or_else(|_| "<this machine's IP>".into());
    println!("Config file : {}", path.display());
    println!("Name        : {}", cfg.name);
    println!("Host        : {ip}");
    println!("Port        : {}", cfg.port);
    println!("Token       : {}", cfg.token);
}

fn init_logging(background: bool, config_path: &std::path::Path) -> Result<()> {
    let builder = tracing_subscriber::fmt()
        .with_target(false)
        .with_max_level(tracing::Level::INFO);
    if background {
        #[cfg(windows)]
        unsafe {
            windows_sys::Win32::System::Console::FreeConsole();
        }
        let log_path = config_path.with_file_name("agent.log");
        if let Some(dir) = log_path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        // Start fresh when the log gets large.
        if std::fs::metadata(&log_path).is_ok_and(|m| m.len() > 5 * 1024 * 1024) {
            let _ = std::fs::remove_file(&log_path);
        }
        let file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_path)
            .with_context(|| format!("opening {}", log_path.display()))?;
        builder
            .with_ansi(false)
            .with_writer(Mutex::new(file))
            .init();
    } else {
        builder.with_writer(std::io::stderr).init();
    }
    Ok(())
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();
    let path = cli.config.unwrap_or_else(Config::default_path);
    let command = cli.command.unwrap_or(Command::Run { background: false });
    let background = matches!(command, Command::Run { background: true });
    init_logging(background, &path)?;
    let mut cfg = Config::load_or_create(&path)?;

    match command {
        Command::Run { .. } => {}
        Command::Info => {
            print_info(&cfg, &path);
            return Ok(());
        }
        Command::RegenerateToken => {
            cfg.token = config::random_hex(24);
            cfg.save(&path)?;
            println!("New token: {}", cfg.token);
            println!("Restart the agent and re-authenticate in Home Assistant.");
            return Ok(());
        }
        Command::Sample => {
            let mut c = Collector::new();
            c.collect(&cfg);
            std::thread::sleep(
                sysinfo::MINIMUM_CPU_UPDATE_INTERVAL.max(std::time::Duration::from_millis(500)),
            );
            println!("{}", serde_json::to_string_pretty(&c.collect(&cfg))?);
            return Ok(());
        }
        Command::InstallAutostart => {
            let exe = std::env::current_exe()?;
            return autostart::install(&exe, &std::path::absolute(&path)?);
        }
        Command::UninstallAutostart => return autostart::uninstall(),
        Command::Update { check } => {
            let status = update::new_shared(&cfg);
            let release = update::check(&cfg, &status)?;
            let latest = status.lock().unwrap().latest.clone().unwrap_or_default();
            match release {
                None => println!(
                    "pc-pulse {} is up to date (latest: {latest})",
                    update::VERSION
                ),
                Some(r) if check => {
                    println!("Update available: {} -> {}", update::VERSION, r.version)
                }
                Some(r) => {
                    update::install(&r)?;
                    println!("Installed {}. Restart the agent to use it.", r.version);
                }
            }
            return Ok(());
        }
    }
    update::cleanup();

    let cfg = Arc::new(cfg);
    let shared = Arc::new(RwLock::new(None));
    let collector = Arc::new(Mutex::new(Collector::new()));
    collector::spawn(cfg.clone(), collector.clone(), shared.clone());
    let update_status = update::new_shared(&cfg);
    update::spawn_checker(cfg.clone(), update_status.clone());
    let os_updates = Arc::new(Mutex::new(None));
    osupdates::spawn_checker(cfg.clone(), os_updates.clone());

    let _mdns = if cfg.mdns {
        match discovery::advertise(&cfg) {
            Ok(d) => Some(d),
            Err(e) => {
                tracing::warn!("mDNS advertisement failed: {e:#}");
                None
            }
        }
    } else {
        None
    };

    let addr: SocketAddr = format!("{}:{}", cfg.bind, cfg.port)
        .parse()
        .context("invalid bind address")?;
    let app = api::router(api::AppState {
        cfg: cfg.clone(),
        collector,
        shared,
        update: update_status,
        os_updates,
    });
    // After a self-update the previous process may still hold the port for a moment.
    let mut attempt = 0;
    let listener = loop {
        match tokio::net::TcpListener::bind(addr).await {
            Ok(l) => break l,
            Err(e) if attempt < 30 && e.kind() == std::io::ErrorKind::AddrInUse => {
                attempt += 1;
                tokio::time::sleep(std::time::Duration::from_secs(1)).await;
            }
            Err(e) => return Err(e).with_context(|| format!("binding {addr}")),
        }
    };
    print_info(&cfg, &path);
    tracing::info!("listening on http://{addr}");
    axum::serve(listener, app)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    if let Some(d) = _mdns {
        let _ = d.shutdown();
    }
    Ok(())
}
