using HarmonyLib;

namespace ValheimWorldMap.Networking;

public interface IVersionHandshake
{
    bool IsAvailable { get; }

    void Initialize(Harmony harmony);

    void Shutdown();
}
