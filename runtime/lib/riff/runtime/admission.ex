defmodule Riff.Runtime.Admission do
  @moduledoc """
  Memory admission for independent model processes.

  A reservation covers a process's predicted peak, including later model
  stages. Current process usage is already reflected in OS free memory, so
  only its remaining growth is charged again. Host and CUDA budgets are
  independent; Metal allocations are attributed to the host footprint.

  This policy does not impose a job-count ceiling. Waiting work is reconsidered
  against fresh observations, and independent work may pass an item waiting
  for a different resource. The owning studio retains cancellation authority.
  """

  defstruct active: %{}, waiting: [], reasons: %{}

  def new, do: %__MODULE__{}

  def step(state, %{"op" => "request", "id" => id, "requirement" => requirement} = message)
      when is_binary(id) and byte_size(id) > 0 do
    with :ok <- validate_requirement(requirement) do
      existing = Map.has_key?(state.active, id) or Enum.any?(state.waiting, &(&1.id == id))

      next =
        if existing,
          do: state,
          else: %{state | waiting: state.waiting ++ [%{id: id, requirement: requirement}]}

      next = admit(next, message)
      {next, result(next, id)}
    else
      {:error, error} -> {state, %{"state" => "error", "error" => error}}
    end
  end

  def step(state, %{"op" => "poll", "id" => id} = message) do
    next = admit(state, message)
    {next, result(next, id)}
  end

  def step(state, %{"op" => operation, "id" => id}) when operation in ["release", "cancel"] do
    next = %{
      state
      | active: Map.delete(state.active, id),
        waiting: Enum.reject(state.waiting, &(&1.id == id)),
        reasons: Map.delete(state.reasons, id)
    }

    {next, %{"state" => if(operation == "cancel", do: "cancelled", else: "released"), "id" => id}}
  end

  def step(state, %{"op" => "status"}) do
    {state,
     %{
       "state" => "ready",
       "active" => Map.keys(state.active),
       "waiting" => Enum.map(state.waiting, & &1.id)
     }}
  end

  def step(state, _), do: {state, %{"state" => "error", "error" => "Unknown scheduling request."}}

  defp validate_requirement(requirement) when is_map(requirement) do
    host = requirement["host_peak"]
    device = requirement["device"]
    device_peak = requirement["device_peak"] || 0

    cond do
      not is_integer(host) or host < 0 ->
        {:error, "Host memory estimate must be a nonnegative byte count."}

      not is_integer(device_peak) or device_peak < 0 ->
        {:error, "Device memory estimate must be a nonnegative byte count."}

      device_peak > 0 and (not is_binary(device) or device == "") ->
        {:error, "Device memory requires a device identity."}

      true ->
        :ok
    end
  end

  defp validate_requirement(_), do: {:error, "A model memory estimate is required."}

  defp admit(state, message) do
    snapshot = message["snapshot"] || %{}
    usage = message["usage"] || %{}
    budget = budget(state.active, snapshot, usage)

    {next, _} =
      Enum.reduce(state.waiting, {%{state | waiting: [], reasons: %{}}, budget}, fn item,
                                                                                    {next,
                                                                                     remaining} ->
        case reason(item.requirement, remaining) do
          nil ->
            admit_item(next, remaining, item)

          why ->
            if remaining.count == 0 and remaining.pressure == "normal" and
                 is_integer(remaining.host) do
              # Conditional overlap must not become a new solo-generation budget.
              # Preserve the lone-model path, but exclude peers for uncertain or
              # oversized work until its owner releases the reservation.
              solo =
                Map.merge(item.requirement, %{
                  "exclusive" => true,
                  "admission" => "solo",
                  "uncertainty" => why
                })

              admit_item(next, remaining, %{item | requirement: solo})
            else
              {%{
                 next
                 | waiting: [item | next.waiting],
                   reasons: Map.put(next.reasons, item.id, why)
               }, remaining}
            end
        end
      end)

    %{next | waiting: Enum.reverse(next.waiting)}
  end

  defp admit_item(state, budget, item) do
    next = %{state | active: Map.put(state.active, item.id, item.requirement)}
    remaining = reserve(budget, item.requirement)

    {next,
     %{
       remaining
       | count: remaining.count + 1,
         exclusive: remaining.exclusive or item.requirement["exclusive"] == true
     }}
  end

  defp budget(active, snapshot, usage) do
    host = snapshot["host"] || %{}

    base = %{
      host: available(host),
      pressure: host["pressure"],
      count: map_size(active),
      exclusive: Enum.any?(active, fn {_, requirement} -> requirement["exclusive"] == true end),
      devices: Map.new(snapshot["devices"] || %{}, fn {key, value} -> {key, available(value)} end)
    }

    # Charge active growth once, then spend the remaining budget in queue order.
    # Admission is linear in active + waiting work, not one scan per candidate.
    Enum.reduce(active, base, fn {id, requirement}, remaining ->
      measured = usage[id] || %{}

      growth = %{
        "host_peak" => max(0, requirement["host_peak"] - nonnegative(measured["host"])),
        "device" => requirement["device"],
        "device_peak" =>
          max(0, (requirement["device_peak"] || 0) - nonnegative(measured["device"]))
      }

      reserve(remaining, growth)
    end)
  end

  defp available(value) do
    if is_integer(value["available"]),
      do: max(0, value["available"] - nonnegative(value["reserve"])),
      else: nil
  end

  defp nonnegative(value) when is_integer(value) and value >= 0, do: value
  defp nonnegative(_), do: 0

  defp reserve(budget, requirement) do
    host = if is_integer(budget.host), do: budget.host - requirement["host_peak"], else: nil
    device = requirement["device"]

    devices =
      if is_integer(budget.devices[device]),
        do: Map.update!(budget.devices, device, &(&1 - (requirement["device_peak"] || 0))),
        else: budget.devices

    %{budget | host: host, devices: devices}
  end

  defp reason(requirement, budget) do
    device_peak = requirement["device_peak"] || 0
    device_available = budget.devices[requirement["device"]]

    cond do
      budget.pressure in ["warning", "critical"] ->
        "Waiting for memory pressure to settle"

      budget.pressure != "normal" ->
        "Waiting for a memory pressure reading"

      not is_integer(budget.host) ->
        "Waiting for a memory reading"

      budget.exclusive ->
        "Waiting for the current model"

      requirement["host_peak"] > budget.host ->
        "Waiting for memory"

      device_peak > 0 and not is_integer(device_available) ->
        "Waiting for a graphics memory reading"

      device_peak > 0 and device_peak > device_available ->
        "Waiting for graphics memory"

      true ->
        nil
    end
  end

  defp result(state, id) do
    cond do
      Map.has_key?(state.active, id) ->
        %{"state" => "admitted", "id" => id, "requirement" => state.active[id]}

      Map.has_key?(state.reasons, id) ->
        %{"state" => "waiting", "id" => id, "reason" => state.reasons[id]}

      true ->
        %{"state" => "absent", "id" => id}
    end
  end
end
