using System;

namespace ValheimWorldMap.LiveMap;

internal readonly struct MapColor
{
    public MapColor(float red, float green, float blue)
    {
        Red = red;
        Green = green;
        Blue = blue;
    }

    public float Red { get; }

    public float Green { get; }

    public float Blue { get; }

    public MapColor Multiply(float amount)
    {
        return new MapColor(
            Clamp01(Red * amount),
            Clamp01(Green * amount),
            Clamp01(Blue * amount));
    }

    public static MapColor Lerp(MapColor from, MapColor to, float amount)
    {
        amount = Clamp01(amount);
        return new MapColor(
            from.Red + ((to.Red - from.Red) * amount),
            from.Green + ((to.Green - from.Green) * amount),
            from.Blue + ((to.Blue - from.Blue) * amount));
    }

    public void WriteRgba(byte[] buffer, int offset)
    {
        buffer[offset] = ToByte(Red);
        buffer[offset + 1] = ToByte(Green);
        buffer[offset + 2] = ToByte(Blue);
        buffer[offset + 3] = 255;
    }

    private static float Clamp01(float value)
    {
        return Math.Max(0f, Math.Min(1f, value));
    }

    private static byte ToByte(float value)
    {
        return (byte)Math.Round(Clamp01(value) * 255f);
    }
}

internal static class BiomePalette
{
    // The biome switch mirrors the game's own minimap biome-to-color mapping
    // (Minimap.GetPixelColor). Black Forest, Swamp and Plains are the game's own map
    // colors in Valheim 1.0.20 (the Minimap's serialized m_blackforestColor,
    // m_swampColor and m_heathColor); Meadows is the game's m_meadowsColor with a
    // slightly bluer tint. Mistlands is the daylight color of the game's map mist,
    // which covers the forested Mistlands (the open ground under it is a dark gray,
    // MapShading.MistlandsSpeck). Ashlands keeps its own red; the lava glow and the
    // white Mountain and Deep North ramps are this renderer's.
    private static readonly MapColor Meadows = new MapColor(0.573f, 0.664f, 0.404f);
    private static readonly MapColor BlackForest = new MapColor(0.420f, 0.455f, 0.247f);
    private static readonly MapColor Swamp = new MapColor(0.639f, 0.447f, 0.345f);
    private static readonly MapColor Plains = new MapColor(0.906f, 0.671f, 0.470f);
    private static readonly MapColor Ashlands = new MapColor(0.602f, 0.208f, 0.163f);
    private static readonly MapColor Mistlands = new MapColor(0.481f, 0.397f, 0.463f);
    private static readonly MapColor MountainLow = new MapColor(0.72f, 0.76f, 0.78f);
    private static readonly MapColor MountainHigh = new MapColor(0.97f, 0.97f, 0.98f);
    private static readonly MapColor DeepNorthLow = new MapColor(0.82f, 0.85f, 0.88f);
    private static readonly MapColor DeepNorthHigh = new MapColor(0.98f, 0.98f, 1.00f);
    private static readonly MapColor Ocean = new MapColor(0.088f, 0.140f, 0.240f);
    private static readonly MapColor Unknown = new MapColor(0.42f, 0.45f, 0.40f);

    public static MapColor Get(Heightmap.Biome biome, float height)
    {
        switch (biome)
        {
            case Heightmap.Biome.Meadows:
                return Meadows;
            case Heightmap.Biome.BlackForest:
                return BlackForest;
            case Heightmap.Biome.Swamp:
                return Swamp;
            case Heightmap.Biome.Plains:
                return Plains;
            case Heightmap.Biome.AshLands:
                return Ashlands;
            case Heightmap.Biome.Mistlands:
                return Mistlands;
            case Heightmap.Biome.Mountain:
                return MapColor.Lerp(MountainLow, MountainHigh, (height - 55f) / 100f);
            case Heightmap.Biome.DeepNorth:
                return MapColor.Lerp(DeepNorthLow, DeepNorthHigh, (height - 40f) / 100f);
            case Heightmap.Biome.Ocean:
                return Ocean;
            default:
                return Unknown;
        }
    }

    public static bool TryDescribe(
        Heightmap.Biome biome,
        out string key,
        out string displayName)
    {
        switch (biome)
        {
            case Heightmap.Biome.Meadows:
                key = "meadows";
                displayName = "Meadows";
                return true;
            case Heightmap.Biome.BlackForest:
                key = "black_forest";
                displayName = "Black Forest";
                return true;
            case Heightmap.Biome.Swamp:
                key = "swamp";
                displayName = "Swamp";
                return true;
            case Heightmap.Biome.Mountain:
                key = "mountain";
                displayName = "Mountain";
                return true;
            case Heightmap.Biome.Plains:
                key = "plains";
                displayName = "Plains";
                return true;
            case Heightmap.Biome.Ocean:
                key = "ocean";
                displayName = "Ocean";
                return true;
            case Heightmap.Biome.Mistlands:
                key = "mistlands";
                displayName = "Mistlands";
                return true;
            case Heightmap.Biome.AshLands:
                key = "ashlands";
                displayName = "Ashlands";
                return true;
            case Heightmap.Biome.DeepNorth:
                key = "deep_north";
                displayName = "Deep North";
                return true;
            default:
                key = string.Empty;
                displayName = string.Empty;
                return false;
        }
    }
}
