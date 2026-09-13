//! Thin graphical app entry point. Only the adjacent bundled OTP release runs.
use std::fs::{self, DirBuilder, OpenOptions};
use std::io;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::os::unix::process::CommandExt;
use std::process::{Command, Stdio};
use std::time::{SystemTime, UNIX_EPOCH};

fn main() -> io::Result<()> {
    let executable = std::env::current_exe()?;
    let contents = executable.parent().and_then(|p| p.parent()).ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "application bundle missing"))?;
    let runtime = contents.join("Resources/runtime");
    let release = runtime.join("bin/riff_installer");
    if !release.is_file() {
        return Err(io::Error::new(io::ErrorKind::NotFound, "bundled runtime missing"));
    }
    let epoch = SystemTime::now().duration_since(UNIX_EPOCH).map_err(io::Error::other)?.as_nanos();
    let temporary = std::env::temp_dir().join(format!("riff-setup-{}-{epoch}", std::process::id()));
    DirBuilder::new().mode(0o700).create(&temporary)?;
    let log = OpenOptions::new().create_new(true).write(true).mode(0o600).open(temporary.join("setup.log"))?;
    let mut command = Command::new(release);
    let payload = contents.join("Resources/payload");
    if payload.is_dir() { command.env("RIFF_INSTALLER_PAYLOAD", payload); }
    let error = command.arg("start")
        .current_dir(&runtime)
        .env("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
        .env("RELEASE_DISTRIBUTION", "none")
        .env("RELEASE_TMP", temporary.join("runtime"))
        .env("ERL_CRASH_DUMP", temporary.join("erl_crash.dump"))
        .env_remove("ERL_LIBS")
        .env_remove("ERL_AFLAGS")
        .env_remove("ERL_FLAGS")
        .env_remove("ELIXIR_ERL_OPTIONS")
        .stdin(Stdio::null())
        .stdout(Stdio::from(log.try_clone()?))
        .stderr(Stdio::from(log))
        .exec();
    // Reached only if the bundled executable could not start.
    fs::write(temporary.join("start-error.txt"), error.to_string())?;
    Err(error)
}
