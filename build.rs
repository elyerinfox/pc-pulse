fn main() {
    // The release asset name contains the target triple; the updater looks for its own.
    println!(
        "cargo:rustc-env=PC_PULSE_TARGET={}",
        std::env::var("TARGET").unwrap()
    );
}
