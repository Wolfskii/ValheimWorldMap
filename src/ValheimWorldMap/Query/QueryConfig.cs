using ValheimWorldMap.Configuration;

namespace ValheimWorldMap.Query;

internal sealed class QueryConfig
{
    private readonly ConfigEntryInt _queryPort;
    private readonly ConfigEntryBool _publicPlayerNames;

    public QueryConfig(
        ConfigEntryInt queryPort,
        ConfigEntryBool publicPlayerNames)
    {
        _queryPort = queryPort;
        _publicPlayerNames = publicPlayerNames;
    }

    public int QueryPort => _queryPort.Value;

    public bool PublicPlayerNames => _publicPlayerNames.Value;
}
