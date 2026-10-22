using System;
using System.Collections.Generic;

namespace ValheimWorldMap.LiveMap;

internal sealed class VwmCommandDefinition
{
    public VwmCommandDefinition(
        string name,
        string usage,
        string description,
        string category,
        string[] examples,
        bool playerArg = false,
        bool itemArg = false)
    {
        Name = name;
        Usage = usage;
        Description = description;
        Category = category;
        Examples = examples;
        PlayerArg = playerArg;
        ItemArg = itemArg;
    }

    public string Name { get; }

    public string Usage { get; }

    public string Description { get; }

    public string Category { get; }

    public string[] Examples { get; }

    public bool PlayerArg { get; }

    public bool ItemArg { get; }
}

internal static class VwmCommandRegistry
{
    public static readonly string[] Categories =
    {
        "server",
        "players",
        "moderation",
        "world",
        "diagnostics",
    };

    private static readonly VwmCommandDefinition[] Definitions =
    {
        new VwmCommandDefinition(
            "help",
            "vwm help [command]",
            "List commands or show detailed help for one command.",
            "server",
            new[] { "vwm help", "vwm help playerinfo" }),
        new VwmCommandDefinition(
            "save",
            "vwm save",
            "Save the world and all connected player profiles.",
            "server",
            new[] { "vwm save" }),
        new VwmCommandDefinition(
            "shutdown",
            "vwm shutdown <seconds> [message] | vwm shutdown cancel",
            "Schedule or cancel a save-first dedicated-server shutdown.",
            "server",
            new[]
            {
                "vwm shutdown 60 Restarting for maintenance",
                "vwm shutdown cancel",
            }),
        new VwmCommandDefinition(
            "players",
            "vwm players",
            "List connected players, session times, host IDs, and public positions.",
            "players",
            new[] { "vwm players" }),
        new VwmCommandDefinition(
            "playerinfo",
            "vwm playerinfo <player>",
            "Show server-known details for one connected player.",
            "players",
            new[] { "vwm playerinfo Alice", "vwm playerinfo Some Viking" },
            playerArg: true),
        new VwmCommandDefinition(
            "playtime",
            "vwm playtime",
            "Show tracked session time for every player and server uptime.",
            "players",
            new[] { "vwm playtime" }),
        new VwmCommandDefinition(
            "kick",
            "vwm kick <player>",
            "Disconnect a player by character name or host ID.",
            "moderation",
            new[] { "vwm kick Alice", "vwm kick Steam_123456789" },
            playerArg: true),
        new VwmCommandDefinition(
            "ban",
            "vwm ban <player>",
            "Ban and disconnect a player by character name or host ID.",
            "moderation",
            new[] { "vwm ban Alice", "vwm ban Steam_123456789" },
            playerArg: true),
        new VwmCommandDefinition(
            "unban",
            "vwm unban <target>",
            "Remove a host or platform ID from the server ban list.",
            "moderation",
            new[] { "vwm unban Steam_123456789" }),
        new VwmCommandDefinition(
            "banlist",
            "vwm banlist",
            "List banned host and platform IDs.",
            "moderation",
            new[] { "vwm banlist" }),
        new VwmCommandDefinition(
            "weather",
            "vwm weather",
            "Show the current environment and wind conditions.",
            "world",
            new[] { "vwm weather" }),
        new VwmCommandDefinition(
            "bosses",
            "vwm bosses",
            "Show defeated bosses and active world modifiers.",
            "world",
            new[] { "vwm bosses" }),
        new VwmCommandDefinition(
            "entities",
            "vwm entities",
            "Show counts from the latest LiveMap entity scan and request a refresh.",
            "world",
            new[] { "vwm entities" }),
        new VwmCommandDefinition(
            "item",
            "vwm item <name>",
            "Show catalog details, recipes, sources, drops, and recipe uses for an item.",
            "world",
            new[] { "vwm item Iron", "vwm item Black metal", "vwm item SwordIron" },
            itemArg: true),
        new VwmCommandDefinition(
            "stats",
            "vwm stats",
            "Show server uptime, population, object, memory, and frame statistics.",
            "diagnostics",
            new[] { "vwm stats" }),
        new VwmCommandDefinition(
            "doctor",
            "vwm doctor",
            "Show plugin, module, configuration, and LiveMap health diagnostics.",
            "diagnostics",
            new[] { "vwm doctor" }),
    };

    private static readonly Dictionary<string, VwmCommandDefinition> ByName =
        BuildLookup(Definitions);

    private static readonly VwmCommandDefinition[] VanillaDefinitions =
    {
        new VwmCommandDefinition(
            "save",
            "save",
            "Force-save the world and connected player profiles, resetting the save interval.",
            "world",
            new[] { "save" }),
        new VwmCommandDefinition(
            "kick",
            "kick <name|ip|userID>",
            "Disconnect a player by character name, IP address, or platform user ID.",
            "moderation",
            new[] { "kick Alice", "kick Steam_123456789" },
            playerArg: true),
        new VwmCommandDefinition(
            "ban",
            "ban <name|ip|userID>",
            "Ban and disconnect a player by character name, IP address, or platform user ID.",
            "moderation",
            new[] { "ban Alice", "ban Steam_123456789" },
            playerArg: true),
        new VwmCommandDefinition(
            "unban",
            "unban <ip|userID>",
            "Remove an IP address or platform user ID from the server ban list.",
            "moderation",
            new[] { "unban Steam_123456789" },
            playerArg: true),
        new VwmCommandDefinition(
            "banned",
            "banned",
            "List banned IP addresses and platform user IDs.",
            "moderation",
            new[] { "banned" }),
        new VwmCommandDefinition(
            "lodbias",
            "lodbias [value]",
            "Show or set the server process's level-of-detail distance bias.",
            "server",
            new[] { "lodbias", "lodbias 2" }),
        new VwmCommandDefinition(
            "sleep",
            "sleep",
            "Skip the world clock to the next morning.",
            "world",
            new[] { "sleep" }),
    };

    private static readonly Dictionary<string, VwmCommandDefinition> VanillaByName =
        BuildLookup(VanillaDefinitions);

    public static IReadOnlyList<VwmCommandDefinition> All => Definitions;

    public static IReadOnlyList<VwmCommandDefinition> Vanilla => VanillaDefinitions;

    public static bool TryGet(string name, out VwmCommandDefinition? definition)
    {
        return ByName.TryGetValue(name, out definition);
    }

    public static bool TryGetVanilla(string name, out VwmCommandDefinition? definition)
    {
        return VanillaByName.TryGetValue(name, out definition);
    }

    public static List<string> GetSubcommandNames()
    {
        var names = new List<string>(Definitions.Length);
        for (int index = 0; index < Definitions.Length; index++)
        {
            names.Add(Definitions[index].Name);
        }

        names.Sort(StringComparer.Ordinal);
        return names;
    }

    private static Dictionary<string, VwmCommandDefinition> BuildLookup(
        VwmCommandDefinition[] definitions)
    {
        var lookup = new Dictionary<string, VwmCommandDefinition>(StringComparer.OrdinalIgnoreCase);
        for (int index = 0; index < definitions.Length; index++)
        {
            lookup.Add(definitions[index].Name, definitions[index]);
        }

        return lookup;
    }
}
