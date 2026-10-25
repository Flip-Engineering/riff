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

  @resident_protocol "riff.resident.v1"
  def resident_protocol, do: @resident_protocol

  defstruct active: %{}, waiting: [], reasons: %{}, residents: %{}, bindings: %{}, usage: %{}

  def new, do: %__MODULE__{}

  def step(state, %{"op" => "resident"} = message) do
    with :ok <- resident_version(message["protocol"]),
         :ok <- resident_id(message["id"]) do
      resident_step(state, message)
    else
      {:error, error} -> {state, %{"state" => "error", "error" => error}}
    end
  end

  def step(state, %{"op" => "request", "id" => id, "requirement" => requirement} = message)
      when is_binary(id) and byte_size(id) > 0 do
    with :ok <- validate_requirement(requirement),
         :ok <- validate_binding(state, message) do
      existing = Map.has_key?(state.active, id) or Enum.any?(state.waiting, &(&1.id == id))

      next =
        if existing,
          do: state,
          else: %{
            state
            | waiting: state.waiting ++ [%{id: id, requirement: requirement}],
              bindings:
                if(message["resident"],
                  do: Map.put(state.bindings, id, message["resident"]),
                  else: state.bindings
                )
          }

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
        reasons: Map.delete(state.reasons, id),
        bindings: Map.delete(state.bindings, id)
    }

    {next, %{"state" => if(operation == "cancel", do: "cancelled", else: "released"), "id" => id}}
  end

  def step(state, %{"op" => "status"}) do
    {state,
     %{
       "state" => "ready",
       "active" => Map.keys(state.active),
       "waiting" => Enum.map(state.waiting, & &1.id),
       "residents" => Map.new(state.residents, fn {id, lease} -> {id, lease_charge(lease)} end)
     }}
  end

  def step(state, _), do: {state, %{"state" => "error", "error" => "Unknown scheduling request."}}

  defp resident_version(@resident_protocol), do: :ok

  defp resident_version(_),
    do: {:error, "Unsupported resident lease protocol; expected riff.resident.v1."}

  defp resident_id(id) when is_binary(id) and byte_size(id) > 0, do: :ok
  defp resident_id(_), do: {:error, "A resident lease identity is required."}

  defp validate_binding(state, message) do
    if Map.has_key?(message, "resident") do
      with :ok <- resident_version(message["resident_protocol"]),
           :ok <- resident_id(message["resident"]) do
        lease = state.residents[message["resident"]]

        cond do
          lease == nil ->
            {:error, "The resident lease is no longer registered."}

          lease.requirement["device"] != message["requirement"]["device"] ->
            {:error, "The resident lease uses a different device."}

          true ->
            :ok
        end
      end
    else
      :ok
    end
  end

  defp resident_step(state, %{"action" => "release_if_idle"} = message) do
    if map_size(state.active) == 0 and state.waiting == [] do
      resident_step(state, Map.put(message, "action", "release"))
    else
      {state,
       %{
         "state" => "busy",
         "id" => message["id"],
         "active" => Map.keys(state.active),
         "waiting" => Enum.map(state.waiting, & &1.id)
       }}
    end
  end

  defp resident_step(state, %{"action" => "release", "id" => id}) do
    if Enum.any?(state.bindings, fn {_, resident} -> resident == id end) do
      {state,
       %{
         "state" => "error",
         "error" => "Release the resident's job reservations before its lease."
       }}
    else
      {%{state | residents: Map.delete(state.residents, id)},
       %{"state" => "released", "id" => id}}
    end
  end

  defp resident_step(state, %{"action" => "fit", "requirement" => requirement} = message) do
    with :ok <- validate_requirement(requirement),
         :ok <-
           if(message["resident"],
             do:
               validate_binding(state, Map.put(message, "resident_protocol", message["protocol"])),
             else: :ok
           ) do
      {state, fit(state, message)}
    else
      {:error, error} -> {state, %{"state" => "error", "error" => error}}
    end
  end

  defp resident_step(state, %{
         "action" => "update",
         "id" => id,
         "requirement" => requirement,
         "usage" => usage
       }) do
    with :ok <- validate_requirement(requirement),
         true <-
           is_map(usage) and is_integer(usage["host"]) and usage["host"] >= 0 and
             is_integer(usage["device"]) and usage["device"] >= 0 do
      lease = %{requirement: requirement, usage: usage}

      {%{state | residents: Map.put(state.residents, id, lease)},
       %{
         "state" => "resident",
         "protocol" => @resident_protocol,
         "id" => id,
         "requirement" => lease_charge(lease)
       }}
    else
      {:error, error} ->
        {state, %{"state" => "error", "error" => error}}

      _ ->
        {state,
         %{
           "state" => "error",
           "error" => "Resident usage must contain measured nonnegative host and device bytes."
         }}
    end
  end

  defp resident_step(state, _),
    do:
      {state,
       %{
         "state" => "error",
         "error" => "Choose resident update, fit, release or release_if_idle."
       }}

  defp lease_charge(lease),
    do: %{
      "host_peak" => lease.usage["host"],
      "device_peak" => lease.usage["device"],
      "device" => lease.requirement["device"]
    }

  defp working_usage(state, id) do
    usage = state.usage[id] || %{}
    lease = state.residents[state.bindings[id]]

    Map.new(~w(host device), fn resource ->
      resident = if lease, do: lease.usage[resource], else: 0
      {resource, max(0, nonnegative(usage[resource]) - resident)}
    end)
  end

  defp weights(requirement), do: nonnegative(get_in(requirement, ["basis", "weights"]))

  defp charge(state, id, requirement) do
    lease = state.residents[state.bindings[id]]

    if lease do
      held = min(weights(requirement), weights(lease.requirement))
      host = min(held, lease.usage["host"])

      device =
        if (requirement["device_peak"] || 0) > 0, do: min(held, lease.usage["device"]), else: 0

      working = working_usage(state, id)

      Map.merge(requirement, %{
        "host_peak" => max(requirement["host_peak"] - host, working["host"]),
        "device_peak" => max((requirement["device_peak"] || 0) - device, working["device"]),
        "resident" => %{
          "id" => state.bindings[id],
          "protocol" => @resident_protocol,
          "usage" => lease.usage,
          "host_weights" => host,
          "device_weights" => device
        }
      })
    else
      requirement
    end
  end

  defp fit(state, message) do
    snapshot = message["snapshot"] || %{}
    host = snapshot["host"] || %{}
    reserve_bytes = nonnegative(host["reserve"])
    # The same user reserve applies independently to host and CUDA availability.
    devices =
      Map.new(snapshot["devices"] || %{}, fn {id, value} ->
        {id, Map.put(value, "reserve", max(reserve_bytes, nonnegative(value["reserve"])))}
      end)

    state = %{
      state
      | active: Map.delete(state.active, message["id"]),
        usage: message["usage"] || %{}
    }

    remaining = budget(state, Map.put(snapshot, "devices", devices))
    lease = state.residents[message["resident"]]
    requirement = message["requirement"]

    retained =
      if lease,
        do: lease.usage,
        else: %{
          "host" => weights(requirement),
          "device" => if((requirement["device_peak"] || 0) > 0, do: weights(requirement), else: 0)
        }

    extra = if lease, do: %{"host" => 0, "device" => 0}, else: retained

    remaining =
      reserve(remaining, %{
        "host_peak" => extra["host"],
        "device_peak" => extra["device"],
        "device" => requirement["device"]
      })

    waiters =
      Enum.filter(
        state.waiting,
        &(&1.id != message["id"] and state.reasons[&1.id] != "Waiting for the current model")
      )

    largest_host =
      Enum.reduce(waiters, 0, &max(&2, charge(state, &1.id, &1.requirement)["host_peak"]))

    largest_devices =
      Enum.reduce(waiters, %{}, fn item, acc ->
        charge = charge(state, item.id, item.requirement)

        if (charge["device_peak"] || 0) > 0,
          do:
            Map.update(
              acc,
              charge["device"],
              charge["device_peak"],
              &max(&1, charge["device_peak"])
            ),
          else: acc
      end)

    relevant_devices =
      if (requirement["device_peak"] || 0) > 0,
        do: Map.put_new(largest_devices, requirement["device"], 0),
        else: largest_devices

    device_ok =
      Enum.all?(relevant_devices, fn {id, needed} ->
        is_integer(remaining.devices[id]) and remaining.devices[id] >= needed and
          Map.get(devices[id] || %{}, "pressure", "normal") == "normal"
      end)

    keep =
      remaining.pressure == "normal" and is_integer(remaining.host) and
        remaining.host >= largest_host and device_ok

    %{
      "state" => "resident_fit",
      "protocol" => @resident_protocol,
      "keep" => keep,
      "reason" =>
        if(keep,
          do: "Resident engine fits alongside waiting work",
          else:
            "Resident engine does not fit alongside waiting work or memory pressure is not normal"
        ),
      "figures" => %{
        "resident" => retained,
        "additional_resident" => extra,
        "host_available_after_resident" => remaining.host,
        "device_available_after_resident" => remaining.devices,
        "reserve_bytes" => reserve_bytes,
        "largest_waiting_host" => largest_host,
        "largest_waiting_devices" => largest_devices,
        "host_pressure" => remaining.pressure,
        "waiting_jobs" => length(waiters)
      }
    }
  end

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
    state = %{state | usage: message["usage"] || %{}}
    budget = budget(state, snapshot)

    {next, _} =
      Enum.reduce(state.waiting, {%{state | waiting: [], reasons: %{}}, budget}, fn item,
                                                                                    {next,
                                                                                     remaining} ->
        charge = charge(next, item.id, item.requirement)

        case reason(charge, remaining) do
          nil ->
            admit_item(next, remaining, item, charge)

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

              admit_item(
                next,
                remaining,
                %{item | requirement: solo},
                Map.merge(charge, Map.take(solo, ["exclusive", "admission", "uncertainty"]))
              )
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

  defp admit_item(state, budget, item, charge) do
    next = %{state | active: Map.put(state.active, item.id, item.requirement)}
    remaining = reserve(budget, charge)

    {next,
     %{
       remaining
       | count: remaining.count + 1,
         exclusive: remaining.exclusive or item.requirement["exclusive"] == true
     }}
  end

  defp budget(state, snapshot) do
    active = state.active
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
    base =
      Enum.reduce(state.residents, base, fn {id, lease}, remaining ->
        measured = state.usage[id] || lease.usage

        reserve(remaining, %{
          "host_peak" => max(0, lease.usage["host"] - nonnegative(measured["host"])),
          "device" => lease.requirement["device"],
          "device_peak" => max(0, lease.usage["device"] - nonnegative(measured["device"]))
        })
      end)

    Enum.reduce(active, base, fn {id, requirement}, remaining ->
      measured = working_usage(state, id)
      requirement = charge(state, id, requirement)

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
        %{"state" => "admitted", "id" => id, "requirement" => charge(state, id, state.active[id])}

      Map.has_key?(state.reasons, id) ->
        %{"state" => "waiting", "id" => id, "reason" => state.reasons[id]}

      true ->
        %{"state" => "absent", "id" => id}
    end
  end
end
