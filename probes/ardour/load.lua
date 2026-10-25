local path = "/mnt/nvme4tb/ci-runners/baton2-native-homelab/home/worktrees/riff-daw-ensemble-20261025/.probe-tmp/deepseek/ardour-probe/load.out"
local f=io.open(path,"w")
local function log(s) f:write(tostring(s).."\n"); f:flush() end
log("start")
local ok, s = pcall(load_session, "/mnt/nvme4tb/ci-runners/baton2-native-homelab/home/worktrees/riff-daw-ensemble-20261025/.probe-tmp/deepseek/ardour-probe/session3", "Test3")
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
quit()
