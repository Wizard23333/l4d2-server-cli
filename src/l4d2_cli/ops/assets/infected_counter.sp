#pragma semicolon 1
#pragma newdecls required

#include <sourcemod>
#include <sdktools>

#define TEAM_INFECTED 3

ConVar g_cvEnable;
ConVar g_cvChatInterval;
Handle g_hTimer = INVALID_HANDLE;
int g_iTick;

public Plugin myinfo =
{
    name = "Infected Counter",
    author = "L4D2 Manager",
    description = "Show alive special infected and common infected count in chat",
    version = "1.6",
    url = ""
};

public void OnPluginStart()
{
    g_cvEnable = CreateConVar("l4d_infcount_enable", "1",
        "Enable infected counter", FCVAR_NONE, true, 0.0, true, 1.0);
    g_cvChatInterval = CreateConVar("l4d_infcount_chat_interval", "20",
        "Seconds between infected counter chat updates, 0 = disable chat updates", FCVAR_NONE, true, 0.0, true, 60.0);
    StartCounterTimer();
    LogMessage("Infected Counter loaded (v1.6, chat mode with map timer fix).");
}

public void OnMapStart()
{
    StartCounterTimer();
}

public void OnMapEnd()
{
    g_hTimer = INVALID_HANDLE;
}

void StartCounterTimer()
{
    if (g_hTimer != INVALID_HANDLE)
    {
        KillTimer(g_hTimer);
        g_hTimer = INVALID_HANDLE;
    }
    g_iTick = 0;
    g_hTimer = CreateTimer(1.0, Timer_Show, _, TIMER_REPEAT | TIMER_FLAG_NO_MAPCHANGE);
}

public Action Timer_Show(Handle timer)
{
    if (!g_cvEnable.BoolValue)
        return Plugin_Continue;
    g_iTick++;

    int special = 0;
    for (int i = 1; i <= MaxClients; i++)
    {
        if (IsClientInGame(i) && GetClientTeam(i) == TEAM_INFECTED && IsPlayerAlive(i))
            special++;
    }

    int common = 0;
    int ent = -1;
    while ((ent = FindEntityByClassname(ent, "infected")) != -1)
        common++;

    int chatInterval = g_cvChatInterval.IntValue;
    if (chatInterval > 0 && (g_iTick % chatInterval) == 0)
    {
        PrintToChatAll("\x04[感染者]\x01 特感: \x03%d\x01  普感: \x03%d", special, common);
    }

    return Plugin_Continue;
}
