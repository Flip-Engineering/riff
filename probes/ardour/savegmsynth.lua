local outpath="/mnt/nvme4tb/ci-runners/baton2-native-homelab/home/worktrees/riff-daw-ensemble-20261025/.probe-tmp/deepseek/ardour-probe/savegmsynth.out"; local sdir="/mnt/nvme4tb/ci-runners/baton2-native-homelab/home/worktrees/riff-daw-ensemble-20261025/.probe-tmp/deepseek/ardour-probe/region-session"
local f=io.open(outpath,"w"); local function log(x) f:write(tostring(x).."\n"); f:flush() end
local s=load_session(sdir,"1region"); log("session="..tostring(s))
for t in s:get_tracks():iter() do
  local p=ARDOUR.LuaAPI.new_plugin(s,"http://gareus.org/oss/lv2/gmsynth",ARDOUR.PluginType.LV2,"")
  log("plugin isnil="..tostring(p:isnil()))
  t:add_processor_by_index(p,0,nil,true)
end
local ok,err=pcall(function() return s:save_state("gmsynthprobe") end)
log("save_state ok="..tostring(ok).." result="..tostring(err))
close_session(); quit()
