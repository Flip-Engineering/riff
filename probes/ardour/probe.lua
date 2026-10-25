print("lua probe start")
s = load_session("/mnt/nvme4tb/ci-runners/baton2-native-homelab/home/worktrees/riff-daw-ensemble-20261025/.probe-tmp/deepseek/ardour-probe/session3", "Test3")
print("session", s)
if s then
  local tl = s:get_tracks()
  print("tracks", tl)
  for t in tl:iter() do print("track", t:name()) end
end
close_session()
quit()
