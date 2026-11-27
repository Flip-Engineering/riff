-- Reopen a saved snapshot, log track names, and save it again.  This is the
-- explicit reopen receipt for the generated project.
-- Environment: ARDOUR_PROBE_SESSION_DIR, ARDOUR_PROBE_SNAPSHOT_IN,
--              ARDOUR_PROBE_SNAPSHOT_OUT, ARDOUR_PROBE_LOG
local sdir = assert(os.getenv("ARDOUR_PROBE_SESSION_DIR"))
local snap_in = assert(os.getenv("ARDOUR_PROBE_SNAPSHOT_IN"))
local snap_out = assert(os.getenv("ARDOUR_PROBE_SNAPSHOT_OUT"))
local logpath = assert(os.getenv("ARDOUR_PROBE_LOG"))
local f = io.open(logpath, "w")
local function log(x) f:write(tostring(x).."\n"); f:flush() end
local s, err = load_session(sdir, snap_in)
log("session="..tostring(s).." err="..tostring(err))
if not s then f:close(); quit(); return end
local n = 0
for t in s:get_tracks():iter() do n = n + 1; log("track "..n.." name="..tostring(t:name())) end
log("tracks="..n)
local ok, e = pcall(function() return s:save_state(snap_out) end)
log("save ok="..tostring(ok).." result="..tostring(e))
f:close(); close_session(); quit()
