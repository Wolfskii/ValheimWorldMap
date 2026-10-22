using UnityEngine;
using ValheimWorldMap.Infrastructure;

namespace ValheimWorldMap.Discord;

internal sealed class DiscordBehaviour : MonoBehaviour
{
    private DiscordModule? _module;
    private ServerSessionEventSource? _sessionEvents;
    private bool _stopped;

    public static DiscordBehaviour Initialize(
        GameObject host,
        DiscordModule module,
        ServerSessionEventSource sessionEvents)
    {
        var behaviour = host.AddComponent<DiscordBehaviour>();
        behaviour._module = module;
        behaviour._sessionEvents = sessionEvents;
        sessionEvents.SessionStarted += behaviour.OnSessionStarted;
        sessionEvents.PlayerJoined += behaviour.OnPlayerJoined;
        sessionEvents.PlayerLeft += behaviour.OnPlayerLeft;
        sessionEvents.PlayerDied += behaviour.OnPlayerDied;
        sessionEvents.RaidStarted += behaviour.OnRaidStarted;
        sessionEvents.RaidEnded += behaviour.OnRaidEnded;
        sessionEvents.DayChanged += behaviour.OnDayChanged;
        return behaviour;
    }

    public void StopPermanently()
    {
        if (_stopped)
        {
            return;
        }

        _stopped = true;
        ServerSessionEventSource? sessionEvents = _sessionEvents;
        if (sessionEvents != null)
        {
            sessionEvents.SessionStarted -= OnSessionStarted;
            sessionEvents.PlayerJoined -= OnPlayerJoined;
            sessionEvents.PlayerLeft -= OnPlayerLeft;
            sessionEvents.PlayerDied -= OnPlayerDied;
            sessionEvents.RaidStarted -= OnRaidStarted;
            sessionEvents.RaidEnded -= OnRaidEnded;
            sessionEvents.DayChanged -= OnDayChanged;
        }

        _sessionEvents = null;
        _module = null;
    }

    private void OnSessionStarted(ServerSessionStartedEvent value)
    {
        _module?.UpdateWorldName(value.WorldName);
    }

    private void OnPlayerJoined(ServerPlayerJoinedEvent value)
    {
        DiscordModule? module = _module;
        if (module?.NotifyJoin == true)
        {
            module.NotifyPlayerJoined(value.Name);
        }
    }

    private void OnPlayerLeft(ServerPlayerLeftEvent value)
    {
        DiscordModule? module = _module;
        if (module?.NotifyLeave == true)
        {
            module.NotifyPlayerLeft(value.Name);
        }
    }

    private void OnPlayerDied(ServerPlayerDeathEvent value)
    {
        _module?.NotifyDeath(value.Name, value.LastPosition);
    }

    private void OnRaidStarted(ServerRaidEvent value)
    {
        DiscordModule? module = _module;
        if (module?.NotifyRaid == true)
        {
            module.NotifyRaidStarted(value.Name);
        }
    }

    private void OnRaidEnded(ServerRaidEvent value)
    {
        DiscordModule? module = _module;
        if (module?.NotifyRaid == true)
        {
            module.NotifyRaidEnded(value.Name);
        }
    }

    private void OnDayChanged(ServerDayChangedEvent value)
    {
        DiscordModule? module = _module;
        if (module?.NotifyDayChange == true)
        {
            module.NotifyNewDay(value.Day);
        }
    }

    private void OnDestroy()
    {
        StopPermanently();
    }
}
