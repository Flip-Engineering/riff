-- Environment: ARDOUR_PROBE_LOG, ARDOUR_PROBE_SESSION_DIR,
-- ARDOUR_PROBE_SNAPSHOT_IN, ARDOUR_PROBE_SNAPSHOT_OUT.
local function required(name)
  local value = os.getenv(name)
  assert(value and value ~= "", "Set " .. name)
  return value
end
local outpath = required("ARDOUR_PROBE_LOG")
local sdir = required("ARDOUR_PROBE_SESSION_DIR")
local snapshot_in = required("ARDOUR_PROBE_SNAPSHOT_IN")
local snapshot_out = required("ARDOUR_PROBE_SNAPSHOT_OUT")
local f = assert(io.open(outpath, "w")); local function log(x) f:write(tostring(x).."\n"); f:flush() end
local s=load_session(sdir,snapshot_in); log("session="..tostring(s))
for t in s:get_tracks():iter() do
  local p=ARDOUR.LuaAPI.new_plugin(s,"http://gareus.org/oss/lv2/gmsynth",ARDOUR.PluginType.LV2,"")
  log("plugin isnil="..tostring(p:isnil()))
  t:add_processor_by_index(p,0,nil,true)
end
local ok,err=pcall(function() return s:save_state(snapshot_out) end)
log("save_state ok="..tostring(ok).." result="..tostring(err))
f:close(); close_session(); quit()
