for path <- Path.wildcard("_build/prod/rel/riff_installer/lib/*/ebin/*.beam") do
  {:ok, _, chunks} = :beam_lib.all_chunks(String.to_charlist(path))

  found =
    for {id, bytes} <- chunks, :binary.match(bytes, "/Users/") != :nomatch, do: List.to_string(id)

  if found != [], do: IO.inspect({Path.basename(path), found})
end
