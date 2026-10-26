-- Environment: ARDOUR_PROBE_LOG (an existing writable parent is required).
local path = os.getenv("ARDOUR_PROBE_LOG")
assert(path and path ~= "", "Set ARDOUR_PROBE_LOG")
local f = assert(io.open(path, "w"))
f:write("start\n"); f:flush()
f:write("arg="..tostring(arg).."\n"); f:flush()
f:write("done\n"); f:close()
