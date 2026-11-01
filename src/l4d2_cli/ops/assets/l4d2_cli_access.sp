// SPDX-License-Identifier: MIT
#pragma semicolon 1
#pragma newdecls required
#include <sourcemod>

public Plugin myinfo = { name = "L4D2 CLI friends", author = "Wizard23333",
    description = "Validated SteamID64 allowlist per game port", version = "0.1.0", url = "" };

StringMap Allowed;
char Generation[40];
bool PublicMode;
bool ValidPolicy;
float ConnectedAt[MAXPLAYERS + 1];

public void OnPluginStart()
{
    Allowed = new StringMap();
    CreateTimer(5.0, CheckEveryone, _, TIMER_REPEAT);
    LoadPolicy();
    for (int i = 1; i <= MaxClients; i++) ConnectedAt[i] = GetEngineTime();
}

public void OnConfigsExecuted() { LoadPolicy(); }
public void OnClientConnected(int client) { ConnectedAt[client] = GetEngineTime(); }
public void OnClientAuthorized(int client, const char[] auth) { CheckClient(client, true); }
public void OnClientPostAdminCheck(int client) { CheckClient(client, true); }

void LoadPolicy()
{
    Allowed.Clear();
    ValidPolicy = false;
    PublicMode = false;
    Generation[0] = '\0';
    ConVar port = FindConVar("hostport");
    if (port == null) return;
    char path[PLATFORM_MAX_PATH], line[128];
    BuildPath(Path_SM, path, sizeof(path), "configs/l4d2_cli_access_%d.txt", port.IntValue);
    File file = OpenFile(path, "r");
    if (file == null) return;
    bool ok = file.ReadLine(line, sizeof(line));
    TrimString(line);
    PublicMode = StrEqual(line, "public");
    ok = ok && (PublicMode || StrEqual(line, "friends"));
    ok = ok && file.ReadLine(Generation, sizeof(Generation));
    TrimString(Generation);
    ok = ok && strlen(Generation) == 32;
    int count;
    while (file.ReadLine(line, sizeof(line)))
    {
        TrimString(line);
        if (line[0] == '\0') continue;
        if (strlen(line) != 17) ok = false;
        for (int i = 0; i < strlen(line); i++)
            if (line[i] < '0' || line[i] > '9') ok = false;
        Allowed.SetValue(line, 1);
        count++;
        if (count > 1024) { ok = false; break; }
    }
    delete file;
    ValidPolicy = ok && (PublicMode || count > 0);
    BuildPath(Path_SM, path, sizeof(path), "data/l4d2_cli_access_%d.txt", port.IntValue);
    File receipt = OpenFile(path, "w");
    if (receipt != null)
    {
        receipt.WriteLine("%s", Generation);
        receipt.WriteLine("%s", !ValidPolicy ? "invalid" : (PublicMode ? "public" : "friends"));
        receipt.WriteLine("%d", GetTime());
        delete receipt;
    }
}

void CheckClient(int client, bool authorized)
{
    if (!IsClientConnected(client) || IsFakeClient(client) || IsClientInKickQueue(client)) return;
    if (ValidPolicy && PublicMode) return;
    char auth[32];
    int present;
    if (ValidPolicy && GetClientAuthId(client, AuthId_SteamID64, auth, sizeof(auth), true)
        && Allowed.GetValue(auth, present)) return;
    if (authorized || GetEngineTime() - ConnectedAt[client] >= 15.0)
        KickClient(client, "好友房间：Steam 身份未验证或不在名单内，请联系服主。");
}

public Action CheckEveryone(Handle timer)
{
    LoadPolicy();
    for (int i = 1; i <= MaxClients; i++) CheckClient(i, false);
    return Plugin_Continue;
}
