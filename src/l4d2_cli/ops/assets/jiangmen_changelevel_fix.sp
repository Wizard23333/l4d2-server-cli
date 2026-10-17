#pragma semicolon 1
#include <sourcemod>
#include <entitylump>

#pragma newdecls required

public Plugin myinfo =
{
    name = "Jiangmen Xinhuai Changelevel Fix",
    author = "TRAE",
    description = "Fixes Jiangmen Xinhuai self-referencing changelevel entities.",
    version = "1.1.0"
};

public void OnMapInit(const char[] mapName)
{
    char nextMap[64];
    bool removeSelfChangelevel = false;

    if (StrEqual(mapName, "jiangmmenxinhuai_2_1_d", false)) {
        strcopy(nextMap, sizeof(nextMap), "jiangmmenxinhuai_3_1");
    } else if (StrEqual(mapName, "jiangmenxinuai_xuxi", false)) {
        removeSelfChangelevel = true;
    } else {
        return;
    }

    int patched = 0;
    int removed = 0;
    for (int i = EntityLump.Length() - 1; i >= 0; i--) {
        EntityLumpEntry entry = EntityLump.Get(i);

        char classname[64];
        if (entry.GetNextKey("classname", classname, sizeof(classname)) == -1) {
            delete entry;
            continue;
        }
        if (!StrEqual(classname, "info_changelevel", false) && !StrEqual(classname, "trigger_changelevel", false)) {
            delete entry;
            continue;
        }

        char targetMap[64];
        int mapKey = entry.GetNextKey("map", targetMap, sizeof(targetMap));
        if (mapKey != -1 && StrEqual(targetMap, mapName, false)) {
            if (removeSelfChangelevel) {
                delete entry;
                EntityLump.Erase(i);
                removed++;
                continue;
            }
            entry.Update(mapKey, NULL_STRING, nextMap);
            patched++;
        }
        delete entry;
    }

    if (patched > 0) {
        LogMessage("Patched %d Jiangmen changelevel entity/entities on %s -> %s", patched, mapName, nextMap);
    }
    if (removed > 0) {
        LogMessage("Removed %d Jiangmen self-referencing changelevel entity/entities on %s", removed, mapName);
    }
}
