//! Hold an OS advisory lock for the lifetime of one Elixir port.
//! No dependencies; Rust 1.89+ supplies File::try_lock on supported hosts.
use std::fs::File;
use std::io::{self, Read, Write};

fn main() -> io::Result<()> {
    let path = std::env::args_os().nth(1).ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "lock path required"))?;
    if path == "--sync-directory" {
        let directory = std::env::args_os().nth(2).ok_or_else(|| io::Error::other("directory required"))?;
        let file = File::open(directory)?;
        if !file.metadata()?.is_dir() { return Err(io::Error::other("expected a directory")); }
        return file.sync_all();
    }
    let file = File::options().read(true).write(true).create(true).open(path)?;
    match file.try_lock() {
        Ok(()) => (),
        Err(std::fs::TryLockError::WouldBlock) => {
            println!("BUSY");
            return Ok(());
        }
        Err(std::fs::TryLockError::Error(error)) => return Err(error),
    }
    println!("LOCKED");
    io::stdout().flush()?;
    // EOF on installer exit, or one explicit release byte, closes the file and
    // releases the kernel lock. The lock file itself is never removed/replaced.
    let mut byte = [0u8; 1];
    let _ = io::stdin().read(&mut byte)?;
    file.unlock()?;
    // The owning VM may have exited, in which case stdout is already closed.
    let _ = writeln!(io::stdout(), "RELEASED");
    Ok(())
}
