-- Environment: ARDOUR_PROBE_LOG, ARDOUR_PROBE_SESSION_DIR, ARDOUR_PROBE_SNAPSHOT_IN.
local function required(name)
  local value = os.getenv(name)
  assert(value and value ~= "", "Set " .. name)
  return value
end
local path = required("ARDOUR_PROBE_LOG")
local sdir = required("ARDOUR_PROBE_SESSION_DIR")
local snapshot = required("ARDOUR_PROBE_SNAPSHOT_IN")
local f = assert(io.open(path, "w"))
local function log(s) f:write(tostring(s).."\n"); f:flush() end
log("start")
local ok, s = pcall(load_session, sdir, snapshot)
log("load pcall ok="..tostring(ok).." session="..tostring(s))
if s then
  log("name="..tostring(s:name()))
  local tl = s:get_tracks()
  log("tracks="..tostring(tl))
  for t in tl:iter() do log("track="..t:tname()) end
end
log("closing")
close_session()
log("quit")
f:close()
quit()
