-- Save one solo snapshot per MIDI track.  Renders are done afterwards with
-- ardour9-export; this is solo-render stem evidence, not the GUI stem dialog.
-- Environment: ARDOUR_PROBE_SESSION_DIR, ARDOUR_PROBE_SNAPSHOT_IN,
--              ARDOUR_PROBE_STEM_PREFIX, ARDOUR_PROBE_LOG
local sdir = assert(os.getenv("ARDOUR_PROBE_SESSION_DIR"))
local snap_in = assert(os.getenv("ARDOUR_PROBE_SNAPSHOT_IN"))
local prefix = assert(os.getenv("ARDOUR_PROBE_STEM_PREFIX"))
local logpath = assert(os.getenv("ARDOUR_PROBE_LOG"))
local f = io.open(logpath, "w")
local function log(x) f:write(tostring(x).."\n"); f:flush() end
local s = load_session(sdir, snap_in)
log("session="..tostring(s))
if not s then f:close(); quit(); return end
local tracks = {}
for t in s:get_tracks():iter() do tracks[#tracks + 1] = t end
log("tracks="..#tracks)
for i, t in ipairs(tracks) do
  for j, o in ipairs(tracks) do
    o:solo_control():set_value(j == i and 1.0 or 0.0,
                               PBD.GroupControlDisposition.NoGroup)
  end
  local snap = prefix..tostring(i)
  local ok, e = pcall(function() return s:save_state(snap) end)
  log("stem "..i.." "..tostring(t:name()).." save="..tostring(ok).." result="..tostring(e))
end
for _, o in ipairs(tracks) do o:solo_control():set_value(0.0, PBD.GroupControlDisposition.NoGroup) end
f:close(); close_session(); quit()
