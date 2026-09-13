//! Persistent, dependency-free graphical entry for the installed Riff studio.
use std::fs;
use std::io::{self, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

fn port(url: &str) -> io::Result<u16> {
    let value = url.strip_prefix("http://127.0.0.1:")
        .and_then(|value| value.parse::<u16>().ok()).filter(|value| *value != 0);
    value.ok_or_else(|| io::Error::other("Riff's saved studio address is invalid. Reopen Riff Setup."))
}

fn ready(port: u16) -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], port));
    let timeout = Duration::from_millis(500);
    let Ok(mut stream) = TcpStream::connect_timeout(&address, timeout) else { return false; };
    let _ = stream.set_read_timeout(Some(timeout));
    let _ = stream.set_write_timeout(Some(timeout));
    if write!(stream, "GET /api/system HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n").is_err() { return false; }
    const HTTP11: &[u8] = b"HTTP/1.1 200 OK\r";
    const HTTP10: &[u8] = b"HTTP/1.0 200 OK\r";
    let mut status = [0_u8; HTTP11.len()];
    stream.read_exact(&mut status).is_ok() && (status == HTTP11 || status == HTTP10)
}

fn open_studio() -> io::Result<()> {
    let home = std::env::var_os("HOME").ok_or_else(|| io::Error::other("Riff could not find your home folder."))?;
    let root = std::env::var_os("RIFF_INSTALL_ROOT").map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(home).join("Library/Application Support/Riff"));
    let url = fs::read_to_string(root.join("studio-url.txt"))?;
    let url = url.trim();
    let selected_port = port(url)?;
    let label = fs::read_to_string(root.join("studio-service.txt"))?;
    let label = label.trim();
    if label.is_empty() || !label.chars().all(|value| value.is_ascii_alphanumeric() || value == '.' || value == '-') {
        return Err(io::Error::other("Riff's saved startup service is invalid."));
    }
    let uid = Command::new("/usr/bin/id").arg("-u").output()?;
    if !uid.status.success() { return Err(io::Error::other("Riff could not identify its startup service.")); }
    let uid = String::from_utf8_lossy(&uid.stdout);
    let uid = uid.trim();
    if uid.is_empty() || !uid.chars().all(|value| value.is_ascii_digit()) { return Err(io::Error::other("Riff's startup account is invalid.")); }
    // No -k: opening the app must never stop an existing generation.
    let status = Command::new("/bin/launchctl").args(["kickstart", &format!("gui/{uid}/{label}")])
        .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).status()?;
    if !status.success() && !ready(selected_port) { return Err(io::Error::other("Riff's startup service is unavailable. Reopen Riff Setup.")); }
    let startup_ms = fs::read_to_string(root.join("startup-timeout-ms.txt")).ok()
        .and_then(|value| value.trim().parse::<u64>().ok()).unwrap_or(30_000);
    let started = Instant::now();
    while !ready(selected_port) {
        if started.elapsed() >= Duration::from_millis(startup_ms) {
            return Err(io::Error::other("Riff is taking longer to open. Try opening it again in a moment."));
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    if !Command::new("/usr/bin/open").arg(url).status()?.success() {
        return Err(io::Error::other("Riff could not open your browser."));
    }
    Ok(())
}

fn main() {
    if let Err(error) = open_studio() {
        let message = if error.kind() == io::ErrorKind::NotFound {
            "Riff has not finished setup. Open Riff Setup to continue.".to_string()
        } else { error.to_string() };
        let _ = Command::new("/usr/bin/osascript").args([
            "-e", "on run argv\ndisplay alert \"Riff could not open\" message (item 1 of argv) buttons {\"OK\"} default button \"OK\"\nend run", &message
        ]).status();
    }
}

#[cfg(test)]
mod tests {
    use super::port;
    #[test]
    fn accepts_only_an_explicit_owned_loopback_port() {
        assert_eq!(port("http://127.0.0.1:7878").unwrap(), 7878);
        for invalid in ["https://example.com", "http://127.0.0.1:0", "http://127.0.0.1:65536", "http://127.0.0.1:7878/path", "http://127.0.0.1:7878@evil"] {
            assert!(port(invalid).is_err());
        }
    }
}
