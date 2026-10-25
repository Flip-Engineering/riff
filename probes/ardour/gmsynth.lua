local outpath = "/mnt/nvme4tb/ci-runners/baton2-native-homelab/home/worktrees/riff-daw-ensemble-20261025/.probe-tmp/deepseek/ardour-probe/gmsynth.out"
local sdir = "/mnt/nvme4tb/ci-runners/baton2-native-homelab/home/worktrees/riff-daw-ensemble-20261025/.probe-tmp/deepseek/ardour-probe/region-session"
local f = io.open(outpath,"w")
local function log(x) f:write(tostring(x).."\n"); f:flush() end
log("start")
local s = load_session(sdir, "1region")
log("session="..tostring(s))
if not s then f:close(); quit(); return end
local n = 0
for t in s:get_tracks():iter() do
  n = n + 1
  log("track "..n.." name="..tostring(t:name()))
  local p = ARDOUR.LuaAPI.new_plugin(s, "http://gareus.org/oss/lv2/gmsynth", ARDOUR.PluginType.LV2, "")
  log("plugin="..tostring(p).." isnil="..tostring(p:isnil()))
  if not p:isnil() then
    local ok, err = pcall(function() t:add_processor_by_index(p, 0, nil, true) end)
    log("add_processor ok="..tostring(ok).." err="..tostring(err))
  end
end
log("tracks="..n)
local ok, err = pcall(function() s:save_state() end)
log("save_state ok="..tostring(ok).." err="..tostring(err))
local se = s:simple_export()
log("simple_export="..tostring(se))
se:set_name("gmsynth-probe")
se:set_folder(sdir.."/export")
se:set_range(529680, 1089150)
log("check_outputs="..tostring(se:check_outputs()))
log("run_export start")
local rok, rerr = pcall(function() return se:run_export() end)
log("run_export ok="..tostring(rok).." result/err="..tostring(rerr))
close_session()
log("closed")
quit()
