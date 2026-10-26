-- Environment: ARDOUR_PROBE_SESSION_DIR, ARDOUR_PROBE_SNAPSHOT_IN.
local function required(name)
  local value = os.getenv(name)
  assert(value and value ~= "", "Set " .. name)
  return value
end
local sdir = required("ARDOUR_PROBE_SESSION_DIR")
local snapshot = required("ARDOUR_PROBE_SNAPSHOT_IN")
print("lua probe start")
local s = load_session(sdir, snapshot)
print("session", s)
if s then
  local tl = s:get_tracks()
  print("tracks", tl)
  for t in tl:iter() do print("track", t:name()) end
end
close_session()
quit()
