local f=io.open("/mnt/nvme4tb/ci-runners/baton2-native-homelab/home/worktrees/riff-daw-ensemble-20261025/.probe-tmp/deepseek/ardour-probe/hi.out","w")
f:write("start\n"); f:flush()
f:write("arg="..tostring(arg).."\n"); f:flush()
f:write("done\n"); f:close()
