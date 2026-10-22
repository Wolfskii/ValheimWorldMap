using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Threading;
using HarmonyLib;
using UnityEngine;
using ValheimWorldMap.Infrastructure;
using ValheimWorldMap.LiveMap;

namespace ValheimWorldMap.RuntimeRegression;

// Permission regressions for the owner-controlled public map layers and the chat feed they
// share with the live map. Everything runs in process on the plugin's own types, with no
// sockets: the checks cover the rules, not the HTTP plumbing around them.
internal static class PublicLayerRegression
{
    private const int SayType = 1;
    private const int ShoutType = 2;
    private const int WhisperType = 0;

    public static void Run(Action<bool, string> check, string root, ModLogger log)
    {
        TestChatCaptureFollowsMirrorChat(check, Path.Combine(root, "public-chat"), log);
        TestChatHistoryStoreLifecycle(check, Path.Combine(root, "chat-store"), log);
        TestPublicChatDisclosure(check);
        TestEntityTrailFollowsFog(check);
        TestChatStreamFollowsPublicChat(check);
    }

    // MirrorChat is the only switch for recording player chat and for keeping it on disk.
    private static void TestChatCaptureFollowsMirrorChat(
        Action<bool, string> check,
        string directory,
        ModLogger log)
    {
        Directory.CreateDirectory(directory);
        string historyPath = Path.Combine(directory, "chat-history.json");
        FieldInfo enabledField = AccessTools.Field(typeof(MapPingPatch), "_enabledCheck");
        FieldInfo mirrorField = AccessTools.Field(typeof(MapPingPatch), "_mirrorChatCheck");
        FieldInfo directoryField = AccessTools.Field(typeof(MapPingPatch), "_chatDataDirectory");
        FieldInfo logField = AccessTools.Field(typeof(MapPingPatch), "_log");
        MethodInfo configure = AccessTools.Method(
            typeof(MapPingPatch),
            "ConfigureChatPersistence",
            new[] { typeof(string), typeof(ModLogger) });
        MethodInfo postfix = AccessTools.Method(typeof(MapPingPatch), "HandleRoutedRpcPostfix");
        object? originalEnabled = enabledField.GetValue(null);
        object? originalMirror = mirrorField.GetValue(null);
        object? originalLog = logField.GetValue(null);
        string originalDirectory = (string)directoryField.GetValue(null)!;

        bool mirror = false;
        enabledField.SetValue(null, new Func<bool>(() => true));
        mirrorField.SetValue(null, new Func<bool>(() => mirror));
        logField.SetValue(null, log);
        MapPingPatch.ShutdownChatPersistence();
        try
        {
            File.WriteAllText(historyPath, "{\"chats\":[]}");
            configure.Invoke(null, new object[] { directory, log });
            check(!File.Exists(historyPath),
                "starting with MirrorChat off deletes a stale chat history file");

            Say(postfix, SayType, "Freya", "off say");
            Say(postfix, ShoutType, "Freya", "off shout");
            check(PlayerChat(0L).Count == 0, "MirrorChat off captures no player Say or Shout");
            MapPingPatch.ShutdownChatPersistence();
            check(!File.Exists(historyPath), "MirrorChat off writes no chat history file");

            mirror = true;
            MapPingPatch.RefreshChatConfiguration();
            Say(postfix, SayType, "Freya", "say line");
            Say(postfix, ShoutType, "Freya", "shout line");
            Say(postfix, WhisperType, "Freya", "whisper line");
            List<MapChatSnapshot> captured = PlayerChat(0L);
            check(captured.Count == 2 && captured[0].Text == "say line" && !captured[0].Shout &&
                  captured[1].Text == "shout line" && captured[1].Shout,
                "MirrorChat on captures Say and Shout and nothing else");
            check(!captured.Exists(chat => chat.Text == "whisper line"),
                "whispers never reach the map");

            MapPingPatch.ShutdownChatPersistence();
            string json = File.Exists(historyPath) ? File.ReadAllText(historyPath) : "";
            check(json.Contains("say line") && json.Contains("shout line") &&
                  !json.Contains("whisper line"),
                "MirrorChat on keeps the Say and Shout lines in chat-history.json");

            configure.Invoke(null, new object[] { directory, log });
            check(PlayerChat(0L).Count == 2, "chat history reloads after a restart");

            mirror = false;
            MapPingPatch.RefreshChatConfiguration();
            check(PlayerChat(0L).Count == 0 && !File.Exists(historyPath),
                "turning MirrorChat off drops stored player chat and deletes the file");

            long serverCapture = MapPingPatch.ExpectServerChat("server notice");
            MapPingPatch.RecordServerChat(serverCapture);
            var all = new List<MapChatSnapshot>();
            MapPingPatch.CopyChatAfter(0L, all);
            MapPingPatch.ShutdownChatPersistence();
            check(all.FindAll(chat => chat.ServerOriginated && chat.Text == "server notice").Count == 1 &&
                  !File.Exists(historyPath),
                "server notices stay in memory with MirrorChat off and are not written to disk");
        }
        finally
        {
            MapPingPatch.ShutdownChatPersistence();
            enabledField.SetValue(null, originalEnabled);
            mirrorField.SetValue(null, originalMirror);
            logField.SetValue(null, originalLog);
            if (originalDirectory.Length > 0)
            {
                configure.Invoke(null, new object[] { originalDirectory, log });
            }
        }
    }

    private static void Say(MethodInfo postfix, int type, string name, string text)
    {
        var parameters = new ZPackage();
        parameters.Write(new Vector3(10f, 0f, 20f));
        parameters.Write(type);
        parameters.Write(name);
        parameters.Write("Steam_1");
        parameters.Write(text);
        var data = new ZRoutedRpc.RoutedRPCData
        {
            m_methodHash = "ChatMessage".GetStableHashCode(),
            m_parameters = new ZPackage(parameters.GetArray()),
        };
        postfix.Invoke(null, new object[] { data });
    }

    private static List<MapChatSnapshot> PlayerChat(long cursor)
    {
        var all = new List<MapChatSnapshot>();
        MapPingPatch.CopyChatAfter(cursor, all);
        all.RemoveAll(chat => chat.ServerOriginated);
        return all;
    }

    private static void TestChatHistoryStoreLifecycle(
        Action<bool, string> check,
        string directory,
        ModLogger log)
    {
        Directory.CreateDirectory(directory);
        string path = Path.Combine(directory, "chat-history.json");
        var line = new MapChatSnapshot(1L, 1f, 2f, "Freya", "kept", false, 1000L, false);

        var flushed = new ChatHistoryStore(directory, log);
        flushed.ScheduleSave(new[] { line });
        flushed.Dispose();
        check(File.Exists(path) && File.ReadAllText(path).Contains("kept"),
            "disposing the chat history store flushes the pending write");
        var writer = (Thread)AccessTools.Field(typeof(ChatHistoryStore), "_writerThread").GetValue(flushed)!;
        var signal = (WaitHandle)AccessTools.Field(typeof(ChatHistoryStore), "_writeSignal").GetValue(flushed)!;
        bool handleClosed = false;
        try { signal.WaitOne(0); }
        catch (ObjectDisposedException) { handleClosed = true; }
        check(!writer.IsAlive && handleClosed,
            "the chat history writer has exited by the time its wait handles are closed");
        flushed.ScheduleSave(new[] { line });
        flushed.Dispose();
        check(true, "saving to or disposing a disposed chat history store is harmless");

        var discarded = new ChatHistoryStore(directory, log);
        discarded.ScheduleSave(new[]
        {
            new MapChatSnapshot(2L, 1f, 2f, "Freya", "secret", false, 2000L, false),
        });
        discarded.Discard();
        Thread.Sleep(1500);
        check(!File.Exists(path), "a discarded chat history store leaves no file behind");
    }

    // The public view hides a chat line's sender the way the player layer hides a player.
    private static void TestPublicChatDisclosure(Action<bool, string> check)
    {
        var player = new MapChatSnapshot(1L, 120f, -340f, "Freya", "hello", false, 1000L, false);
        var server = new MapChatSnapshot(2L, 0f, 0f, "Server", "restart soon", true, 2000L, true);
        var listed = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "Freya" };
        var nobody = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

        string Json(MapChatSnapshot chat, HashSet<string>? senders, bool showNames)
        {
            LiveMapHttpServer.ResolvePublicChatDisclosure(
                chat, senders, showNames, out bool name, out bool position);
            return LiveMapHttpServer.BuildChatJson(chat, name, position);
        }

        string shown = Json(player, null, true);
        check(shown.Contains("\"playerName\":\"Freya\"") && shown.Contains("\"x\":120"),
            "public chat shows the sender and position when names and positions are public");
        string unnamed = Json(player, null, false);
        check(unnamed.Contains("\"playerName\":\"\"") && !unnamed.Contains("Freya") &&
              unnamed.Contains("\"x\":120"),
            "public chat hides the sender's name unless PublicShowPlayerNames is on");
        string listedSender = Json(player, listed, true);
        check(listedSender.Contains("\"playerName\":\"Freya\"") && listedSender.Contains("\"x\":120"),
            "a sender the public player layer lists keeps name and position in public chat");
        string hiddenSender = Json(player, nobody, true);
        check(hiddenSender.Contains("\"playerName\":\"\"") && !hiddenSender.Contains("Freya") &&
              !hiddenSender.Contains("\"x\"") && !hiddenSender.Contains("\"z\"") &&
              hiddenSender.Contains("hello"),
            "RespectInGameVisibility hides an unlisted sender's name and position but not the line");
        string serverLine = Json(server, nobody, false);
        check(serverLine.Contains("\"playerName\":\"Server\"") && serverLine.Contains("restart soon"),
            "server notices keep their label on the public view");
        string adminLine = LiveMapHttpServer.BuildChatJson(player, true, true);
        check(adminLine.Contains("Freya") && adminLine.Contains("\"z\":-340"),
            "admin and shared chat keep every field");
    }

    // A fogged view sees only the part of a route that lies in explored cells.
    private static void TestEntityTrailFollowsFog(Action<bool, string> check)
    {
        const float half = FogTracker.WorldSpan / 2f;
        var mask = new byte[FogTracker.Size * FogTracker.Size];
        float Cell(int index) => ((index + 0.5f) * FogTracker.MetersPerPixel) - half;

        void Explore(int column, int row) => mask[(row * FogTracker.Size) + column] = 1;

        // Row 300 is the boat's lane: columns 100-199 and 400-499 are explored, the rest is not.
        for (int column = 100; column < 200; column++) Explore(column, 300);
        for (int column = 400; column < 500; column++) Explore(column, 300);
        var fog = new FogMaskSnapshot(mask, 1L);
        float laneZ = half - ((300 + 0.5f) * FogTracker.MetersPerPixel);
        PositionSample At(int column, long time) => new PositionSample(Cell(column), laneZ, time);

        check(FogTracker.IsExplored(fog, Cell(150), laneZ) && !FogTracker.IsExplored(fog, Cell(300), laneZ),
            "trail fixture marks exactly the lane cells it means to");

        PositionSample[] intoExplored =
        {
            At(20, 1), At(40, 2), At(60, 3), At(120, 4), At(140, 5),
        };
        PositionSample[] trimmed = LiveMapHttpServer.TrimTrailToExplored(intoExplored, fog);
        check(trimmed.Length == 2 && trimmed[0].UnixMs == 4 && trimmed[1].UnixMs == 5,
            "a boat that sailed from unexplored into explored water shows only the explored part");

        PositionSample[] outAndBack = { At(120, 1), At(300, 2), At(420, 3) };
        trimmed = LiveMapHttpServer.TrimTrailToExplored(outAndBack, fog);
        check(trimmed.Length == 1 && trimmed[0].UnixMs == 3,
            "a route that left explored water and came back shows no line across the gap");

        PositionSample[] allExplored = { At(110, 1), At(150, 2), At(450, 3) };
        check(LiveMapHttpServer.TrimTrailToExplored(allExplored, fog).Length == 3,
            "a route that stayed in explored water is served whole");

        PositionSample[] leftExplored = { At(110, 1), At(150, 2), At(300, 3) };
        check(LiveMapHttpServer.TrimTrailToExplored(leftExplored, fog).Length == 0,
            "a route whose newest point is unexplored shows nothing");
        check(LiveMapHttpServer.TrimTrailToExplored(Array.Empty<PositionSample>(), fog).Length == 0,
            "an empty route stays empty");
    }

    // An open event stream re-checks PublicChat every tick.
    private static void TestChatStreamFollowsPublicChat(Action<bool, string> check)
    {
        long cursor = 5L;
        bool sending = LiveMapHttpServer.UpdateChatStream(true, true, () => 90L, ref cursor);
        check(sending && cursor == 5L, "a stream with chat on keeps its cursor");
        sending = LiveMapHttpServer.UpdateChatStream(true, false, () => 90L, ref cursor);
        check(!sending && cursor == 5L, "turning PublicChat off stops chat on streams that are already open");
        sending = LiveMapHttpServer.UpdateChatStream(false, false, () => 90L, ref cursor);
        check(!sending, "a stream stays silent while PublicChat is off");
        sending = LiveMapHttpServer.UpdateChatStream(false, true, () => 90L, ref cursor);
        check(sending && cursor == 90L,
            "turning PublicChat back on starts from the newest line instead of replaying the gap");
    }
}
